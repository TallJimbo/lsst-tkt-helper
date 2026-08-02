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

from pathlib import Path

import git
import pytest

from tkt._workspace import Workspace
from tkt.sandbox import Sandbox


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace with a main clone and an agent worktree for ``pkg``.

    The main repo's package ``pkg`` lives at ``<dir>/pkg`` on branch
    ``tickets/X``, with an agent worktree at ``<dir>/.agent/pkg`` on branch
    ``tickets/X-agent``.
    """
    repo_dir = tmp_path / "pkg"
    repo_dir.mkdir()
    repo = git.Repo.init(repo_dir)
    repo.config_writer().set_value("user", "name", "test").release()
    repo.config_writer().set_value("user", "email", "test@test").release()
    (repo_dir / "file1.txt").write_text("file1\n")
    repo.git.add("file1.txt")
    repo.git.commit("-m", "base")
    repo.git.checkout("-b", "tickets/X")
    (repo_dir / "file1.txt").write_text("file1\nhuman change\n")
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
        tools=("sandbox",),
    )


@pytest.fixture
def sandbox():
    """Return a `Sandbox` instance with an empty command."""
    return Sandbox(command=[])


def _agent(workspace):
    return git.Repo(str(workspace.directory) + "/.agent/pkg")


def test_reset_saves_uncommitted_work_and_resets(workspace, sandbox):
    """Verify reset stashes work, backs up commits, and restores the branch."""
    agent_dir = Path(f"{workspace.directory}/.agent/pkg")
    # The agent makes a commit not on the human branch, then leaves uncommitted
    # work plus untracked and ignored files behind.
    agent = git.Repo(agent_dir)
    agent.config_writer().set_value("user", "name", "agent").release()
    agent.config_writer().set_value("user", "email", "agent@agent").release()
    (agent_dir / "file1.txt").write_text("file1\nhuman change\nagent commit\n")
    agent.git.add("file1.txt")
    agent.git.commit("-m", "agent commit")
    (agent_dir / "file1.txt").write_text("file1\nhuman change\nagent commit\nuncommitted\n")
    (agent_dir / "untracked.txt").write_text("untracked\n")
    (agent_dir / "ignored.log").write_text("ignored\n")
    (agent_dir / ".gitignore").write_text("*.log\n")
    agent.git.add(".gitignore")
    agent.git.commit("-m", "gitignore")
    (agent_dir / "ignored2.log").write_text("ignored2\n")
    human_head = agent.rev_parse("tickets/X")

    sandbox.reset(workspace)

    agent = git.Repo(agent_dir)
    # The worktree is clean, reset to the human branch, with no leftovers.
    assert not agent.is_dirty(untracked_files=True)
    assert (agent_dir / "file1.txt").read_text() == "file1\nhuman change\n"
    assert not (agent_dir / "untracked.txt").exists()
    assert not (agent_dir / "ignored2.log").exists()
    assert agent.head.commit == human_head
    # Uncommitted work was stashed (including untracked and ignored files via
    # `--all`).
    stash = agent.git.stash("list")
    assert "tkt reset backup: pkg" in stash
    stash_files = agent.git.stash("show", "--name-only", "--include-untracked", "stash@{0}")
    assert "untracked.txt" in stash_files
    assert "ignored2.log" in stash_files
    # Unmerged commits were saved to a timestamped backup branch.
    saved = [b.name for b in agent.heads if b.name.startswith("tickets/X-agent-saved-")]
    assert saved
    assert any(s.endswith("-") is False and "T" in s for s in saved)


def test_reset_clean_worktree_makes_no_backups(workspace, sandbox):
    """Verify a clean, in-sync worktree creates no stash or backup branch."""
    agent_dir = f"{workspace.directory}/.agent/pkg"
    agent = git.Repo(agent_dir)

    sandbox.reset(workspace)

    agent = git.Repo(agent_dir)
    assert not agent.is_dirty(untracked_files=True)
    assert not agent.git.stash("list").strip()
    assert not [b.name for b in agent.heads if "saved" in b.name]


def test_reset_skips_package_without_agent_worktree(workspace, sandbox):
    """Verify a package without an agent worktree is skipped without error."""
    # Remove the agent worktree so the package has none to reset.
    repo = git.Repo(f"{workspace.directory}/pkg")
    repo.git.worktree("remove", f"{workspace.directory}/.agent/pkg")
    sandbox.reset(workspace)
