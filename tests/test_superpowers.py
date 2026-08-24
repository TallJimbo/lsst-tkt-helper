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

import pytest

from tkt._cli import _classify_tools, update
from tkt._workspace import Workspace
from tkt.superpowers import Superpowers


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


def test_write_creates_namespace(tmp_path):
    """``write`` creates the per-ticket specs and plans directories."""
    sp = Superpowers(path=str(tmp_path))
    sp.write("DM-1", str(tmp_path / "ws"), [], workspace=object(), environment=object())
    assert (tmp_path / "DM-1" / "specs").is_dir()
    assert (tmp_path / "DM-1" / "plans").is_dir()


def test_eups_env_lines():
    """``eups_env_lines`` yields a single ``SUPERPOWERS_DIR`` envSet line."""
    sp = Superpowers(path="/shared")
    assert sp.eups_env_lines("DM-1") == ("envSet(SUPERPOWERS_DIR, /shared/DM-1)",)


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


def test_write_eups_table_superpowers(tmp_path):
    """Emits a ``SUPERPOWERS_DIR`` line when the tool is configured."""
    ws = _workspace(tmp_path, ("superpowers",))
    ws._write_eups_table(_FakeEnv({"superpowers": Superpowers(path="/shared")}))
    text = (tmp_path / "ups" / "x.table").read_text()
    assert "envSet(SUPERPOWERS_DIR, /shared/DM-1)" in text


def test_write_eups_table_no_superpowers(tmp_path):
    """``_write_eups_table`` omits ``SUPERPOWERS_DIR`` when not configured."""
    ws = _workspace(tmp_path, ())
    ws._write_eups_table(_FakeEnv({}))
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
    assert openspec.removed == [ws.directory]
    assert "openspec" in ws.removed_tools
    assert ws.update_calls and ws.update_calls[-1]["dry_run"] is False


def test_update_dry_run_reports_without_removing(tmp_path, monkeypatch):
    """``update`` dry-run reports non-default tools but removes nothing."""
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
