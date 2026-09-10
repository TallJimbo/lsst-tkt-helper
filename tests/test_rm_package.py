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
import pathlib
import shutil

import click
import git
import pytest

from tkt._cli import rm_sh
from tkt._workspace import Workspace


class _FakeEnv:
    """Stand-in ``Environment`` with no configured tools."""

    def get_tool(self, name):
        return None


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """Workspace with one clean, fully pushed clone and one external.

    The clone ``pkg`` lives at ``<dir>/pkg`` on branch ``tickets/X``
    (pushed to a local origin) with an agent worktree at ``<dir>/.agent/pkg``;
    the external ``ext`` lives at ``<dir>/ext`` and is not cloned from git.
    """
    for var in ("SETUP_PKG", "PKG_DIR", "SETUP_EXT", "EXT_DIR"):
        monkeypatch.delenv(var, raising=False)
    seed = tmp_path / "seed"
    seed.mkdir()
    seed_repo = git.Repo.init(seed)
    seed_repo.config_writer().set_value("user", "name", "test").release()
    seed_repo.config_writer().set_value("user", "email", "test@test").release()
    (seed / "file1.txt").write_text("file1\n")
    seed_repo.git.add("file1.txt")
    seed_repo.git.commit("-m", "base")
    origin = tmp_path / "origin.git"
    git.Repo.init(str(origin), bare=True)
    seed_repo.git.push(str(origin), "--all")
    pkg = tmp_path / "pkg"
    repo = git.Repo.clone_from(str(origin), str(pkg))
    repo.git.checkout("-b", "tickets/X")
    (pkg / "file1.txt").write_text("file1\nhuman work\n")
    repo.git.add("file1.txt")
    repo.git.commit("-m", "ticket work")
    repo.git.push("origin", "tickets/X")
    (pkg / "ups").mkdir()
    agent_dir = tmp_path / ".agent" / "pkg"
    agent_dir.parent.mkdir()
    repo.git.worktree("add", "-b", "tickets/X-agent", str(agent_dir), "tickets/X")
    (tmp_path / "ext").mkdir()
    workspace = Workspace(
        ticket="X",
        directory=str(tmp_path),
        metapackage_name="m",
        metapackage_tag="t",
        packages={"pkg": "tickets/X"},
        externals={"ext": str(tmp_path / "ext")},
        workspace_eups_product="x",
        tools=(),
    )
    workspace._write_description()
    workspace._write_eups_table(_FakeEnv())
    return workspace


def _table_text(ws: Workspace) -> str:
    return open(os.path.join(ws.directory, "ups", "x.table")).read()


def _decliner(asked: list[str]):
    """Build a confirm callable that records the message and declines."""

    def confirm(message: str) -> bool:
        asked.append(message)
        return False

    return confirm


def test_table_setup_before_removal(ws):
    """Sanity check that the fixture table references both packages."""
    text = _table_text(ws)
    assert "setupRequired(pkg -j -r ${PRODUCT_DIR}/pkg)" in text
    assert f"setupRequired(ext -j -r {ws.directory}/ext)" in text


def test_removes_clone(ws):
    """Removing a clone deletes it, its worktree, and all references to it."""
    env = _FakeEnv()
    assert ws.remove_packages(["pkg"], environment=env) == ["pkg"]
    assert not os.path.exists(os.path.join(ws.directory, "pkg"))
    assert not os.path.exists(os.path.join(ws.directory, ".agent", "pkg"))
    assert "pkg" not in ws.packages
    data = json.load(open(os.path.join(ws.directory, "tkt.json")))
    assert "pkg" not in data["packages"]
    text = _table_text(ws)
    assert "setupRequired(pkg" not in text
    assert "setupRequired(ext" in text  # the external is untouched


def test_removes_external(ws):
    """Externals lose only their EUPS table line; their directory stays."""
    assert ws.remove_packages(["ext"], environment=_FakeEnv()) == ["ext"]
    assert "ext" not in ws.externals
    assert os.path.isdir(os.path.join(ws.directory, "ext"))
    assert "setupRequired(ext" not in _table_text(ws)


def test_unknown_package_raises(ws):
    """Removing a package that is in neither dict is an error."""
    with pytest.raises(KeyError):
        ws.remove_packages(["nosuch"], environment=_FakeEnv())


def test_dirty_clone_requires_confirmation(ws):
    """A dirty clone is removed only when confirm() says yes."""
    env = _FakeEnv()
    (pathlib.Path(ws.directory) / "pkg" / "untracked.txt").write_text("oops\n")
    asked: list[str] = []
    assert ws.remove_packages(["pkg"], environment=env, confirm=_decliner(asked)) == []
    assert any("uncommitted changes" in m for m in asked)
    assert "pkg" in ws.packages
    assert ws.remove_packages(["pkg"], environment=env, confirm=lambda m: True) == ["pkg"]


def test_unpushed_commit_requires_confirmation(ws):
    """A clone with commits not on any remote triggers confirmation."""
    pkg = pathlib.Path(ws.directory) / "pkg"
    repo = git.Repo(str(pkg))
    (pkg / "file2.txt").write_text("new\n")
    repo.git.add("file2.txt")
    repo.git.commit("-m", "unpushed work")
    asked: list[str] = []
    assert ws.remove_packages(["pkg"], environment=_FakeEnv(), confirm=_decliner(asked)) == []
    assert any("not on any remote" in m for m in asked)
    assert os.path.isdir(pkg)


def test_declined_without_confirm_callable(ws):
    """Without a confirm callable, packages with unsaved work are skipped."""
    pkg = pathlib.Path(ws.directory) / "pkg"
    (pkg / "dirty.txt").write_text("dirty\n")
    assert ws.remove_packages(["pkg"], environment=_FakeEnv()) == []
    assert os.path.isdir(pkg)


def test_force_skips_dirty_check(ws):
    """--force removes a dirty clone without asking."""
    pkg = pathlib.Path(ws.directory) / "pkg"
    (pkg / "dirty.txt").write_text("dirty\n")
    assert ws.remove_packages(["pkg"], environment=_FakeEnv(), force=True) == ["pkg"]
    assert not os.path.exists(pkg)


def test_non_git_repo_dir_warns(ws):
    """A package directory that is not a git clone requires confirmation."""
    shutil.rmtree(ws.directory + "/pkg")
    (pathlib.Path(ws.directory) / "pkg").mkdir()
    asked: list[str] = []
    assert ws.remove_packages(["pkg"], environment=_FakeEnv(), confirm=_decliner(asked)) == []
    assert any("not a git repository" in m for m in asked)
    assert ws.remove_packages(["pkg"], environment=_FakeEnv(), force=True) == ["pkg"]
    assert not os.path.exists(os.path.join(ws.directory, "pkg"))


def test_setup_in_place_refuses(ws, monkeypatch):
    """A clone that is EUPS-setup from this workspace is not deleted."""
    pkg_dir = os.path.join(ws.directory, "pkg")
    monkeypatch.setenv("SETUP_PKG", f"pkg LOCAL:{pkg_dir} -f Linux64 -Z (none)")
    with pytest.raises(RuntimeError, match="unsetup -j pkg"):
        ws.remove_packages(["pkg"], environment=_FakeEnv())
    assert os.path.isdir(pkg_dir)
    # --force overrides the setup check.
    assert ws.remove_packages(["pkg"], environment=_FakeEnv(), force=True) == ["pkg"]


def test_setup_via_dir_var_refuses(ws, monkeypatch):
    """The ``<PKG>_DIR`` variable alone is enough to detect setup in place."""
    monkeypatch.setenv("PKG_DIR", os.path.join(ws.directory, "pkg"))
    with pytest.raises(RuntimeError, match="still EUPS-setup in place"):
        ws.remove_packages(["pkg"], environment=_FakeEnv())


def test_setup_from_elsewhere_allows_removal(ws, monkeypatch):
    """A same-named product set up from another directory does not block."""
    monkeypatch.setenv("SETUP_PKG", "pkg LOCAL:/opt/lsst/sw/pkg -f Linux64 -Z (none)")
    assert ws.remove_packages(["pkg"], environment=_FakeEnv()) == ["pkg"]


def test_agent_worktree_dirty_warns(ws):
    """A dirty agent worktree warns and requires confirmation."""
    agent = pathlib.Path(ws.directory) / ".agent" / "pkg"
    (agent / "agent_dirty.txt").write_text("agent work\n")
    asked: list[str] = []
    assert ws.remove_packages(["pkg"], environment=_FakeEnv(), confirm=_decliner(asked)) == []
    assert any("agent worktree" in m for m in asked)
    assert "pkg" in ws.packages


def test_dry_run_changes_nothing(ws):
    """A dry run leaves the workspace completely intact."""
    assert ws.remove_packages(["pkg", "ext"], environment=_FakeEnv(), dry_run=True) == ["pkg", "ext"]
    assert os.path.isdir(os.path.join(ws.directory, "pkg"))
    assert os.path.isdir(os.path.join(ws.directory, ".agent", "pkg"))
    assert "pkg" in ws.packages
    assert "ext" in ws.externals
    assert "setupRequired(pkg" in _table_text(ws)


def test_rm_sh_emits_unsetup_and_command(ws, capsys, monkeypatch):
    """``_rm-sh`` prints unsetup -j for setup packages, then rm-package."""
    monkeypatch.setenv("SETUP_PKG", f"pkg LOCAL:{os.path.join(ws.directory, 'pkg')} -f Linux64 -Z (none)")
    rm_sh.callback(packages=("pkg", "ext"), directory=ws.directory, verbose=0)
    out = capsys.readouterr().out.strip()
    assert out == "unsetup -j pkg; tkt rm-package pkg ext"


def test_rm_sh_without_setup(ws, capsys):
    """With nothing setup, ``_rm-sh`` only prints the rm-package command."""
    rm_sh.callback(packages=("pkg",), directory=ws.directory, verbose=0)
    assert capsys.readouterr().out.strip() == "tkt rm-package pkg"


def test_rm_sh_unknown_package(ws):
    """``_rm-sh`` rejects names that are not in the workspace."""
    with pytest.raises(click.ClickException):
        rm_sh.callback(packages=("nosuch",), directory=ws.directory, verbose=0)
