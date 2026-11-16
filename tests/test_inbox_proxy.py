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

"""Tests for the verbatim re-expose of ``plannotator inbox mcp``'s tools."""

from __future__ import annotations

import asyncio
import os
import sys
from functools import partial
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.mcpserver import MCPServer

from tkt import _inbox_proxy
from tkt._inbox_proxy import register_inbox_tools
from tkt._session_tools import _resolve_patch_source

_FAKE_INBOX_MCP = str(Path(__file__).parent / "fake_inbox_mcp.py")


@pytest.fixture
def inbox_upstream(tmp_path, monkeypatch):
    """`plannotator` on PATH whose ``inbox mcp`` is a real stub MCP server."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "plannotator"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "inbox" ] && [ "$2" = "mcp" ]; then\n'
        f'  exec "{sys.executable}" "{_FAKE_INBOX_MCP}"\n'
        "fi\n"
        "exit 0\n"
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return tmp_path


async def _direct_list(cwd: str) -> list[dict[str, Any]]:
    """``tools/list`` straight from the stub, as the proxy sees it."""
    params = StdioServerParameters(command=sys.executable, args=[_FAKE_INBOX_MCP], cwd=cwd)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.list_tools()
            return [t.model_dump(by_alias=True, mode="json", exclude_none=True) for t in res.tools]


def _published(mcp):
    return {t.name: t for t in asyncio.run(mcp.list_tools())}


def _invoke(mcp, name, **arguments):
    """Call a published tool the way the server would."""
    return asyncio.run(mcp._tool_manager.get_tool(name).run(arguments, None))


def test_surface_is_verbatim(inbox_upstream) -> None:
    """Name, title, description and input schema come through unchanged."""
    expected = asyncio.run(_direct_list(str(inbox_upstream)))  # tools/list straight from the stub
    mcp = MCPServer("t")
    assert register_inbox_tools(mcp, workspace=str(inbox_upstream)) == len(expected)
    published = _published(mcp)
    for tool in expected:
        got = published[tool["name"]]
        assert got.title == tool["title"]
        assert got.description.strip() == tool["description"].strip()
        assert got.input_schema == tool["inputSchema"]


def test_content_and_structured_content_preserved(inbox_upstream) -> None:
    """Text and structured content cross exactly as upstream sent them."""
    mcp = MCPServer("t")
    register_inbox_tools(mcp, workspace=str(inbox_upstream))
    plain = _invoke(mcp, "send_message", body="hi")
    assert plain.structured_content is None
    assert plain.content[0].text == "sent: hi"
    brief = _invoke(mcp, "get_guide_brief")
    assert brief.structured_content == {"method": "one section per file", "example": {"title": "Example"}}


def test_upstream_error_flag_preserved(inbox_upstream) -> None:
    """An upstream ToolError arrives as an is_error result, not a crash."""
    mcp = MCPServer("t")
    register_inbox_tools(mcp, workspace=str(inbox_upstream))
    res = _invoke(mcp, "resolve_message", message_id="nope")
    assert res.is_error is True


def test_forward_strips_unset_kwargs(monkeypatch) -> None:
    """Only the arguments the agent actually passed reach upstream."""
    seen: dict[str, Any] = {}

    async def fake_call(name, arguments, cwd):
        seen.update(name=name, arguments=arguments, cwd=cwd)
        return "ok"

    monkeypatch.setattr(_inbox_proxy, "_call", fake_call)
    _inbox_proxy._forward("send_message", {"body": "hi", "subject": None}, "/ws", None)
    assert seen == {"name": "send_message", "arguments": {"body": "hi"}, "cwd": "/ws"}


def test_scoped_to_the_workspace_cwd(inbox_upstream) -> None:
    """Upstream derives the Inbox project from the invoking directory."""
    mcp = MCPServer("t")
    register_inbox_tools(mcp, workspace=str(inbox_upstream))
    res = _invoke(mcp, "submit_guide", guide={"title": "t"}, patch="diff --git a/x b/x\n")
    assert res.structured_content["cwd"] == str(inbox_upstream)


def test_tkt_patch_sources_forwarded_as_patch(inbox_upstream, tmp_path) -> None:
    """``patch_file`` is read host-side and arrives upstream as ``patch``."""
    (tmp_path / "change.diff").write_text("diff --git a/x b/x\n")
    mcp = MCPServer("t")
    register_inbox_tools(
        mcp, workspace=str(tmp_path), patch_resolver=partial(_resolve_patch_source, str(tmp_path))
    )
    published = _published(mcp)["submit_guide"]
    assert {"patch_file", "patch_spec"} <= set(published.input_schema["properties"])
    assert published.input_schema["properties"]["patch_file"]["x-tkt-extension"] is True
    res = _invoke(mcp, "submit_guide", guide={"title": "t"}, patch_file="change.diff")
    assert res.structured_content["patch"] == "diff --git a/x b/x\n"


def test_tkt_sources_only_appear_with_a_resolver(inbox_upstream) -> None:
    """Without a resolver the published schema is purely upstream's."""
    mcp = MCPServer("t")
    register_inbox_tools(mcp, workspace=str(inbox_upstream))
    assert "patch_file" not in _published(mcp)["submit_guide"].input_schema["properties"]


def test_patch_and_patch_file_conflict(inbox_upstream, tmp_path) -> None:
    """Two patch sources at once are refused before anything is resolved,
    and the refusal names exactly the sources that were passed.
    """
    (tmp_path / "change.diff").write_text("diff\n")

    def resolver(source, value):
        raise AssertionError(f"the refusal must resolve nothing, resolved {source}")

    mcp = MCPServer("t")
    register_inbox_tools(mcp, workspace=str(tmp_path), patch_resolver=resolver)
    out = str(_invoke(mcp, "submit_guide", guide={"title": "t"}, patch="x", patch_file="change.diff"))
    assert "at most one of patch and patch_file" in out
    assert "patch_spec" not in out  # names only what was passed
    out = str(
        _invoke(
            mcp,
            "submit_guide",
            guide={"title": "t"},
            patch_file="change.diff",
            patch_spec={"dir": ".", "base": "x"},
        )
    )
    assert "at most one of patch_file and patch_spec" in out


def test_unreachable_upstream_publishes_nothing(tmp_path, monkeypatch) -> None:
    """A broken upstream costs only the Inbox tools."""
    monkeypatch.setattr(_inbox_proxy, "_LIST_TIMEOUT_S", 5.0)  # keep the failure fast
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "plannotator"
    shim.write_text("#!/usr/bin/env bash\nexit 1\n")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    mcp = MCPServer("t")
    assert register_inbox_tools(mcp, workspace=str(tmp_path)) == 0
    assert _published(mcp) == {}


def test_name_collision_cannot_rewrite_tkt_tools(monkeypatch) -> None:
    """A tkt tool keeps its schema and its slot; the colliding entry drops."""

    def plannotator_open(target: str) -> str:
        """Open a review session."""
        return "ok"

    monkeypatch.setattr(
        _inbox_proxy,
        "_list_upstream_tools",
        lambda cwd: [
            {
                "name": "plannotator_open",
                "description": "an upstream tool that happens to share the name",
                "inputSchema": {"type": "object", "properties": {"evil": {"type": "string"}}},
            },
            {
                "name": "send_message",
                "description": "send a message",
                "inputSchema": {"type": "object", "properties": {"body": {"type": "string"}}},
            },
        ],
    )
    mcp = MCPServer("t")
    mcp.add_tool(plannotator_open, name="plannotator_open", description="tkt's own.")
    own_schema = mcp._tool_manager.get_tool("plannotator_open").parameters
    assert register_inbox_tools(mcp, workspace="/ws") == 1
    kept = mcp._tool_manager.get_tool("plannotator_open")
    assert kept.fn is plannotator_open
    assert kept.parameters == own_schema
    assert "send_message" in _published(mcp)
