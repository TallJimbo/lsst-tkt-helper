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
from functools import partial
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from ._inbox_proxy import register_inbox_tools
from ._sessionmanagers import BrainstormManager, PlannotatorManager, _format_brainstorm_info


def safe_subpath(workspace: str, candidate: str) -> str:
    """Resolve ``candidate`` under ``workspace``; refuse escapes."""
    ws = Path(workspace).resolve()
    resolved = (ws / candidate).resolve() if not os.path.isabs(candidate) else Path(candidate).resolve()
    if resolved != ws and not str(resolved).startswith(str(ws) + os.sep):
        raise ValueError(f"path {candidate!r} is outside workspace {str(ws)!r}.")
    return str(resolved)


def _git_diff_spec(workspace: str, spec: dict[str, Any]) -> tuple[str, str]:
    """Run a ``patch_spec`` ``git diff`` host-side; return (diff, origin).

    The diff is ``base...head`` (merge-base semantics) over the optional
    pathspecs, inside ``spec["dir"]`` contained to the workspace.  Refs are
    checked for a leading ``-`` so they cannot read as git options.
    """
    import subprocess

    unknown = set(spec) - {"dir", "base", "head", "paths"}
    if unknown:
        raise ValueError(f"patch_spec has unknown key(s) {sorted(unknown)}; allowed: dir, base, head, paths.")
    base = spec.get("base")
    if not isinstance(base, str) or not base:
        raise ValueError("patch_spec requires a non-empty 'base' ref.")
    head = spec.get("head", "HEAD")
    if not isinstance(head, str) or not head:
        raise ValueError("patch_spec 'head' must be a non-empty ref.")
    if base.startswith("-") or head.startswith("-"):
        raise ValueError("patch_spec refs must not start with '-'.")
    paths = spec.get("paths") or []
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise ValueError("patch_spec 'paths' must be a list of path strings.")
    repo = safe_subpath(workspace, str(spec.get("dir", ".")))
    res = subprocess.run(
        ["git", "-C", repo, "diff", "--no-color", f"{base}...{head}", "--", *paths],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise ValueError(f"git diff failed in {repo}: {res.stderr.strip()}")
    if not res.stdout.strip():
        raise ValueError(f"patch_spec produced an empty diff (base={base!r}, head={head!r}).")
    return res.stdout, f"git diff {base}...{head} in {repo}"


def _resolve_patch_source(workspace: str, name: str, value: Any) -> str:
    """Resolve a tkt ``submit_guide`` patch source to diff text, host-side.

    ``patch_file`` is read inside the workspace (a sandbox-private path is
    invisible to this host process, which is why the property exists);
    ``patch_spec`` is a ``git diff base...head`` run here.
    """
    if name == "patch_file":
        try:
            path = safe_subpath(workspace, str(value))
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        try:
            return Path(path).read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ValueError(
                f"patch file {path!r} not found on the host. patch_file resolves host-side inside "
                "the workspace; files under the sandbox's /tmp are not visible there — stage the "
                "diff inside the workspace."
            ) from exc
        except UnicodeDecodeError as exc:
            raise ValueError(f"patch file {path!r} is not valid UTF-8 text.") from exc
        except OSError as exc:
            raise ValueError(f"cannot read patch file {path!r}: {exc}") from exc
    patch_text, _origin = _git_diff_spec(workspace, dict(value or {}))
    return patch_text


# Host-side built-in Plan-mode plan store: the session ``plannotator_open``
# tool accepts files from here as ``open`` targets in addition to the
# workspace.
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
    def plannotator_open(
        target: str,
        kind: str = "annotate",
        gate: bool = False,
        base: str | None = None,
        description: str | None = None,  # present for human approvals of tool actions
    ) -> str:
        """Open a Plannotator review session on the host, where the human's
        browser reaches it.

        Hand the human the returned URL **verbatim** and end your turn:
        feedback is not pushed to you.  Their decision arrives as JSON at
        ``result_path``, a file you read once they say the review is in.
        Never run the ``plannotator`` CLI yourself: a session started inside
        a sandbox is unreachable by design.

        Args:
            target: For "annotate", a text-like file to mark up — markdown
                and the other formats Plannotator renders; the CLI refuses
                code, which goes through "review".  For "review", a directory
                or a PR URL: Plannotator registers a directory review at its
                repository root, so the directory is not a diff scope.
                Non-URL paths may be absolute or workspace-relative and must
                live inside the workspace or the host plan directory
                (``~/.opencode/plan``).
            kind: "annotate" (default) or "review".
            gate: Add the approval-gate UI to an annotate session.
            base: Compare ref for a review session (``--base``).
            description: Optional human-readable rationale for this call.
        """
        try:
            plan_root = os.path.realpath(os.path.expanduser(_PLAN_DIR))
            if not re.match(r"https?://", target):  # PR URLs allowed verbatim
                try:
                    target = safe_subpath(workspace, target)
                except ValueError:
                    target = safe_subpath(plan_root, target)  # plan-mode plan files
            info = plannotator_mgr.open(
                workspace, target, kind=kind, gate=gate, base=base, extra_roots=[plan_root]
            )
        except (ValueError, RuntimeError, FileNotFoundError, OSError) as exc:
            return f"plannotator_open failed: {exc}"
        lines = ["```json", _json_block(info), "```"]
        if info.get("decision_captured"):
            lines.append(
                f"The decision is already captured: read `{info['result_path']}` now. The session "
                "has ended and its page may be gone, so do not hand out its URL or wait on it."
            )
            return "\n".join(lines)
        if info.get("ended_without_decision"):
            lines.append(
                "This session ended without a decision: the CLI exited before the human submitted, "
                "the capture stays empty, and nothing further will arrive. Ask the human what they "
                "want; a session the human walked away from has to be started again."
            )
            return "\n".join(lines)
        url = info.get("url")
        if url:
            lines.append(f"Hand the human this URL **verbatim** and end your turn: {url}")
        elif info.get("err_path"):
            lines.append(
                "The session listing did not report a URL; it is the `Plannotator session ready:` "
                f"line in `{info['err_path']}`."
            )
        if info.get("source") == "discovered":
            lines.append(
                "This session was started outside tkt, so its feedback is not captured for you: ask "
                "the human what they submitted, or ask them to close the tab and let you open your "
                "own session."
            )
        else:
            if info.get("previous_ended_without_decision"):
                lines.append(
                    "A previous session on this target ended without a decision; this is a new "
                    "session for the same target."
                )
            lines.append(
                f"When the human says the review is in, read `{info['result_path']}`. While the "
                "session is still open the file is empty; an empty file for a session that is no "
                "longer running means it ended without a decision — ask the human rather than "
                "re-reading. JSON that does not parse means the session has not resolved — read it "
                "again later rather than reporting a parse error."
            )
        return "\n".join(lines)

    register_inbox_tools(mcp, workspace=workspace, patch_resolver=partial(_resolve_patch_source, workspace))


def _json_block(obj: Any) -> str:
    import json

    return json.dumps(obj, indent=2, default=str)
