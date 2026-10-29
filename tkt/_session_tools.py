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
                # Upstream ships real payloads (get_guide_brief's brief)
                # in structuredContent; content carries only a summary.
                if res.structured_content is not None:
                    import json

                    block = json.dumps(res.structured_content, indent=2, default=str)
                    joined += f"\n```json\n{block}\n```"
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


# Host-side built-in Plan-mode plan store: the session ``plannotator`` tool
# accepts files from here as ``open`` targets in addition to the workspace.
_PLAN_DIR = os.path.join("~", ".opencode", "plan")


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
        (feedback is not pushed to you — pull it with ``action="result"``
        once the human says it is in).  Never run the ``plannotator``
        CLI yourself.
        ``status`` lists this workspace's sessions (live and closed-but-
        uncollected); entries carry the ``id`` accepted by ``result`` and
        ``close``; ``result`` reads the on-disk records for ``session``
        (``type``: feedback/pending/closed); ``close`` terminates it.
        ``kind`` is ``annotate`` or ``review``; ``gate`` adds the approval
        UI; ``base`` sets the review compare ref.  Non-URL ``target`` paths
        must live inside the workspace or the host plan directory
        (``~/.opencode/plan``).

        Args:
            action: One of "open", "status", "result", or "close".
            target: "open" only; the file or folder to annotate, the file or
                folder to review, or a PR URL to review (absolute or
                workspace-relative). Files under the host plan directory
                (``~/.opencode/plan``) are also accepted.
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
                plan_root = os.path.realpath(os.path.expanduser(_PLAN_DIR))
                if not re.match(r"https?://", target):  # PR URLs allowed
                    try:
                        target = safe_subpath(workspace, target)
                    except ValueError:
                        target = safe_subpath(plan_root, target)  # plan-mode plan files
                info = plannotator_mgr.open(
                    workspace, target, kind=kind, gate=gate, base=base, extra_roots=[plan_root]
                )
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
        body: str | None = None,
        subject: str | None = None,
        text: str | None = None,
        thread: str | None = None,
        reply_to: str | None = None,
        thread_id: str | None = None,
        message_id: str | None = None,
        cursor: int | None = None,
        resolved: bool | None = None,
        attachments: list[str] | None = None,
        guide: dict[str, Any] | str | None = None,
        patch: str | None = None,
        snapshot: dict[str, Any] | str | None = None,
        project_path: str | None = None,
        payload: dict[str, Any] | str | None = None,
        description: str | None = None,  # present for human approvals of tool actions
    ) -> str:
        """Leave non-blocking messages/questions for the human in the
        Plannotator Inbox, and drive the guided-review flow:
        ``guide_brief`` fetches the brief for authoring a guided review
        walkthrough, ``guide_submit`` submits the authored ``guide`` with the
        ``patch`` it describes (or a ``snapshot``).  ``wait`` blocks up to
        ~50 s for a reply and may return a ``cursor`` — pass it back to keep
        waiting.  Nothing here wakes the human's browser at the session;
        replies arrive to you via ``wait``/``read``.

        Args:
            action: One of "send", "read", "wait", "resolve", "decisions",
                "record", "guide_brief", or "guide_submit".
            body: "send": the markdown message for the human (question
                blocks render as answerable cards); "guide_submit": an
                optional note above the guide card.
            subject: "send"/"guide_submit": one-line thread subject when
                starting a thread.
            text: "record" only: the decision summary.
            thread: "send"/"guide_submit": thread *name* to join or start.
            reply_to: "send"/"guide_submit": message id this one answers.
            thread_id: "read"/"wait": the thread to read or wait on.
            message_id: "read"/"wait": read/wait on this message's thread;
                "resolve": a message (or the thread id) whose thread to
                archive.
            cursor: "wait": resume token from the previous wait's result.
            resolved: "resolve": False reopens an archived thread.
            attachments: "send": files to show the human beside the message
                (workspace-relative or absolute paths).
            guide: "guide_submit": the authored guide (JSON object or
                string) in the shape ``guide_brief`` describes.
            patch: "guide_submit": the exact unified diff the guide
                describes.
            snapshot: "guide_submit": a complete snapshot in place of
                guide + patch.
            project_path: "decisions": folder whose decisions to list;
                defaults to this workspace.
            payload: "record": structured data carried with the decision.
            description: Optional human-readable rationale for this call.
        """
        name = INBOX_ACTION_TOOLS.get(action.strip().lower())
        if name is None:
            return f"plannotator_inbox: unknown action {action!r}; use one of {sorted(INBOX_ACTION_TOOLS)}."
        action = action.strip().lower()

        def _present(**kv: Any) -> dict[str, Any]:
            return {k: v for k, v in kv.items() if v is not None}

        # Forward only the keys the upstream tool for this action accepts,
        # under its own names.
        if action == "send":
            if body is None:
                return "plannotator_inbox: send requires `body` (the message for the human)."
            args = _present(
                body=body, subject=subject, thread=thread, reply_to=reply_to, attachments=attachments
            )
        elif action == "read":
            args = _present(thread_id=thread_id, message_id=message_id)
        elif action == "wait":
            args = _present(thread_id=thread_id, message_id=message_id, cursor=cursor)
        elif action == "resolve":
            if message_id is None:
                return (
                    "plannotator_inbox: resolve requires `message_id` "
                    "(any message in the thread, or the thread id)."
                )
            args = _present(message_id=message_id, resolved=resolved)
        elif action == "record":
            if text is None:
                return "plannotator_inbox: record requires `text` (the decision summary)."
            args = _present(text=text, payload=payload)
        elif action == "decisions":
            try:
                folder = safe_subpath(workspace, project_path) if project_path else workspace
            except ValueError as exc:
                return f"plannotator_inbox failed: {exc}"
            args = {"project_path": folder}
        elif action == "guide_brief":
            args = {}
        else:  # guide_submit
            if snapshot is None and (guide is None or patch is None):
                return "plannotator_inbox: guide_submit needs `guide` with `patch`, or a complete `snapshot`."
            args = _present(
                guide=guide,
                patch=patch,
                snapshot=snapshot,
                body=body,
                subject=subject,
                thread=thread,
                reply_to=reply_to,
            )
        return _inbox_call(name, args)


def _json_block(obj: Any) -> str:
    import json

    return json.dumps(obj, indent=2, default=str)
