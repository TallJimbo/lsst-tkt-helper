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

"""Session tool registration shared by the Zed MCP server and the sandbox
session server.  Registers ONLY session-lifecycle tools; the sandbox-facing
surface must never grow host-execution tools.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from ._sessionmanagers import BrainstormManager, PlannotatorManager, _format_brainstorm_info

_INBOX_UPSTREAM_TIMEOUT_S = 75.0  # > the upstream wait_for_reply ~50 s cursor

INBOX_ACTION_TOOLS = {
    "send": "send_message",
    "read": "read_thread",
    "wait": "wait_for_reply",
    "resolve": "resolve_message",
    "decisions": "list_decisions",
    "record": "record_decision",
    "guide_brief": "get_guide_brief",
    "guide_submit": "submit_guide",
}


def _inbox_call(upstream_tool: str, arguments: dict[str, Any]) -> str:
    """One-shot stdio MCP call to ``plannotator inbox mcp`` (host side)."""
    import asyncio

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.types import TextContent

    async def _run() -> str:
        params = StdioServerParameters(command="plannotator", args=["inbox", "mcp"])
        async with asyncio.timeout(_INBOX_UPSTREAM_TIMEOUT_S):  # > upstream's ~50 s cursor
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    res = await session.call_tool(upstream_tool, arguments)
                texts = [c.text for c in res.content if isinstance(c, TextContent)]
                joined = "\n".join(texts)
                # mcp 2.x models use snake_case attributes.
                if res.is_error:
                    return f"plannotator_inbox upstream error: {joined}"
                return joined

    try:
        return asyncio.run(_run())
    except FileNotFoundError as exc:
        return f"plannotator_inbox failed: plannotator binary not found on host ({exc})."
    except Exception as exc:  # surface any upstream breakage as tool text
        return f"plannotator_inbox failed: {exc}."


def safe_subpath(workspace: str, candidate: str) -> str:
    """Resolve ``candidate`` under ``workspace``; refuse escapes."""
    ws = Path(workspace).resolve()
    resolved = (ws / candidate).resolve() if not os.path.isabs(candidate) else Path(candidate).resolve()
    if resolved != ws and not str(resolved).startswith(str(ws) + os.sep):
        raise ValueError(f"path {candidate!r} is outside workspace {str(ws)!r}.")
    return str(resolved)


def build_session_tools(mcp: MCPServer, *, workspace: str) -> None:
    """Register the session tools on ``mcp``."""
    brainstorm = BrainstormManager(root=workspace)
    plannotator_mgr = PlannotatorManager()

    @mcp.tool()
    def brainstorm_server(
        action: str,
        project_dir: str = ".",
        port: int | None = None,
        idle_timeout_minutes: int | None = None,
        open_browser: bool = True,
        description: str | None = None,  # present for human approvals of tool actions
    ) -> str:
        """Manage the brainstorming visual-companion server on the host.

        The companion server runs outside the sandbox, where the human's
        browser reaches it directly (no network bridge needed) and can
        auto-open.  You interact with it only through files: write HTML
        screens into ``screen_dir`` and read the human's clicks from
        ``state_dir/events``.  Prefer this tool over launching
        ``start-server.sh`` from ``bash``: the sandbox is network-isolated
        (an in-sandbox server's URL is unreachable) and detached processes
        there are fragile.  See the brainstorming skill's visual companion
        guide for the screen-writing conventions.  Also never run it with
        ``--owner-pid`` semantics tied to a client; the host owns its
        lifecycle.

        Args:
            action: One of "start", "status", or "stop".
            project_dir: Writable project root for session files, inside the
                workspace (relative paths resolve against the project root);
                screens and events live under
                ``<project_dir>/.superpowers/brainstorm/``.
            port: Fixed host port for "start"; by default a random high port
                is chosen and then reused across restarts of the same
                project.
            idle_timeout_minutes: "start" only; auto-shutdown after this many
                idle minutes (server default 240).
            open_browser: "start" only; auto-open the human's browser on the
                first pushed screen (default True; only ever opens once, and
                not if a tab is already connected).
            description: Optional human-readable rationale for this call.
        """
        action = action.strip().lower()
        try:
            project = safe_subpath(workspace, project_dir)
            if action == "start":
                # owner_pid omitted on purpose: session servers die by
                # close / idle timeout / sweep / tkt rm, never by client
                # disconnect.
                info = brainstorm.start(
                    project, port=port, idle_timeout_minutes=idle_timeout_minutes, open_browser=open_browser
                )
            elif action == "status":
                info = brainstorm.status(project)
            elif action == "stop":
                info = brainstorm.stop(project)
            else:
                return f"brainstorm_server: unknown action {action!r}; use 'start', 'status', or 'stop'."
        except (ValueError, RuntimeError, FileNotFoundError, OSError) as exc:
            return f"brainstorm_server failed: {exc}"
        return _format_brainstorm_info(info)

    @mcp.tool()
    def plannotator(
        action: str,  # "open" | "status" | "result" | "close"
        target: str | None = None,
        kind: str = "annotate",
        gate: bool = False,
        base: str | None = None,
        session: str | None = None,
        description: str | None = None,  # present for human approvals of tool actions
    ) -> str:
        """Run host-side Plannotator review sessions for this workspace.

        ``open`` attaches to a live session for the same target when one
        exists, otherwise spawns a detached host session; it returns the
        session ``url`` — hand it to the human **verbatim** and end your turn
        (feedback arrives later, or via ``action="result"`` after the human
        says it is in).  Never run the ``plannotator`` CLI yourself.
        ``status`` lists this workspace's sessions (live and closed-but-
        uncollected); entries carry the ``id`` accepted by ``result`` and
        ``close``; ``result`` reads the on-disk records for ``session``
        (``type``: feedback/pending/closed); ``close`` terminates it.
        ``kind`` is ``annotate`` or ``review``; ``gate`` adds the approval
        UI; ``base`` sets the review compare ref.  Non-URL ``target`` paths
        must live inside the workspace.

        Args:
            action: One of "open", "status", "result", or "close".
            target: "open" only; the file or folder to annotate, the file or
                folder to review, or a PR URL to review (absolute or
                workspace-relative).
            kind: "open" only; "annotate" (default) or "review".
            gate: "open" only; add the approval-gate UI to an annotate
                session.
            base: "open" only; the compare ref for a review session
                (``--base``).
            session: Session id (from "open" or "status"); required by
                "result" and "close".
            description: Optional human-readable rationale for this call.
        """
        try:
            if action == "open":
                if target is None:
                    return "plannotator open requires a target (file, folder, or PR URL)."
                if not re.match(r"https?://", target):  # PR URLs allowed
                    target = safe_subpath(workspace, target)
                info = plannotator_mgr.open(workspace, target, kind=kind, gate=gate, base=base)
                url = info.get("url")
                body = (
                    "```json\n" + _json_block(info) + "\n```\n"
                    f"Hand the human this URL **verbatim** and end your turn: {url}"
                    if url
                    else "```json\n" + _json_block(info) + "\n```\n"
                    "Session spawning; call action='status' once to retrieve its URL to hand over."
                )
                return body
            if action == "status":
                return "```json\n" + _json_block(plannotator_mgr.status(workspace)) + "\n```"
            if action == "result":
                if session is None:
                    return "plannotator result requires a session id (from open/status)."
                return "```json\n" + _json_block(plannotator_mgr.result(workspace, session)) + "\n```"
            if action == "close":
                if session is None:
                    return "plannotator close requires a session id."
                return "```json\n" + _json_block(plannotator_mgr.close(workspace, session)) + "\n```"
            return f"plannotator: unknown action {action!r}; use 'open', 'status', 'result', or 'close'."
        except (ValueError, RuntimeError, FileNotFoundError, OSError) as exc:
            return f"plannotator failed: {exc}"

    @mcp.tool()
    def plannotator_inbox(
        action: str,  # "send"|"read"|"wait"|"resolve"|"decisions"|"record"|"guide_brief"|"guide_submit"
        text: str | None = None,
        thread: str | None = None,
        reply_to: str | None = None,
        payload: dict[str, Any] | str | None = None,
        description: str | None = None,  # present for human approvals of tool actions
    ) -> str:
        """Leave non-blocking messages/questions for the human in the
        Plannotator Inbox, and drive the guided-review flow:
        ``guide_brief`` fetches the diff brief for authoring a guided review
        walkthrough, ``guide_submit`` submits the authored guide JSON (in
        ``payload``).  ``wait`` blocks up to ~50 s for a reply and may return
        a cursor — call it again.  Nothing here wakes the human's browser at
        the session; replies arrive to you via ``wait``/``read`` or as
        injected ``Plannotator:`` messages.

        Args:
            action: One of "send", "read", "wait", "resolve", "decisions",
                "record", "guide_brief", or "guide_submit".
            text: "send" only; the message body left for the human.
            thread: Thread id routing read/wait/resolve to one conversation.
            reply_to: Message id this one answers.
            payload: Structured input carried for "record" and
                "guide_submit" — a JSON object (dict) or string.
            description: Optional human-readable rationale for this call.
        """
        name = INBOX_ACTION_TOOLS.get(action.strip().lower())
        if name is None:
            return f"plannotator_inbox: unknown action {action!r}; use one of {sorted(INBOX_ACTION_TOOLS)}."
        args: dict[str, Any] = {
            k: v for k, v in {"text": text, "thread": thread, "reply_to": reply_to}.items() if v is not None
        }
        if payload is not None:
            args["payload"] = payload
        return _inbox_call(name, args)


def _json_block(obj: Any) -> str:
    import json

    return json.dumps(obj, indent=2, default=str)
