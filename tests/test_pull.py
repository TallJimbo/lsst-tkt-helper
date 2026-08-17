# Copyright 2020-2026 Jim Bosch
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

from __future__ import annotations

import json
import os
from pathlib import Path

import git
import pytest

from tkt._workspace import Workspace
from tkt.pull import (
    _WIP_COMMIT_MESSAGE,
    Pull,
    PullError,
    _is_cherry_pick_in_progress,
    _is_rebase_in_progress,
)


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace with a main clone and agent worktree for ``pkg``."""
    human_dir = tmp_path / "pkg"
    human_dir.mkdir()
    repo = git.Repo.init(human_dir)
    repo.config_writer().set_value("user", "name", "test").release()
    repo.config_writer().set_value("user", "email", "test@test").release()
    (human_dir / "file1.txt").write_text("file1\n")
    repo.git.add("file1.txt")
    repo.git.commit("-m", "base")
    repo.git.checkout("-b", "tickets/X")
    (human_dir / "file1.txt").write_text("file1\nhuman change\n")
    repo.git.add("file1.txt")
    repo.git.commit("-m", "human work")
    agent_dir = tmp_path / ".agent" / "pkg"
    agent_dir.parent.mkdir()
    repo.git.worktree("add", "-b", "tickets/X-agent", str(agent_dir), "tickets/X")
    return Workspace(
        ticket="X",
        directory=str(tmp_path),
        metapackage_name="m",
        metapackage_tag="t",
        packages={"pkg": "tickets/X"},
        externals={},
        workspace_eups_product="x",
        tools=(),
    )


def _human(workspace):
    return git.Repo(f"{workspace.directory}/pkg")


def _agent(workspace):
    return git.Repo(f"{workspace.directory}/.agent/pkg")


def _agent_dir(workspace):
    return Path(f"{workspace.directory}/.agent/pkg")


def _make_agent_commit(workspace, content, *, message="agent work"):
    agent = _agent(workspace)
    agent.config_writer().set_value("user", "name", "agent").release()
    agent.config_writer().set_value("user", "email", "agent@agent").release()
    (_agent_dir(workspace) / "file1.txt").write_text(content)
    agent.git.add("file1.txt")
    agent.git.commit("-m", message)


def test_fast_path(workspace):
    """H ancestor of A, clean agent -> fast-forward the human branch."""
    _make_agent_commit(workspace, "file1\nhuman change\nagent commit\n")
    human = _human(workspace)
    expected = human.rev_parse("tickets/X-agent")

    Pull.run(workspace)

    human = _human(workspace)
    assert human.head.commit == expected
    # The human branch now points at the agent branch tip.
    assert human.rev_parse("tickets/X") == expected


def test_uncommitted_transfer_as_staged_restore(workspace):
    """Uncommitted agent work lands as unstaged changes; branch unchanged."""
    agent = _agent(workspace)
    agent.config_writer().set_value("user", "name", "agent").release()
    agent.config_writer().set_value("user", "email", "agent@agent").release()
    pre = _human(workspace).head.commit
    agent_pre = _agent(workspace).head.commit
    (_agent_dir(workspace) / "file1.txt").write_text("file1\nhuman change\nagent edit\n")
    (_agent_dir(workspace) / "untracked.txt").write_text("untracked\n")

    Pull.run(workspace)

    human = _human(workspace)
    # The branch itself does not move; the result is ordinary unstaged work.
    assert human.head.commit == pre
    assert (Path(f"{workspace.directory}/pkg/file1.txt")).read_text() == ("file1\nhuman change\nagent edit\n")
    assert (Path(f"{workspace.directory}/pkg/untracked.txt")).read_text() == "untracked\n"
    assert human.is_dirty(untracked_files=True)
    # The untracked file is not staged.
    assert "untracked.txt" not in human.git.diff("--cached", "--name-only")
    # The temporary WIP commit is removed from the agent branch: it is back at
    # its pre-WIP tip and the work is uncommitted/untracked there again.
    agent = _agent(workspace)
    assert agent.head.commit == agent_pre
    assert _WIP_COMMIT_MESSAGE not in agent.git.log("--format=%s")
    assert agent.is_dirty(untracked_files=True)


def test_uncommitted_transfer_skips_failing_pre_commit_hook(workspace):
    """A failing pre-commit hook must not block the uncommitted transfer."""
    human = _human(workspace)
    hooks = Path(human.git_dir) / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "pre-commit").write_text("#!/bin/sh\nexit 1\n")
    os.chmod(hooks / "pre-commit", 0o755)
    agent = _agent(workspace)
    agent.config_writer().set_value("user", "name", "agent").release()
    agent.config_writer().set_value("user", "email", "agent@agent").release()
    (_agent_dir(workspace) / "file1.txt").write_text("file1\nhuman change\nagent edit\n")

    Pull.run(workspace)

    human = _human(workspace)
    assert human.is_dirty(untracked_files=True)
    assert (Path(f"{workspace.directory}/pkg/file1.txt")).read_text() == ("file1\nhuman change\nagent edit\n")


def test_diverged_interactive_rebase(workspace, monkeypatch):
    """Diverged branches -> snapshot + non-interactive rebase (drop none)."""
    # Human gains a commit the agent lacks, then the agent gains its own.
    human = _human(workspace)
    (Path(f"{workspace.directory}/pkg/extra.txt")).write_text("extra\n")
    human.git.add("extra.txt")
    human.git.commit("-m", "human extra")
    _make_agent_commit(workspace, "file1\nhuman change\nagent commit\n")
    human = _human(workspace)
    pre = human.head.commit

    monkeypatch.setenv("GIT_SEQUENCE_EDITOR", "true")
    monkeypatch.setenv("GIT_EDITOR", "true")
    Pull.run(workspace)

    human = _human(workspace)
    # Replay landed the agent commit on top of the human commit.
    assert human.head.commit != pre
    assert "extra.txt" in human.git.ls_files()
    # Snapshot branch was cleaned up on success, no ledger left behind.
    assert "tickets/X-sync" not in human.heads
    assert not os.path.exists(f"{workspace.directory}/.pull-sandbox.json")


def test_mixed_requires_side_flag(workspace):
    """A mixed package errors without --skip/--only-uncommitted."""
    _make_agent_commit(workspace, "file1\nhuman change\nagent commit\n")
    (_agent_dir(workspace) / "file1.txt").write_text("file1\nhuman change\nagent commit\nwip\n")

    with pytest.raises(PullError, match="--skip-uncommitted"):
        Pull.run(workspace)


def test_skip_then_only_uncommitted(workspace):
    """Two syncs split a mixed package into committed then uncommitted."""
    human = _human(workspace)
    (Path(f"{workspace.directory}/pkg/extra.txt")).write_text("extra\n")
    human.git.add("extra.txt")
    human.git.commit("-m", "human extra")
    _make_agent_commit(workspace, "file1\nhuman change\nagent commit\n")
    (_agent_dir(workspace) / "file1.txt").write_text("file1\nhuman change\nagent commit\nwip\n")
    pre = _human(workspace).head.commit

    # Sync 1: committed only.
    Pull.run(workspace, skip_uncommitted=True)
    human = _human(workspace)
    assert human.head.commit != pre
    # The dirty agent worktree was deferred, so the file is untouched there.
    assert "wip" in (_agent_dir(workspace) / "file1.txt").read_text()
    assert os.path.exists(f"{workspace.directory}/.pull-sandbox.json")

    # Sync 2: uncommitted only, committed side now reconciled (no guard).
    Pull.run(workspace, only_uncommitted=True, confirm=lambda msg: True)
    human = _human(workspace)
    assert "wip" in (Path(f"{workspace.directory}/pkg/file1.txt")).read_text()


def test_only_uncommitted_ordering_guard_declined(workspace):
    """Declining the ordering guard cancels the uncommitted transfer."""
    _make_agent_commit(workspace, "file1\nhuman change\nagent commit\n")
    (_agent_dir(workspace) / "file1.txt").write_text("file1\nhuman change\nagent commit\nwip\n")
    pre = _human(workspace).head.commit

    Pull.run(workspace, only_uncommitted=True, confirm=lambda msg: False)

    human = _human(workspace)
    assert human.head.commit == pre
    assert not human.is_dirty(untracked_files=True)


def test_both_dirty_aborts_whole_run(workspace):
    """Both human and agent dirty -> global preflight abort."""
    (Path(f"{workspace.directory}/pkg/file1.txt")).write_text("file1\nhuman change\ndirty\n")
    (_agent_dir(workspace) / "file1.txt").write_text("file1\nhuman change\nagent wip\n")

    with pytest.raises(PullError, match="both a dirty human worktree"):
        Pull.run(workspace)


def test_finish_abort_empty_ledger_errors(workspace):
    """--finish/--abort with no sync state report nothing to do."""
    with pytest.raises(PullError, match="nothing to do"):
        Pull.finish(workspace)
    with pytest.raises(PullError, match="nothing to do"):
        Pull.abort(workspace)


def test_abort_restores_snapshot_and_stash(workspace):
    """--abort resets to the snapshot, drops it, and restores the stash."""
    human = _human(workspace)
    human_dir = Path(f"{workspace.directory}/pkg")
    human_head = human.head.commit
    (human_dir / "file1.txt").write_text("file1\nhuman change\ndirty human\n")
    human.git.stash("push", "-u", "-m", "tkt pull-sandbox: pkg")
    stash_ref = human.git.rev_parse("stash@{0}").strip()
    human.git.branch("tickets/X-sync", human_head)

    # Simulate the state _commit_transfer leaves behind (reset to agent tip).
    human.git.reset("--hard", "tickets/X")

    ledger_path = f"{workspace.directory}/.pull-sandbox.json"
    with open(ledger_path, "w") as f:
        json.dump(
            {
                "pkg": {
                    "snapshot_branch": "tickets/X-sync",
                    "human_stash_ref": stash_ref,
                    "sync_kind": "diverged",
                }
            },
            f,
        )

    Pull.abort(workspace)

    human = _human(workspace)
    assert human.head.commit == human_head
    assert "tickets/X-sync" not in human.heads
    assert not os.path.exists(ledger_path)
    assert "dirty human" in (human_dir / "file1.txt").read_text()


def test_dry_run_makes_no_changes(workspace):
    """--dry-run reports actions without mutating any worktree."""
    _make_agent_commit(workspace, "file1\nhuman change\nagent commit\n")
    pre = _human(workspace).head.commit

    Pull.run(workspace, dry_run=True)

    human = _human(workspace)
    assert human.head.commit == pre
    assert not os.path.exists(f"{workspace.directory}/.pull-sandbox.json")


def _make_divergent_conflict(workspace) -> None:
    """Have human commit and agent commit modify the same line (conflict)."""
    human = _human(workspace)
    human_dir = Path(f"{workspace.directory}/pkg")
    (human_dir / "file1.txt").write_text("file1\nhuman change\nhuman extra\n")
    human.git.add("file1.txt")
    human.git.commit("-m", "human extra")
    agent = _agent(workspace)
    agent.config_writer().set_value("user", "name", "agent").release()
    agent.config_writer().set_value("user", "email", "agent@agent").release()
    (_agent_dir(workspace) / "file1.txt").write_text("file1\nhuman change\nagent extra\n")
    agent.git.add("file1.txt")
    agent.git.commit("-m", "agent extra")


def test_diverged_conflict_is_left_in_progress_and_recorded(workspace, monkeypatch):
    """Conflicted rebase stays in progress and is recorded for finish/abort."""
    _make_divergent_conflict(workspace)
    monkeypatch.setenv("GIT_SEQUENCE_EDITOR", "true")
    monkeypatch.setenv("GIT_EDITOR", "true")

    Pull.run(workspace)

    human = _human(workspace)
    assert human.git.status("--porcelain", "--branch")  # in-progress rebase state
    assert _is_rebase_in_progress(human)
    # The ledger records the in-progress divergent sync.
    with open(f"{workspace.directory}/.pull-sandbox.json") as f:
        entry = json.load(f)["pkg"]
    assert entry["snapshot_branch"] == "tickets/X-sync"
    assert entry["sync_kind"] == "diverged"

    # --abort restores the human branch and cleans up.
    original_h = human.rev_parse("tickets/X-sync")
    Pull.abort(workspace)
    human = _human(workspace)
    assert human.head.commit == original_h
    assert "tickets/X-sync" not in human.heads
    assert not os.path.exists(f"{workspace.directory}/.pull-sandbox.json")
    assert not _is_rebase_in_progress(human)


def test_uncommitted_conflict_recorded_and_abort_restores(workspace):
    """Conflicted uncommitted cherry-pick recorded; abort restores state."""
    human = _human(workspace)
    human_dir = Path(f"{workspace.directory}/pkg")
    # Human evolves a line...
    (human_dir / "file1.txt").write_text("file1\nhuman change\nhuman extra\n")
    human.git.add("file1.txt")
    human.git.commit("-m", "human extra")
    # ...while the agent edits the same line uncommitted (no commits ahead).
    my_human = _human(workspace)
    pre = my_human.head.commit
    (_agent_dir(workspace) / "file1.txt").write_text("file1\nhuman change\nagent wip\n")

    Pull.run(workspace)

    human = _human(workspace)
    assert _is_cherry_pick_in_progress(human)
    with open(f"{workspace.directory}/.pull-sandbox.json") as f:
        entry = json.load(f)["pkg"]
    assert entry["pending_uncommitted_finalize"] == pre.hexsha
    assert entry["sync_kind"] == "uncommitted"

    # --abort returns the human branch and the agent's work to unstaged state.
    Pull.abort(workspace)
    human = _human(workspace)
    assert human.head.commit == pre
    assert not human.is_dirty(untracked_files=True)
    agent = _agent(workspace)
    assert agent.is_dirty(untracked_files=True)
    assert (Path(_agent_dir(workspace) / "file1.txt")).read_text() == ("file1\nhuman change\nagent wip\n")


def test_uncommitted_conflict_resolved_then_finish(workspace, monkeypatch):
    """Resolving a conflicted cherry-pick, then --finish, lands it unstaged."""
    monkeypatch.setenv("GIT_EDITOR", "true")
    human = _human(workspace)
    human_dir = Path(f"{workspace.directory}/pkg")
    (human_dir / "file1.txt").write_text("file1\nhuman change\nhuman extra\n")
    human.git.add("file1.txt")
    human.git.commit("-m", "human extra")
    my_human = _human(workspace)
    pre = my_human.head.commit
    agent_pre = _agent(workspace).head.commit
    (_agent_dir(workspace) / "file1.txt").write_text("file1\nhuman change\nagent wip\n")

    Pull.run(workspace)

    human = _human(workspace)
    assert _is_cherry_pick_in_progress(human)
    # Resolve the conflict and continue the cherry-pick (commits onto human).
    (human_dir / "file1.txt").write_text("file1\nhuman change\nexpected merged value\n")
    human.git.add("file1.txt")
    human.git.cherry_pick("--continue")

    Pull.finish(workspace)

    human = _human(workspace)
    # Branch restored to the pre-transfer tip; the merged work is unstaged.
    assert human.head.commit == pre
    assert not _is_cherry_pick_in_progress(human)
    assert human.is_dirty(untracked_files=True)
    assert (human_dir / "file1.txt").read_text() == "file1\nhuman change\nexpected merged value\n"
    assert not os.path.exists(f"{workspace.directory}/.pull-sandbox.json")
    # The temporary WIP commit is removed from the agent branch after finish.
    agent = _agent(workspace)
    assert agent.head.commit == agent_pre
    assert _WIP_COMMIT_MESSAGE not in agent.git.log("--format=%s")


def test_finish_abandoning_pending_cherry_pick_keeps_wip(workspace):
    """--finish while the cherry-pick is in progress keeps the agent WIP."""
    human = _human(workspace)
    human_dir = Path(f"{workspace.directory}/pkg")
    (human_dir / "file1.txt").write_text("file1\nhuman change\nhuman extra\n")
    human.git.add("file1.txt")
    human.git.commit("-m", "human extra")
    my_human = _human(workspace)
    pre = my_human.head.commit
    agent_pre = _agent(workspace).head.commit
    (_agent_dir(workspace) / "file1.txt").write_text("file1\nhuman change\nagent wip\n")

    Pull.run(workspace)
    human = _human(workspace)
    assert _is_cherry_pick_in_progress(human)
    wip = _agent(workspace).head.commit

    # --finish without resolving/continuing abandons the in-progress
    # cherry-pick.
    Pull.finish(workspace)

    human = _human(workspace)
    assert human.head.commit == pre
    assert not _is_cherry_pick_in_progress(human)
    assert not os.path.exists(f"{workspace.directory}/.pull-sandbox.json")
    # The agent's WIP commit is the only surviving copy of the work, so it is
    # kept (the agent branch is not reset back to its pre-WIP tip).
    agent = _agent(workspace)
    assert agent.head.commit == wip
    assert agent.head.commit != agent_pre
    assert _WIP_COMMIT_MESSAGE in agent.git.log("--format=%s")
    assert (Path(_agent_dir(workspace) / "file1.txt")).read_text() == ("file1\nhuman change\nagent wip\n")


def test_finish_refuses_while_diverged_rebase_in_progress(workspace, monkeypatch):
    """--finish refuses to dismantle the snapshot mid-rebase."""
    _make_divergent_conflict(workspace)
    monkeypatch.setenv("GIT_SEQUENCE_EDITOR", "true")
    monkeypatch.setenv("GIT_EDITOR", "true")
    Pull.run(workspace)
    assert _is_rebase_in_progress(_human(workspace))

    with pytest.raises(PullError, match="rebase is still in progress"):
        Pull.finish(workspace)
    # The snapshot and ledger survive so the user can continue or abort.
    human = _human(workspace)
    assert "tickets/X-sync" in human.heads
    assert os.path.exists(f"{workspace.directory}/.pull-sandbox.json")
