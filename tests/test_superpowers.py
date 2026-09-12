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

import shutil
from pathlib import Path

import git
import pytest

from tkt._cli import _classify_tools, update
from tkt._workspace import Workspace
from tkt.superpowers import Superpowers


def _init_repo(path):
    """Initialize a git repo at ``path`` with one commit on branch ``main``."""
    path.mkdir(parents=True)
    repo = git.Repo.init(path)
    repo.config_writer().set_value("user", "name", "test").release()
    repo.config_writer().set_value("user", "email", "test@test").release()
    repo.git.branch("-m", "main")
    (path / "README.md").write_text("# docs\n")
    repo.git.add("README.md")
    repo.git.commit("-m", "base")
    return repo


class _DocsEnv:
    """Stand-in ``Environment`` with the ticket branch naming."""

    def get_default_branch(self, package: str, ticket: str) -> str:
        return f"tickets/{ticket}"


class _FakeEnv:
    """Stand-in ``Environment`` exposing only ``get_tool``."""

    def __init__(self, tools):
        self._tools = tools

    def get_tool(self, name):
        return self._tools.get(name)


def _workspace(tmp_path, tools):
    """Build a ``Workspace`` with the given tools."""
    return Workspace(
        ticket="DM-1",
        directory=str(tmp_path),
        metapackage_name="m",
        metapackage_tag="t",
        packages={},
        externals={},
        workspace_eups_product="x",
        tools=tools,
    )


def test_from_json_data():
    """``from_json_data`` builds a ``Superpowers`` from its ``path``."""
    tool = Superpowers.from_json_data({"path": "/shared"})
    assert isinstance(tool, Superpowers)
    assert tool.path == "/shared"


def test_from_json_data_rejects_extra():
    """``from_json_data`` rejects unexpected configuration entries."""
    with pytest.raises(ValueError):
        Superpowers.from_json_data({"path": "/shared", "x": 1})


def test_from_json_data_requires_path():
    """``from_json_data`` requires a ``path``."""
    with pytest.raises(KeyError):
        Superpowers.from_json_data({})


@pytest.fixture
def shared(tmp_path):
    """Create a shared superpowers docs repo with one commit on ``main``."""
    return _init_repo(tmp_path / "shared")


@pytest.fixture
def sp(shared):
    """Return a ``Superpowers`` tool pointed at the shared repo."""
    return Superpowers(path=str(shared.working_dir))


def _worktree_dir(tmp_path):
    return tmp_path / ".agent" / "superpowers-docs"


def _registered_worktrees(shared):
    """Return the working-tree paths registered as worktrees of ``shared``."""
    out = shared.git.worktree("list", "--porcelain")
    return [line[len("worktree ") :] for line in out.splitlines() if line.startswith("worktree ")]


def test_write_creates_worktree_on_ticket_branch(tmp_path, shared, sp):
    """``write`` creates a worktree of the shared repo on the ticket branch."""
    sp.write("DM-1", str(tmp_path), [], workspace=object(), environment=_DocsEnv())
    wt = git.Repo(_worktree_dir(tmp_path))
    assert wt.active_branch.name == "tickets/DM-1"
    assert wt.head.commit == shared.head.commit
    assert str(_worktree_dir(tmp_path)) in _registered_worktrees(shared)


def test_write_attaches_existing_ticket_branch(tmp_path, shared, sp):
    """An existing ticket branch is attached to, not recreated from main."""
    shared.create_head("tickets/DM-1")
    # main moves ahead after the ticket branch was made; the worktree must
    # track the branch, not main.
    (Path(shared.working_dir) / "later.md").write_text("later\n")
    shared.git.add("later.md")
    shared.git.commit("-m", "later main work")
    sp.write("DM-1", str(tmp_path), [], workspace=object(), environment=_DocsEnv())
    wt = git.Repo(_worktree_dir(tmp_path))
    assert wt.active_branch.name == "tickets/DM-1"
    assert wt.head.commit == shared.heads["tickets/DM-1"].commit
    assert wt.head.commit != shared.head.commit


def test_write_is_idempotent(tmp_path, sp):
    """A second ``write`` must not disturb an existing worktree."""
    sp.write("DM-1", str(tmp_path), [], workspace=object(), environment=_DocsEnv())
    wt_dir = _worktree_dir(tmp_path)
    wt = git.Repo(wt_dir)
    (wt_dir / "notes.md").write_text("agent work\n")
    sp.write("DM-1", str(tmp_path), [], workspace=object(), environment=_DocsEnv())
    assert wt.active_branch.name == "tickets/DM-1"
    assert (wt_dir / "notes.md").read_text() == "agent work\n"


def test_write_prunes_stale_registration(tmp_path, shared, sp):
    """Re-add a worktree whose directory was deleted behind git's back."""
    sp.write("DM-1", str(tmp_path), [], workspace=object(), environment=_DocsEnv())
    shutil.rmtree(_worktree_dir(tmp_path))
    sp.write("DM-1", str(tmp_path), [], workspace=object(), environment=_DocsEnv())
    assert git.Repo(_worktree_dir(tmp_path)).active_branch.name == "tickets/DM-1"


def test_write_skips_missing_shared_repo(tmp_path, sp):
    """Skip (do not crash on) a nonexistent shared repo."""
    shutil.rmtree(sp.path)
    sp.write("DM-1", str(tmp_path), [], workspace=object(), environment=_DocsEnv())
    assert not _worktree_dir(tmp_path).exists()


def test_remove_removes_worktree(tmp_path, shared, sp):
    """``remove`` deregisters and deletes the docs worktree."""
    sp.write("DM-1", str(tmp_path), [], workspace=object(), environment=_DocsEnv())
    sp.remove(str(tmp_path))
    assert not _worktree_dir(tmp_path).exists()
    assert str(_worktree_dir(tmp_path)) not in _registered_worktrees(shared)


def test_remove_without_worktree_is_noop(tmp_path, sp):
    """``remove`` on a workspace with no docs worktree does nothing."""
    sp.remove(str(tmp_path))
    assert not _worktree_dir(tmp_path).exists()


def test_eups_env_lines_empty(sp):
    """The tool contributes no EUPS environment lines (no SUPERPOWERS_DIR)."""
    assert tuple(sp.eups_env_lines("DM-1")) == ()


def test_write_eups_table_no_superpowers_dir(tmp_path):
    """Omit SUPERPOWERS_DIR from the table even when the tool is set up."""
    ws = _workspace(tmp_path, ("superpowers",))
    ws._write_eups_table(_FakeEnv({"superpowers": Superpowers(path="/shared")}))
    text = (tmp_path / "ups" / "x.table").read_text()
    assert "SUPERPOWERS_DIR" not in text


class _EnvTool:
    """Stand-in generic tool with an ``eups_env_lines`` hook."""

    def eups_env_lines(self, ticket):
        return (f"envSet(FOO_DIR, foo/{ticket})",)


def test_write_eups_table_generic_tool(tmp_path):
    """``_write_eups_table`` writes env lines from any configured tool."""
    ws = _workspace(tmp_path, ("foo",))
    ws._write_eups_table(_FakeEnv({"foo": _EnvTool()}))
    text = (tmp_path / "ups" / "x.table").read_text()
    assert "envSet(FOO_DIR, foo/DM-1)" in text


class _RecordingRemoveTool:
    """Stand-in tool recording ``remove`` calls from ``Workspace.remove``."""

    def __init__(self):
        self.removed = []

    def remove(self, directory):
        self.removed.append(directory)


def test_workspace_remove_calls_tool_hooks(tmp_path):
    """Let each configured tool clean up its artifacts on workspace removal."""
    tool = _RecordingRemoveTool()
    ws = _workspace(tmp_path, ("docs",))
    ws.remove(_FakeEnv({"docs": tool}))
    assert tool.removed == [str(tmp_path)]
    assert not tmp_path.exists()


def test_classify_tools():
    """``_classify_tools`` splits tools into missing/stale/non-default."""

    def get_tool(name):
        return {"openspec": object(), "superpowers": object(), "zed": object()}.get(name)

    missing, stale, nondefault = _classify_tools(
        ["openspec", "zed", "removed"], ["superpowers", "zed"], get_tool
    )
    assert missing == ["superpowers"]
    assert stale == ["removed"]
    assert nondefault == ["openspec"]


class _UpdateTool:
    """Stand-in tool recording the directories it has been removed from."""

    def __init__(self):
        self.removed = []

    def remove(self, directory):
        self.removed.append(directory)


class _UpdateEnv:
    """Stand-in ``Environment`` with a fixed default-tools tuple."""

    def __init__(self, tools, default_tools):
        self._tools = tools
        self.default_tools = default_tools

    def get_tool(self, name):
        return self._tools.get(name)


class _UpdateWorkspace:
    """Stand-in ``Workspace`` recording removals and update calls."""

    def __init__(self, tools, directory):
        self.tools = tools
        self.directory = directory
        self.removed_tools = []
        self.update_calls = []

    def remove_tools(self, names):
        self.removed_tools.extend(names)

    def update(self, **kwargs):
        self.update_calls.append(kwargs)


def _patch_update_bounds(monkeypatch, env, ws):
    """Point ``update`` at fake env/workspace boundaries."""
    monkeypatch.setattr("tkt._cli.Environment.from_file", lambda f: env)
    monkeypatch.setattr("tkt._cli.Workspace.from_existing", lambda **kw: ws)


def _update_fakes(tmp_path):
    """Build fake env/workspace: default ``zed``, non-default ``openspec``."""
    openspec = _UpdateTool()
    env = _UpdateEnv({"openspec": openspec}, ("zed",))
    ws = _UpdateWorkspace(("zed", "openspec"), str(tmp_path))
    return openspec, env, ws


def _call_update(**kwargs):
    """Invoke the raw ``update`` command body, bypassing the click wrapper."""
    return update.callback(**kwargs)


def test_update_migrates_nondefault(tmp_path, monkeypatch):
    """``update`` removes a non-default tool and cleans its artifacts."""
    openspec, env, ws = _update_fakes(tmp_path)
    _patch_update_bounds(monkeypatch, env, ws)
    monkeypatch.setattr("tkt._cli.click.confirm", lambda msg: True)
    _call_update(
        packages=(),
        ticket=None,
        directory=str(tmp_path),
        environment="env",
        dry_run=False,
        verbose=0,
    )
    assert openspec.removed == [str(tmp_path)]
    assert "openspec" in ws.removed_tools


def test_update_dry_run_skips_removal(tmp_path, monkeypatch):
    """``update --dry-run`` removes nothing."""
    openspec, env, ws = _update_fakes(tmp_path)
    _patch_update_bounds(monkeypatch, env, ws)
    _call_update(
        packages=(),
        ticket=None,
        directory=str(tmp_path),
        environment="env",
        dry_run=True,
        verbose=0,
    )
    assert openspec.removed == []
    assert ws.removed_tools == []
    assert ws.update_calls == [{"packages": (), "environment": env, "dry_run": True}]


def test_update_decline_keeps_tool(tmp_path, monkeypatch):
    """``update`` keeps the non-default tool when removal is declined."""
    openspec, env, ws = _update_fakes(tmp_path)
    _patch_update_bounds(monkeypatch, env, ws)
    monkeypatch.setattr("tkt._cli.click.confirm", lambda msg: False)
    _call_update(
        packages=(),
        ticket=None,
        directory=str(tmp_path),
        environment="env",
        dry_run=False,
        verbose=0,
    )
    assert openspec.removed == []
    assert "openspec" not in ws.removed_tools
    assert ws.update_calls and ws.update_calls[-1]["tools"] == []
