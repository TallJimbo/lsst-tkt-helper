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
from mcp.server.mcpserver import MCPServer


def _tools(mcp: MCPServer) -> set[str]:
    return {t.name for t in asyncio.run(mcp.list_tools())}


def _call(tool: Any, **kwargs: Any) -> Any:
    """Invoke a registered tool callable, whether the SDK wrapped it or not."""
    out = tool.fn(**kwargs)
    if inspect.iscoroutine(out):
        out = asyncio.run(out)
    return out


def test_session_tools_registered() -> None:
    """The builder registers exactly the session-lifecycle tools."""
    mcp = MCPServer("t")
    build_session_tools(mcp, workspace="/tmp/ws")
    names = _tools(mcp)
    assert {"brainstorm_server", "plannotator"} <= names


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
    """The plannotator tool delegates to the manager and refuses escapes."""
    from tkt import _session_tools

    calls: list[tuple] = []

    class FakeMgr:
        def open(self, workspace, target, **kw):
            calls.append((workspace, target, kw))
            return {"type": "open", "url": "http://localhost:9/x", "session": "pn-1", "reused": False}

    monkeypatch.setattr(_session_tools, "PlannotatorManager", lambda: FakeMgr())
    mcp = MCPServer("t")
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

# Per-tool argument sets mirroring `plannotator inbox --help`: required keys
# and the keys each upstream tool recognizes.  A wrapper that forwards a
# wrong or unrecognized key must be refused here, the way the real Inbox
# refuses it.
SCHEMAS = {
    "send_message": (["body"], {"body", "subject", "thread", "reply_to", "attachments"}),
    "read_thread": ([], {"thread_id", "message_id"}),
    "wait_for_reply": ([], {"thread_id", "message_id", "cursor"}),
    "resolve_message": (["message_id"], {"message_id", "resolved"}),
    "record_decision": (["text"], {"text", "payload"}),
    "list_decisions": (["project_path"], {"project_path"}),
    "get_guide_brief": ([], set()),
    "submit_guide": ([], {"guide", "patch", "snapshot", "body", "subject", "thread", "reply_to"}),
}

def refuse(rid, msg):
    return {"jsonrpc": "2.0", "id": rid, "result": {
        "content": [{"type": "text", "text": msg}], "isError": True}}

for line in sys.stdin:
    try: req = json.loads(line)
    except ValueError: continue
    rid, method, params = req.get("id"), req.get("method"), req.get("params") or {}
    if method == "initialize":
        resp = {"jsonrpc":"2.0","id":rid,"result":{"protocolVersion":"2024-11-05",
                "capabilities":{"tools":{}},"serverInfo":{"name":"fake","version":"0"}}}
    elif method == "tools/list":
        resp = {"jsonrpc":"2.0","id":rid,"result":{"tools":[]}}
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "boom":
            resp = refuse(rid, "upstream exploded")
        else:
            required, allowed = SCHEMAS.get(name, ([], set(args)))
            unknown = sorted(set(args) - allowed)
            missing = [k for k in required if k not in args]
            if unknown:
                resp = refuse(rid, 'Invalid arguments for tool %s: Unrecognized key: "%s"'
                              % (name, unknown[0]))
            elif missing:
                resp = refuse(rid, "Invalid arguments for tool %s: %s: Invalid input: "
                              "expected string, received undefined" % (name, missing[0]))
            elif name == "get_guide_brief":
                # The brief ships in structuredContent, with only a one-line
                # companion string in content (as the real get_guide_brief does).
                resp = {"jsonrpc":"2.0","id":rid,"result":{
                    "content":[{"type":"text","text":"The guided-review method, the "
                                "guide's shape, how to read the diff, the rules and a "
                                "worked submit_guide call."}],
                    "structuredContent":{"methodology":"read the diff first",
                                          "output_schema":{"title":"str"},
                                          "diff_steps":["git diff main...HEAD"],
                                          "rules":["code diffs only"],
                                          "example":{"guide":{"title":"tour"},"patch":"@@ x"}}}}
            else:
                resp = {"jsonrpc":"2.0","id":rid,"result":{"content":[{"type":"text",
                        "text":json.dumps({"tool":name,"args":args})}]}}
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


def _inbox_tool(workspace: str = "/ws") -> Any:
    mcp = MCPServer("t")
    build_session_tools(mcp, workspace=workspace)
    return mcp._tool_manager.get_tool("plannotator_inbox")


def _forwarded(out: str) -> dict:
    import json as _json

    return _json.loads(out)


def _no_spawn_spy(monkeypatch) -> list:
    import mcp.client.stdio as stdio_mod

    spawns: list = []

    def _spy(*args, **kwargs):
        spawns.append((args, kwargs))
        raise AssertionError("upstream must not spawn when the wrapper rejects locally")

    monkeypatch.setattr(stdio_mod, "stdio_client", _spy)
    return spawns


def test_inbox_send_forwards_body_and_routing(fake_inbox_cli) -> None:
    """``send`` maps to upstream ``send_message``, forwarding ``body`` (not
    ``text``) plus the routing fields under their upstream names.
    """
    out = _forwarded(
        _call(
            _inbox_tool(),
            action="send",
            body="hello",
            subject="Probe",
            reply_to="msg_1",
            attachments=["docs/plan.md"],
        )
    )
    assert out["tool"] == "send_message"
    assert out["args"] == {
        "body": "hello",
        "subject": "Probe",
        "reply_to": "msg_1",
        "attachments": ["docs/plan.md"],
    }


def test_inbox_guide_submit_forwards_guide_and_patch(fake_inbox_cli) -> None:
    """``guide_submit`` maps to upstream ``submit_guide`` with ``guide`` +
    ``patch`` intact (keys and values), plus its routing fields.
    """
    guide = {"title": "Guided tour", "steps": [{"label": "diff", "note": "start here"}]}
    patch = "@@ -1 +1 @@\n-a\n+b\n"
    out = _forwarded(_call(_inbox_tool(), action="guide_submit", guide=guide, patch=patch, reply_to="msg_1"))
    assert out["tool"] == "submit_guide"
    assert out["args"] == {"guide": guide, "patch": patch, "reply_to": "msg_1"}


def test_inbox_guide_submit_forwards_snapshot(fake_inbox_cli) -> None:
    """A complete ``snapshot`` substitutes for guide + patch."""
    snapshot = {"files": [{"path": "a.py", "content": "x"}]}
    out = _forwarded(_call(_inbox_tool(), action="guide_submit", snapshot=snapshot))
    assert out["args"] == {"snapshot": snapshot}


def test_inbox_read_wait_resolve_forward_upstream_ids(fake_inbox_cli) -> None:
    """``read``/``wait``/``resolve`` route by ``thread_id``/``message_id`` and
    ``wait`` carries the ``cursor`` resume token; ``resolve`` takes
    ``resolved`` to reopen.
    """
    tool = _inbox_tool()
    out = _forwarded(_call(tool, action="read", thread_id="thr_1"))
    assert out["tool"] == "read_thread" and out["args"] == {"thread_id": "thr_1"}
    out = _forwarded(_call(tool, action="wait", thread_id="thr_1", cursor=3))
    assert out["tool"] == "wait_for_reply" and out["args"] == {"thread_id": "thr_1", "cursor": 3}
    out = _forwarded(_call(tool, action="resolve", message_id="msg_1", resolved=False))
    assert out["tool"] == "resolve_message" and out["args"] == {"message_id": "msg_1", "resolved": False}


def test_inbox_record_forwards_text_and_payload(fake_inbox_cli) -> None:
    """``record`` maps to upstream ``record_decision`` with ``text`` and an
    optional structured ``payload``.
    """
    payload = {"choice": "dedicated queue", "why": "isolation"}
    out = _forwarded(_call(_inbox_tool(), action="record", text="Chose the queue", payload=payload))
    assert out["tool"] == "record_decision"
    assert out["args"] == {"text": "Chose the queue", "payload": payload}


def test_inbox_decisions_defaults_project_path_to_workspace(fake_inbox_cli) -> None:
    """``decisions`` supplies the ``project_path`` the upstream shim does not
    fill itself: the workspace by default, a contained path when given.
    """
    tool = _inbox_tool(workspace="/ws")
    out = _forwarded(_call(tool, action="decisions"))
    assert out["tool"] == "list_decisions" and out["args"] == {"project_path": "/ws"}
    out = _forwarded(_call(tool, action="decisions", project_path="sub"))
    assert out["args"] == {"project_path": "/ws/sub"}
    out = _call(tool, action="decisions", project_path="../elsewhere")
    assert "outside workspace" in out


def test_inbox_structured_content_surfaced(fake_inbox_cli) -> None:
    """``guide_brief``'s brief arrives in ``structuredContent``; the wrapper
    must surface it, not just the one-line companion text.
    """
    out = _call(_inbox_tool(), action="guide_brief")
    assert "guided-review method" in out  # companion text kept
    assert "```json" in out  # structured fields surfaced
    for key in ("methodology", "output_schema", "diff_steps", "rules", "example"):
        assert key in out


def test_inbox_local_validation_rejects_without_spawn(monkeypatch) -> None:
    """Missing required arguments fail locally with the parameter named —
    no upstream spawn, no opaque upstream error.
    """
    spawns = _no_spawn_spy(monkeypatch)
    tool = _inbox_tool()
    assert "body" in _call(tool, action="send")
    assert "message_id" in _call(tool, action="resolve")
    assert "text" in _call(tool, action="record")
    assert "guide" in _call(tool, action="guide_submit")
    assert "patch" in _call(tool, action="guide_submit", guide={"title": "t"})
    assert spawns == []


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
    mcp = MCPServer("t")
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
