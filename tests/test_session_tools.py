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

"""Tool-surface tests: registration, guards, containment, delegation."""

from __future__ import annotations

import asyncio
import inspect
from functools import partial
from typing import Any

import pytest

from tkt import _session_tools
from tkt._inbox_proxy import _forward
from tkt._session_tools import _resolve_patch_source, build_session_tools, safe_subpath

pytest.importorskip("mcp")
from mcp.server.mcpserver import MCPServer


def _tools(mcp: MCPServer) -> set[str]:
    return {t.name for t in asyncio.run(mcp.list_tools())}


def _call(tool: Any, **kwargs: Any) -> Any:
    """Invoke a registered tool callable, whether the SDK wrapped it or not."""
    out = tool.fn(**kwargs)
    if inspect.iscoroutine(out):
        out = asyncio.run(out)
    return out


@pytest.fixture(autouse=True)
def _no_inbox_upstream(monkeypatch):
    """Keep the Inbox re-publish out of this module: a real ``plannotator`` on
    PATH would otherwise spawn one listing per ``build_session_tools`` call.
    The Inbox surface itself is covered by ``tests/test_inbox_proxy.py``.
    """
    monkeypatch.setattr(_session_tools, "register_inbox_tools", lambda *a, **k: 0)


def test_session_tools_registered() -> None:
    """The builder registers exactly the session-lifecycle tools."""
    mcp = MCPServer("t")
    build_session_tools(mcp, workspace="/tmp/ws")
    names = _tools(mcp)
    assert names == {"brainstorm_server", "plannotator_open"}
    # The Inbox action map is gone: inbox tools are re-published upstream's
    # own names by _inbox_proxy, never as a single plannotator_inbox tool.
    assert "plannotator_inbox" not in names


def test_no_host_danger_tools() -> None:
    """Guard: no host execution tools on the session surface."""
    mcp = MCPServer("t")
    build_session_tools(mcp, workspace="/tmp/ws")
    forbidden = {"bash", "read", "write", "edit", "glob", "grep", "ls", "todo_write"}
    assert not forbidden & _tools(mcp)


def test_safe_subpath(tmp_path) -> None:
    """safe_subpath resolves inside the workspace and refuses escapes."""
    (tmp_path / "ok.md").write_text("x")
    assert safe_subpath(str(tmp_path), "ok.md") == str(tmp_path / "ok.md")
    with pytest.raises(ValueError, match="outside workspace"):
        safe_subpath(str(tmp_path), "../evil.md")
    with pytest.raises(ValueError, match="outside workspace"):
        safe_subpath(str(tmp_path), "/etc/passwd")  # absolute escape
    (tmp_path / "link.md").symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="outside workspace"):
        safe_subpath(str(tmp_path), "link.md")  # symlink resolution escapes too


def test_plannotator_tool_delegates_and_contains(monkeypatch) -> None:
    """The plannotator_open tool delegates and refuses escapes."""
    from tkt import _session_tools

    calls: list[tuple] = []

    class FakeMgr:
        def open(self, workspace, target, **kw):
            calls.append((workspace, target, kw))
            return {
                "type": "open",
                "handle": "pn-1",
                "url": "http://localhost:9/x",
                "result_path": "/state/pn-1.out",
                "err_path": "/state/pn-1.err",
                "reused": False,
                "source": "spawned",
            }

    monkeypatch.setattr(_session_tools, "PlannotatorManager", lambda: FakeMgr())
    mcp = MCPServer("t")
    build_session_tools(mcp, workspace="/ws")
    # The installed SDK's list_tools() returns schema-only mcp.types.Tool
    # objects; the registered callable lives on the tool manager's Tool.
    tool = mcp._tool_manager.get_tool("plannotator_open")
    out = _call(tool, target="../escape.md")
    assert "outside workspace" in out
    out = _call(tool, target="spec.md")
    assert "pn-1" in out and calls[0][0] == "/ws"


def test_plannotator_open_accepts_plan_dir_target(monkeypatch) -> None:
    """Plan-dir files are accepted open targets (and still contained)."""
    from tkt import _session_tools

    calls: list[tuple] = []

    class FakeMgr:
        def open(self, workspace, target, **kw):
            calls.append((workspace, target, kw))
            return {
                "type": "open",
                "handle": "pn-1",
                "url": "http://localhost:9/x",
                "result_path": "/state/pn-1.out",
                "err_path": "/state/pn-1.err",
                "reused": False,
                "source": "spawned",
            }

    monkeypatch.setattr(_session_tools, "PlannotatorManager", lambda: FakeMgr())
    monkeypatch.setenv("HOME", "/host-home")
    mcp = MCPServer("t")
    build_session_tools(mcp, workspace="/ws")
    tool = mcp._tool_manager.get_tool("plannotator_open")
    out = _call(tool, target="/host-home/.opencode/plan/plan.md")
    assert "pn-1" in out
    assert calls[0][1] == "/host-home/.opencode/plan/plan.md"
    assert calls[0][2]["extra_roots"] == ["/host-home/.opencode/plan"]
    # Escaping the plan dir is refused like any other escape.
    out = _call(tool, target="/host-home/.opencode/plan/../evil.md")
    assert "failed" in out
    assert len(calls) == 1  # the refused target never reached the manager


def test_plannotator_open_tells_the_agent_how_to_pull(monkeypatch) -> None:
    """The result text is the pull contract: read the file, do not poll."""

    class FakeMgr:
        def open(self, workspace, target, **kw):
            return {
                "type": "open",
                "handle": "pna-plan-md-1",
                "url": "http://localhost:9/x",
                "result_path": "/state/pna-plan-md-1.out",
                "err_path": "/state/pna-plan-md-1.err",
                "reused": False,
                "source": "spawned",
            }

    from tkt import _session_tools

    monkeypatch.setattr(_session_tools, "PlannotatorManager", lambda: FakeMgr())
    mcp = MCPServer("t")
    build_session_tools(mcp, workspace="/ws")
    out = _call(mcp._tool_manager.get_tool("plannotator_open"), target="spec.md")
    assert "http://localhost:9/x" in out and "/state/pna-plan-md-1.out" in out
    assert "still open" in out and "ended without a decision" in out and "does not parse" in out
    assert "without feedback" not in out  # an unsubmitted live session is empty too


def test_plannotator_open_captured_decision_is_read_now(monkeypatch) -> None:
    """A reuse carrying a real decision is acted on: read the file now, not
    "hand over the URL and end your turn" for a finished session.
    """

    class FakeMgr:
        def open(self, workspace, target, **kw):
            return {
                "type": "open",
                "handle": "pna-plan-md-1",
                "url": "http://localhost:9/x",
                "result_path": "/state/pna-plan-md-1.out",
                "err_path": "/state/pna-plan-md-1.err",
                "reused": True,
                "source": "spawned",
                "decision_captured": True,
            }

    from tkt import _session_tools

    monkeypatch.setattr(_session_tools, "PlannotatorManager", lambda: FakeMgr())
    mcp = MCPServer("t")
    build_session_tools(mcp, workspace="/ws")
    out = _call(mcp._tool_manager.get_tool("plannotator_open"), target="spec.md")
    assert "read `/state/pna-plan-md-1.out` now" in out
    assert "verbatim" not in out  # the page is gone; there is nothing to hand over


def test_plannotator_open_fresh_session_explains_the_previous_one(monkeypatch) -> None:
    """A spawn after an ended attempt says so, instead of a second session
    on the same target looking mysterious.
    """

    class FakeMgr:
        def open(self, workspace, target, **kw):
            return {
                "type": "open",
                "handle": "pna-plan-md-2",
                "url": "http://localhost:9/y",
                "result_path": "/state/pna-plan-md-2.out",
                "err_path": "/state/pna-plan-md-2.err",
                "reused": False,
                "source": "spawned",
                "previous_ended_without_decision": True,
            }

    from tkt import _session_tools

    monkeypatch.setattr(_session_tools, "PlannotatorManager", lambda: FakeMgr())
    mcp = MCPServer("t")
    build_session_tools(mcp, workspace="/ws")
    out = _call(mcp._tool_manager.get_tool("plannotator_open"), target="spec.md")
    assert "previous session on this target ended without a decision" in out
    assert "new session" in out
    assert "/state/pna-plan-md-2.out" in out  # the normal read contract still rides along


def test_plannotator_open_ended_session_says_so(monkeypatch) -> None:
    """The ended report replaces the read-the-file contract for the
    agent.
    """

    class FakeMgr:
        def open(self, workspace, target, **kw):
            return {
                "type": "open",
                "handle": "pna-plan-md-1",
                "url": None,
                "result_path": "/state/pna-plan-md-1.out",
                "err_path": "/state/pna-plan-md-1.err",
                "reused": False,
                "source": "spawned",
                "ended_without_decision": True,
            }

    from tkt import _session_tools

    monkeypatch.setattr(_session_tools, "PlannotatorManager", lambda: FakeMgr())
    mcp = MCPServer("t")
    build_session_tools(mcp, workspace="/ws")
    out = _call(mcp._tool_manager.get_tool("plannotator_open"), target="spec.md")
    assert "ended without a decision" in out
    assert "read `/state/pna-plan-md-1.out`" not in out
    assert "verbatim" not in out  # no live session to hand over


def test_plannotator_open_docstring_states_the_surface() -> None:
    """The tool description says what annotate and review accept."""
    mcp = MCPServer("t")
    build_session_tools(mcp, workspace="/ws")
    tool = mcp._tool_manager.get_tool("plannotator_open")
    flat = " ".join((tool.description or "").split())
    assert "text-like" in flat and "not a diff scope" in flat


def test_plannotator_open_spawn_failure_is_tool_text(monkeypatch) -> None:
    """A failed spawn arrives as the standard failure text, carrying the
    CLI's advice.
    """

    class FakeMgr:
        def open(self, workspace, target, **kw):
            raise RuntimeError(
                "plannotator exited without opening a session; "
                "CLI stderr: 'For code review, use: plannotator review [DIRECTORY]'"
            )

    from tkt import _session_tools

    monkeypatch.setattr(_session_tools, "PlannotatorManager", lambda: FakeMgr())
    mcp = MCPServer("t")
    build_session_tools(mcp, workspace="/ws")
    out = _call(mcp._tool_manager.get_tool("plannotator_open"), target="spec.py")
    assert "plannotator_open failed" in out and "plannotator review" in out


def test_plannotator_open_discovered_session_has_no_capture(monkeypatch) -> None:
    """A human-started session is reported honestly: nothing to read back."""

    class FakeMgr:
        def open(self, workspace, target, **kw):
            return {
                "type": "open",
                "handle": "pna-plan-md-1",
                "url": "http://localhost:9/x",
                "result_path": None,
                "err_path": None,
                "reused": True,
                "source": "discovered",
            }

    from tkt import _session_tools

    monkeypatch.setattr(_session_tools, "PlannotatorManager", lambda: FakeMgr())
    mcp = MCPServer("t")
    build_session_tools(mcp, workspace="/ws")
    out = _call(mcp._tool_manager.get_tool("plannotator_open"), target="spec.md")
    assert "started outside tkt" in out and "null" in out


def test_plannotator_open_without_url_points_at_stderr(monkeypatch) -> None:
    """Listing breakage costs the URL only; the stderr file carries it."""

    class FakeMgr:
        def open(self, workspace, target, **kw):
            return {
                "type": "open",
                "handle": "pna-plan-md-1",
                "url": None,
                "result_path": "/state/pna-plan-md-1.out",
                "err_path": "/state/pna-plan-md-1.err",
                "reused": False,
                "source": "spawned",
            }

    from tkt import _session_tools

    monkeypatch.setattr(_session_tools, "PlannotatorManager", lambda: FakeMgr())
    mcp = MCPServer("t")
    build_session_tools(mcp, workspace="/ws")
    out = _call(mcp._tool_manager.get_tool("plannotator_open"), target="spec.md")
    assert "/state/pna-plan-md-1.err" in out


def _resolver(workspace: str) -> Any:
    """Return the resolver ``build_session_tools`` hands the Inbox proxy."""
    return partial(_resolve_patch_source, workspace)


def test_patch_file_source_guards(tmp_path) -> None:
    """A sandbox-supplied ``patch_file`` stays inside the workspace and the
    failure paths answer with agent-facing ValueError text.
    """
    (tmp_path / "binary.patch").write_bytes(b"\xff\xfe not utf-8 \x00")
    (tmp_path / "dir.patch").mkdir()
    with pytest.raises(ValueError, match="outside workspace"):
        _resolve_patch_source(str(tmp_path), "patch_file", "../elsewhere.patch")
    with pytest.raises(ValueError, match="outside workspace"):
        _resolve_patch_source(str(tmp_path), "patch_file", "/etc/passwd")
    with pytest.raises(ValueError, match="not found on the host"):
        _resolve_patch_source(str(tmp_path), "patch_file", "missing.patch")
    with pytest.raises(ValueError, match="not valid UTF-8"):
        _resolve_patch_source(str(tmp_path), "patch_file", "binary.patch")
    with pytest.raises(ValueError, match="cannot read patch file"):
        _resolve_patch_source(str(tmp_path), "patch_file", "dir.patch")


def test_patch_file_containment_reaches_the_agent_as_tool_text(tmp_path) -> None:
    """Forwarding a ``patch_file`` escape returns the refusal as tool text;
    nothing reaches upstream.
    """
    out = _forward(
        "submit_guide",
        {"guide": {"title": "t"}, "patch_file": "/etc/passwd"},
        str(tmp_path),
        _resolver(str(tmp_path)),
    )
    assert "outside workspace" in out
    assert "plannotator inbox failed" not in out  # the refusal short-circuits upstream


@pytest.fixture
def git_repo(tmp_path):
    """Create a real git repo at ``pkg``: a base commit plus a ``feature``
    branch touching two files (so a pathspec-scoped diff differs from the
    full one). Returns the base branch's name.
    """
    import git

    repo_dir = tmp_path / "pkg"
    repo_dir.mkdir()
    repo = git.Repo.init(repo_dir)
    repo.config_writer().set_value("user", "name", "Tests").release()
    repo.config_writer().set_value("user", "email", "tests@example.com").release()
    (repo_dir / "a.py").write_text("x = 1\n")
    (repo_dir / "b.txt").write_text("one\n")
    repo.git.add("-A")
    repo.git.commit("-m", "base")
    base = repo.active_branch.name
    repo.git.checkout("-b", "feature")
    (repo_dir / "a.py").write_text("x = 2\n")
    (repo_dir / "b.txt").write_text("two\n")
    repo.git.add("-A")
    repo.git.commit("-m", "feature")
    return base


def test_patch_spec_source_runs_real_git_diff(tmp_path, git_repo) -> None:
    """``patch_spec`` produces host-side ``git diff base...head`` output, with
    ``head`` defaulting to HEAD and ``paths`` scoping the diff.
    """
    ws = str(tmp_path)
    diff = _resolve_patch_source(ws, "patch_spec", {"dir": "pkg", "base": git_repo})
    assert "x = 2" in diff and "two" in diff
    scoped = _resolve_patch_source(
        ws, "patch_spec", {"dir": "pkg", "base": git_repo, "head": "feature", "paths": ["a.py"]}
    )
    assert "a.py" in scoped and "b.txt" not in scoped


def test_patch_spec_source_refusals(tmp_path, git_repo) -> None:
    """Unknown keys, missing or ``-``-prefixed refs, escaping ``dir`` and
    empty diffs all raise before git runs a harmful argv.
    """
    ws = str(tmp_path)
    cases = [
        ({"dir": "pkg", "base": git_repo, "diff": "x"}, "unknown key"),
        ({"dir": "pkg", "head": "feature"}, "requires a non-empty 'base'"),
        ({"dir": "pkg", "base": ""}, "requires a non-empty 'base'"),
        ({"dir": "pkg", "base": "-b", "head": "feature"}, "must not start with '-'"),
        ({"dir": "pkg", "base": git_repo, "head": "-x"}, "must not start with '-'"),
        ({"dir": "../outside", "base": git_repo}, "outside workspace"),
        ({"dir": "pkg", "base": "nope"}, "git diff failed"),
        ({"dir": "pkg", "base": git_repo, "head": git_repo}, "empty diff"),
    ]
    for spec, expected in cases:
        with pytest.raises(ValueError, match=expected):
            _resolve_patch_source(ws, "patch_spec", spec)
