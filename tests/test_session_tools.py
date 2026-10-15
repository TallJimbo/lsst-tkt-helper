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
from typing import Any

import pytest

from tkt._session_tools import build_session_tools, safe_subpath

pytest.importorskip("mcp")
from mcp.server.fastmcp import FastMCP


def _tools(mcp: FastMCP) -> set[str]:
    return {t.name for t in asyncio.run(mcp.list_tools())}


def _call(tool: Any, **kwargs: Any) -> Any:
    """Invoke a registered tool callable, whether the SDK wrapped it or not."""
    out = tool.fn(**kwargs)
    if inspect.iscoroutine(out):
        out = asyncio.run(out)
    return out


def test_session_tools_registered() -> None:
    """The builder registers exactly the session-lifecycle tools."""
    mcp = FastMCP("t")
    build_session_tools(mcp, workspace="/tmp/ws")
    names = _tools(mcp)
    assert {"brainstorm_server", "plannotator"} <= names


def test_no_host_danger_tools() -> None:
    """Guard: no host execution tools on the session surface."""
    mcp = FastMCP("t")
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
    """The plannotator tool delegates to the manager and refuses escapes."""
    from tkt import _session_tools

    calls: list[tuple] = []

    class FakeMgr:
        def open(self, workspace, target, **kw):
            calls.append((workspace, target, kw))
            return {"type": "open", "url": "http://localhost:9/x", "session": "pn-1", "reused": False}

    monkeypatch.setattr(_session_tools, "PlannotatorManager", lambda: FakeMgr())
    mcp = FastMCP("t")
    build_session_tools(mcp, workspace="/ws")
    # The installed SDK's list_tools() returns schema-only mcp.types.Tool
    # objects; the registered callable lives on the tool manager's Tool.
    tool = mcp._tool_manager.get_tool("plannotator")
    out = _call(tool, action="open", target="../escape.md")
    assert "outside workspace" in out
    out = _call(tool, action="open", target="spec.md")
    assert "pn-1" in out and calls[0][0] == "/ws"


FAKE_INBOX_MCP = r"""#!/usr/bin/env python3
import json, sys
for line in sys.stdin:
    try: req = json.loads(line)
    except ValueError: continue
    rid, method, params = req.get("id"), req.get("method"), req.get("params") or {}
    if method == "initialize":
        resp = {"jsonrpc":"2.0","id":rid,"result":{"protocolVersion":"2024-11-05",
                "capabilities":{"tools":{}},"serverInfo":{"name":"fake","version":"0"}}}
    elif method == "tools/list":
        resp = {"jsonrpc":"2.0","id":rid,"result":{"tools":[
            {"name":params.get("needle","send_message"),"description":"x",
             "inputSchema":{"type":"object","properties":{}}}]}}
    elif method == "tools/call":
        echo = {"jsonrpc":"2.0","id":rid,"result":{"content":[{"type":"text",
                "text":json.dumps({"tool":params.get("name"),"args":params.get("arguments")})}]}}
        if params.get("name") == "boom":
            echo["result"]["isError"] = True
            echo["result"]["content"][0]["text"] = "upstream exploded"
        sys.stdout.write(json.dumps(echo) + "\n"); sys.stdout.flush(); continue
    elif method == "notifications/initialized":
        continue
    else:
        resp = {"jsonrpc":"2.0","id":rid,"result":{}}
    sys.stdout.write(json.dumps(resp) + "\n"); sys.stdout.flush()
"""


@pytest.fixture
def fake_inbox_cli(tmp_path, monkeypatch):
    """Fake `plannotator` on PATH whose ``inbox mcp`` execs a newline
    JSON-RPC upstream (no real ``plannotator`` binary required).
    """
    import os

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "plannotator").write_text(
        '#!/usr/bin/env bash\nif [ "$1" = "inbox" ] && [ "$2" = "mcp" ]; then exec python3 '
        f"{bin_dir}/fake_inbox_mcp.py" + "; fi\nexit 0\n"
    )
    (bin_dir / "plannotator").chmod(0o755)
    (bin_dir / "fake_inbox_mcp.py").write_text(FAKE_INBOX_MCP)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return tmp_path


def test_inbox_send_maps_and_forwards(fake_inbox_cli) -> None:
    """``send`` maps to upstream ``send_message`` and forwards arguments."""
    import json as _json

    mcp = FastMCP("t")
    build_session_tools(mcp, workspace="/ws")
    tool = mcp._tool_manager.get_tool("plannotator_inbox")
    out = _call(tool, action="send", text="hello")
    forwarded = _json.loads(out)
    assert forwarded["tool"] == "send_message" and forwarded["args"]["text"] == "hello"


def test_inbox_guide_submit_forwards_payload_verbatim(fake_inbox_cli) -> None:
    """``guide_submit`` maps to upstream ``submit_guide`` and the payload dict
    reaches the upstream server verbatim (keys and values intact).
    """
    import json as _json

    mcp = FastMCP("t")
    build_session_tools(mcp, workspace="/ws")
    tool = mcp._tool_manager.get_tool("plannotator_inbox")
    payload = {"title": "Guided tour", "steps": [{"label": "diff", "note": "start here"}], "n": 2}
    out = _call(tool, action="guide_submit", payload=payload)
    forwarded = _json.loads(out)
    assert forwarded["tool"] == "submit_guide"
    assert forwarded["args"]["payload"] == payload
    assert set(forwarded["args"]["payload"]["steps"][0]) == {"label", "note"}


def test_inbox_action_map() -> None:
    """The allow-list is exactly the eight documented action mappings."""
    from tkt._session_tools import INBOX_ACTION_TOOLS

    assert INBOX_ACTION_TOOLS == {
        "send": "send_message",
        "read": "read_thread",
        "wait": "wait_for_reply",
        "resolve": "resolve_message",
        "decisions": "list_decisions",
        "record": "record_decision",
        "guide_brief": "get_guide_brief",
        "guide_submit": "submit_guide",
    }


def test_inbox_unknown_action_rejected_without_spawn(monkeypatch) -> None:
    """Security surface: non-allow-listed actions are rejected locally
    and never spawn the upstream server.
    """
    import mcp.client.stdio as stdio_mod

    spawns: list = []

    def _spy(*args, **kwargs):
        spawns.append((args, kwargs))
        raise AssertionError("upstream must not spawn for a non-allow-listed action")

    monkeypatch.setattr(stdio_mod, "stdio_client", _spy)
    mcp = FastMCP("t")
    build_session_tools(mcp, workspace="/ws")
    tool = mcp._tool_manager.get_tool("plannotator_inbox")
    out = _call(tool, action="approve", text="rm -rf")
    assert "unknown action" in out
    assert spawns == []


def test_inbox_upstream_error_surfaced(fake_inbox_cli) -> None:
    """Upstream ``isError`` results surface as tool text, not exceptions."""
    from tkt._session_tools import _inbox_call

    out = _inbox_call("boom", {})
    assert "upstream error" in out and "upstream exploded" in out
