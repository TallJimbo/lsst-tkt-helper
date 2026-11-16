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

"""Host-side session lifecycle managers shared by every MCP server surface.

``BrainstormManager`` (moved from ``tkt/mcp_server.py``) owns the visual
companion server; ``PlannotatorManager`` owns ``plannotator`` review sessions.
Servers they start are detached and outlive the MCP connection that spawned
them; they die by explicit close, idle timeout, the boot sweep, or ``tkt rm``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# Path of the visual-companion launcher inside this repo's pinned superpowers
# submodule, and the fallback location installed by ``tkt install-harness``.
_BRAINSTORM_SCRIPT_RELPATH = os.path.join(
    "superpowers", "skills", "brainstorming", "scripts", "start-server.sh"
)
_BRAINSTORM_SCRIPT_HOME_RELPATH = os.path.join(
    ".agents", "skills", "brainstorming", "scripts", "start-server.sh"
)
# Wall-clock bound for the launcher script (it self-bounds to ~8 s waiting for
# the server to start and prove it stays alive).
_BRAINSTORM_START_TIMEOUT_S = 30


def _find_brainstorm_script() -> str:
    """Locate ``start-server.sh`` on the host, honoring the env override.

    Raises
    ------
    FileNotFoundError
        If no candidate launcher script exists.
    """
    override = os.environ.get("TKT_BRAINSTORM_SCRIPT")
    candidates = (
        [override]
        if override
        else [
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), _BRAINSTORM_SCRIPT_RELPATH
            ),
            os.path.join(os.path.expanduser("~"), _BRAINSTORM_SCRIPT_HOME_RELPATH),
        ]
    )
    for cand in candidates:
        if cand and os.path.isfile(cand):
            return os.path.realpath(cand)
    raise FileNotFoundError(
        "brainstorm server launcher not found; tried: " + ", ".join(c for c in candidates if c)
    )


def _last_json_object(text: str) -> dict[str, Any] | None:
    """Return the last JSON-object line in ``text``, or ``None``."""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
    return None


class BrainstormManager:
    """Own the visual-companion server as a host-side process.

    The companion server talks to the agent purely through files — it serves
    the newest HTML file the agent writes into ``screen_dir`` and records
    browser clicks into ``state_dir/events`` — while only the human's browser
    needs HTTP.  Running it on the host (outside the sandbox) therefore needs
    no network bridge, is reachable from the human's browser verbatim, and
    ``--open`` can auto-open the browser again.

    Lifetime is normally bounded by the companion server's own idle timeout
    (and the boot sweep or ``tkt rm``) rather than by the MCP connection that
    spawned it, so a session survives reconnects.  ``start()`` optionally
    anchors the server's lifetime to a specific process via ``--owner-pid``,
    for callers that want it to self-exit when their process dies.
    """

    def __init__(self, *, root: str) -> None:
        self._root = os.path.realpath(root)
        self._sessions: dict[str, str] = {}  # real project dir -> state dir

    @property
    def root(self) -> str:
        return self._root

    def _resolve_project_dir(self, project_dir: str) -> str:
        """Resolve ``project_dir`` and require it inside the workspace root."""
        if os.path.isabs(project_dir):
            resolved = os.path.realpath(project_dir)
        else:
            resolved = os.path.realpath(os.path.join(self._root, project_dir))
        if resolved != self._root and not resolved.startswith(self._root + os.sep):
            raise ValueError(
                f"project_dir must be inside the workspace root ({self._root}), got {project_dir!r}."
            )
        if not os.path.isdir(resolved):
            raise ValueError(f"project_dir does not exist: {project_dir!r}.")
        return resolved

    def _scan_state_dir(self, project: str) -> str | None:
        """Return the newest session ``state`` dir under ``project``."""
        base = os.path.join(project, ".superpowers", "brainstorm")
        newest: str | None = None
        newest_mtime = 0.0
        try:
            entries = list(os.scandir(base))
        except OSError:
            return None
        for entry in entries:
            if not entry.is_dir():
                continue
            state_dir = os.path.join(entry.path, "state")
            info = os.path.join(state_dir, "server-info")
            try:
                mtime = os.path.getmtime(info)
            except OSError:
                continue
            if mtime > newest_mtime:
                newest, newest_mtime = state_dir, mtime
        return newest

    def _state_dir(self, project: str) -> str | None:
        """Return the current session state dir for ``project``, if any."""
        cached = self._sessions.get(project)
        if cached is not None and os.path.isdir(cached):
            return cached
        found = self._scan_state_dir(project)
        if found is not None:
            self._sessions[project] = found
        return found

    @staticmethod
    def _read_info(state_dir: str) -> dict[str, Any] | None:
        """Read the server's ``server-info`` JSON, or ``None``."""
        try:
            with open(os.path.join(state_dir, "server-info"), encoding="utf-8") as f:
                obj = _last_json_object(f.read())
        except OSError:
            return None
        return obj

    @staticmethod
    def _read_pid(state_dir: str) -> int | None:
        """Read the launcher's recorded server pid, or ``None``."""
        try:
            with open(os.path.join(state_dir, "server.pid"), encoding="utf-8") as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    def _is_alive(self, state_dir: str | None) -> bool:
        """Return True when a live companion server exists for this session."""
        if state_dir is None or not os.path.isdir(state_dir):
            return False
        if os.path.exists(os.path.join(state_dir, "server-stopped")):
            return False
        pid = self._read_pid(state_dir)
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def start(
        self,
        project_dir: str,
        *,
        port: int | None = None,
        idle_timeout_minutes: int | None = None,
        open_browser: bool = True,
        owner_pid: int | None = None,
    ) -> dict[str, Any]:
        """Start the companion server, or return the live session's info.

        The launcher script does the backgrounding (``nohup``/``disown``) and
        waits for the server's startup JSON, so this call returns once the
        server is confirmed up (or failed).  A still-running server for this
        project is reused rather than duplicated.

        ``owner_pid`` optionally anchors the server's lifetime to that process
        via the launcher's ``--owner-pid`` flag; when ``None`` (the default)
        the flag is omitted and the server then dies only via explicit
        close, idle timeout, the boot sweep, or ``tkt rm``.
        """
        project = self._resolve_project_dir(project_dir)
        state_dir = self._state_dir(project)
        if self._is_alive(state_dir):
            assert state_dir is not None
            info = dict(self._read_info(state_dir) or {})
            info["type"] = "server-running"
            info["reused"] = True
            return info
        env = dict(os.environ)
        if port is None:
            env.pop("BRAINSTORM_PORT", None)  # let the script reuse/choose a port
        else:
            env["BRAINSTORM_PORT"] = str(int(port))
        argv = ["bash", _find_brainstorm_script(), "--project-dir", project]
        if owner_pid is not None:
            argv += ["--owner-pid", str(int(owner_pid))]
        if idle_timeout_minutes is not None:
            argv += ["--idle-timeout-minutes", str(int(idle_timeout_minutes))]
        if open_browser:
            argv += ["--open"]
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=_BRAINSTORM_START_TIMEOUT_S, env=env, check=False
        )
        started = _last_json_object(proc.stdout)
        if started is None:
            detail = proc.stdout.strip() or proc.stderr.strip() or f"exit code {proc.returncode}"
            raise RuntimeError(f"brainstorm server failed to start: {detail}")
        if "error" in started:
            return started
        self._sessions[project] = str(started["state_dir"])
        return started

    def status(self, project_dir: str) -> dict[str, Any]:
        """Report whether a companion server is live for ``project_dir``."""
        project = self._resolve_project_dir(project_dir)
        state_dir = self._state_dir(project)
        if state_dir is None:
            return {"type": "not-running", "project_dir": project}
        info = dict(self._read_info(state_dir) or {})
        info["type"] = "server-running" if self._is_alive(state_dir) else "server-stopped"
        return info

    def stop(self, project_dir: str) -> dict[str, Any]:
        """Terminate the companion server for ``project_dir``, if any."""
        project = self._resolve_project_dir(project_dir)
        state_dir = self._state_dir(project)
        if not self._is_alive(state_dir):
            self._sessions.pop(project, None)
            return {"type": "not-running", "project_dir": project}
        assert state_dir is not None
        pid = self._read_pid(state_dir)
        info = self._read_info(state_dir) or {}
        assert pid is not None  # guaranteed by _is_alive
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                break
            time.sleep(0.1)
        self._sessions.pop(project, None)
        return {
            "type": "server-stopped",
            "port": info.get("port"),
            "state_dir": state_dir,
            "project_dir": project,
        }


def _format_brainstorm_info(info: dict[str, Any]) -> str:
    """Render a BrainstormManager result dict as agent-facing markdown."""
    body = "```json\n" + json.dumps(info, indent=2) + "\n```\n"
    kind = info.get("type")
    if kind in ("server-started", "server-running"):
        return body + (
            "Give the human the **complete** `url` verbatim, including the "
            "`?key=…` query string (requests without the key are rejected). "
            "Push a screen by writing an HTML file into `screen_dir`; the "
            "human's clicks are recorded in `state_dir/events`. Check "
            "`status` before referring to the URL again."
        )
    if kind == "not-running":
        return body + "No companion server session exists for this project yet."
    return body


_PLANNOTATOR_TIMEOUT_S = 15.0
_DISCOVER_POLL_S = 0.25
_DISCOVER_BUDGET_S = 8.0


_PLANNOTATOR_RETENTION_S = 30 * 24 * 3600
_HANDLE_SLUG_MAX = 40


def plannotator_result_dir() -> str:
    """Directory of per-session stdout captures and registry entries.

    Restricted sandbox runs bind-mount this directory read-only at the same
    path and export it as ``TKT_PLANNOTATOR_RESULTS``, so the ``result_path``
    a session tool returns is readable from inside the sandbox.
    """
    base = os.path.join(os.path.expanduser("~"), ".local", "state", "tkt", "plannotator")
    os.makedirs(base, exist_ok=True)
    return base


def _legacy_registry_dir() -> str:
    """Pre-capture registry location, removed by the launch sweep."""
    return os.path.join(os.path.expanduser("~"), ".local", "state", "tkt", "sessions")


def _registry_path(handle: str) -> str:
    return os.path.join(plannotator_result_dir(), f"{handle}.registry.json")


def _capture_path(handle: str, suffix: str) -> str:
    return os.path.join(plannotator_result_dir(), f"{handle}{suffix}")


def _handle_slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:_HANDLE_SLUG_MAX]
    return slug.strip("-") or "session"


def _claim_handle(target: str) -> str:
    """Mint and reserve a ``pna-<slug>-<epoch>`` handle for one session.

    Reservation is an ``O_EXCL`` create of the registry file, so two session
    servers opening the same target in the same second cannot end up sharing a
    handle (and therefore not one capture file).
    """
    base = f"pna-{_handle_slug(os.path.basename(target.rstrip('/')))}-{int(time.time())}"
    n = 0
    while True:
        handle = base if n == 0 else f"{base}-{n}"
        try:
            fd = os.open(_registry_path(handle), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            n += 1
            continue
        os.close(fd)
        return handle


def _registry_entries() -> dict[str, dict[str, Any]]:
    """Registry entries by handle; unreadable ones are skipped."""
    out: dict[str, dict[str, Any]] = {}
    for name in os.listdir(plannotator_result_dir()):
        if not name.endswith(".registry.json"):
            continue
        try:
            with open(os.path.join(plannotator_result_dir(), name), encoding="utf-8") as f:
                entry = json.load(f)
        except (OSError, ValueError):
            continue
        out[name[: -len(".registry.json")]] = entry
    return out


def _write_entry(handle: str, entry: dict[str, Any]) -> None:
    path = _registry_path(handle)
    # mkstemp: concurrent writers of the same handle never share one tmp
    # filename (a fixed "<path>.tmp" could interleave).
    fd, tmp = tempfile.mkstemp(dir=plannotator_result_dir(), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(entry, f)
        os.replace(tmp, path)  # atomic: parallel session servers share this directory
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _pgid_alive(pgid: int) -> bool:
    if pgid <= 0:
        return False
    try:
        # Reap our own finished children first: killpg(pgid, 0) succeeds
        # on a zombie group leader, which would make a dead CLI look alive.
        os.waitpid(pgid, os.WNOHANG)
    except ChildProcessError:
        pass  # not our child (entry written by another process) — probe anyway
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _pid_alive(pid: int) -> bool:
    """Whether ``pid`` names a live process we could signal."""
    if pid <= 0:
        return False
    try:
        # Reap our own finished children: kill(pid, 0) succeeds on a zombie,
        # which would make an exited CLI look alive forever.
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass  # not our child (entry written by another process) — probe anyway
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _sessions_listing() -> list[dict[str, Any]] | None:
    """`plannotator sessions --json`, or None when it cannot be obtained.

    None and [] mean different things to the callers: "cannot tell" versus
    "no live sessions".  Entries carry ``target``, ``url``, ``port``, ``pid``.
    """
    binary = shutil.which("plannotator")
    if binary is None:
        return None
    try:
        proc = subprocess.run(
            [binary, "sessions", "--json"],
            capture_output=True,
            text=True,
            timeout=_PLANNOTATOR_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else None


def _stderr_tail(path: str) -> str:
    """Return the last lines of a stderr capture, bounded to a few hundred
    characters.
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return "\n".join(lines[-4:])[-600:]


def _capture_is_empty(path: Any) -> bool:
    """Whether a stdout capture holds nothing (yet)."""
    if not path:
        return True
    try:
        return os.path.getsize(path) == 0
    except OSError:
        return True


def _matches(sessions: list[dict[str, Any]] | None, target: str) -> list[dict[str, Any]]:
    """Return the listing entries that are the session for ``target``,
    ordered.

    Exact target matches come first, then ``review`` entries whose
    registered directory contains the target: Plannotator normalizes a
    ``review`` of a subdirectory to the repository root before registering,
    so the listing's target can be an ancestor of what was asked for.
    ``annotate`` entries stay exact-match only.  Sessions whose target is a
    URL (a web review surface) never match, so a PR-URL review is always a
    fresh spawn.
    """
    if not sessions:
        return []
    resolved = str(Path(target).resolve())
    exact = [s for s in sessions if str(s.get("target", "")) == resolved]
    nested = [
        s
        for s in sessions
        if s.get("mode") == "review"
        and str(s.get("target", "")) != resolved
        and Path(resolved).is_relative_to(str(s.get("target", "")))
    ]
    return exact + nested


def _match(sessions: list[dict[str, Any]] | None, target: str) -> dict[str, Any] | None:
    """Return the first listing entry that is the session for ``target``,
    if any.

    This answers "is the target listed at all", including sessions whose
    registering CLI has died: the reaper paths (`_terminate`'s
    resolve-by-target fallback, `sweep_session_registry`) must keep seeing
    orphaned servers.  Attaching goes through `_live_match`, which skips
    dead registrations.
    """
    matches = _matches(sessions, target)
    return matches[0] if matches else None


def _live_match(sessions: list[dict[str, Any]] | None, target: str) -> dict[str, Any] | None:
    """Return the attachable session for ``target``: the first matching
    entry whose registering CLI is still alive, or None.

    The listing's ``pid`` is the registering CLI's process, and the CLI is
    the decision channel: a listed session whose registration is dead can
    never produce a decision, so it must not be handed back as reusable.
    Plannotator does not dedup, so a target can be listed twice (an
    orphan and a live server); selection prefers a live entry over the
    listing order.  An entry without a ``pid`` cannot be judged dead and
    stays attachable.
    """
    for s in _matches(sessions, target):
        pid = int(s.get("pid", 0) or 0)
        if pid <= 0 or _pid_alive(pid):
            return s
    return None


def _entry_ended(entry: dict[str, Any]) -> bool:
    """Whether ``entry`` is our own spawned session whose CLI died with
    nothing captured: no decision can arrive through it anymore.
    """
    return (
        entry.get("source", "spawned") == "spawned"
        and not _pgid_alive(int(entry.get("pgid", 0) or 0))
        and _capture_is_empty(entry.get("out_path"))
    )


def _listed_target(entry: dict[str, Any]) -> str:
    """Return the target to look up in `sessions --json` for ``entry``: the
    one the listing registered, falling back to the one that was asked for.
    """
    return str(entry.get("listed_target") or entry.get("target", ""))


def _terminate(entry: dict[str, Any], listed: list[dict[str, Any]] | None) -> None:
    """TERM the CLI process group and the session server recorded in ``entry``.

    The CLI's own process group never contains the session server: Plannotator
    spawns it outside the CLI's group, so killing the CLI leaves the browser
    session serving.  An entry with no recorded server pid (its URL came from
    the stderr ready line before the CLI registered the session) gets its
    server from ``listed``, the current `sessions --json` output, matched on
    the target that entry registered.  A pid the listing does not confirm is
    never signalled, which is what keeps pid reuse from making us hit an
    unrelated process; with no recorded pid and nothing listed, there is
    nothing to signal.
    """
    pgid = int(entry.get("pgid", 0) or 0)
    if pgid > 0:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    server_pid = int(entry.get("server_pid", 0) or 0)
    if server_pid <= 0:
        live = _match(listed, _listed_target(entry))
        if live is None:
            return
        server_pid = int(live.get("pid", 0) or 0)
    if server_pid <= 0:
        return
    if listed is not None and not any(int(s.get("pid", -1) or -1) == server_pid for s in listed):
        return
    try:
        os.kill(server_pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def sweep_session_registry() -> int:
    """Launch-time hygiene; never kills anything.

    Drops registry entries whose CLI, session server, and listed session are
    all gone (their captures stay: they are the record of what the human
    submitted, and the retention pass below ages them out), prunes captures
    past the retention window, and removes the pre-capture registry directory.
    An entry with no recorded server pid is live while `sessions --json`
    still shows the target it registered, and an unusable listing never
    makes any entry look dead.  A live session server outlives its sandbox
    on purpose — the human may still be reviewing.
    """
    shutil.rmtree(_legacy_registry_dir(), ignore_errors=True)
    removed = 0
    listed: list[dict[str, Any]] | None = None
    listed_fetched = False
    for handle, entry in _registry_entries().items():
        if _pgid_alive(int(entry.get("pgid", 0) or 0)):
            continue
        server_pid = int(entry.get("server_pid", 0) or 0)
        if server_pid > 0:
            if _pid_alive(server_pid):
                continue
        else:
            if not listed_fetched:
                listed, listed_fetched = _sessions_listing(), True
            if listed is None or _match(listed, _listed_target(entry)) is not None:
                continue
        try:
            os.unlink(_registry_path(handle))
            removed += 1
        except OSError:
            continue
    now = time.time()
    for name in os.listdir(plannotator_result_dir()):
        if not name.endswith((".out", ".err")):
            continue
        full = os.path.join(plannotator_result_dir(), name)
        try:
            if now - os.path.getmtime(full) > _PLANNOTATOR_RETENTION_S:
                os.unlink(full)
        except OSError:
            continue
    return removed


def cleanup_workspace_sessions(workspace: str) -> int:
    """``tkt rm`` hook: tear down sessions opened through ``workspace``.

    Terminates each registered CLI process group and its session server, then
    drops the registry entries. Captures stay on disk.  A session the human
    started themselves is registered too (``pgid`` 0), so its abandoned server
    is reaped with the workspace.
    """
    ws = str(Path(workspace).resolve())
    listed = _sessions_listing()
    removed = 0
    for handle, entry in _registry_entries().items():
        if entry.get("workspace") != ws:
            continue
        _terminate(entry, listed)
        try:
            os.unlink(_registry_path(handle))
        except OSError:
            continue
        removed += 1
    return removed


class PlannotatorManager:
    """Host-side lifecycle for ``plannotator`` review sessions.

    A session's result is the CLI's stdout, captured to a file when the session
    is spawned and left for the agent to read: restricted sandboxes mount
    ``plannotator_result_dir()`` read-only.  Plannotator's own storage is never
    read.
    """

    _READY_RE = re.compile(r"Plannotator session ready:\s*(\S+)")

    def _binary(self) -> str:
        binary = shutil.which("plannotator")
        if binary is None:
            raise RuntimeError(
                "plannotator binary not found on the host PATH; install Plannotator "
                "on the host to use review tools from the sandbox."
            )
        return binary

    def open(
        self,
        workspace: str,
        target: str,
        *,
        kind: str = "annotate",
        gate: bool = False,
        base: str | None = None,
        extra_roots: Sequence[str] = (),
    ) -> dict[str, Any]:
        workspace = str(Path(workspace).resolve())
        if kind == "review" and re.match(r"https?://", target):
            resolved = target  # PR URLs allowed verbatim
        else:
            resolved = str(Path(target).resolve())
            roots = [workspace, *(str(Path(root).resolve()) for root in extra_roots)]
            if not any(resolved == root or resolved.startswith(root + os.sep) for root in roots):
                raise ValueError(f"review target {target!r} is outside workspace {workspace!r}.")
        listed = _sessions_listing()
        live = _live_match(listed, resolved)
        if live is None:
            # A dead-pid listing match whose own entry already holds the
            # decision is still the session for the target: hand that
            # decision back rather than spawning over a captured review.
            # The listing says which entry that is — another entry's
            # decision is no reason to withhold a spawn when the listed
            # entry is an ended orphan.
            dead = _match(listed, resolved)
            if dead is not None:
                server_pid = int(dead.get("pid", -1) or -1)
                own = next(
                    (
                        e
                        for e in _registry_entries().values()
                        if e.get("workspace") == workspace
                        and e.get("target") == resolved
                        and int(e.get("server_pid", -1) or -1) == server_pid
                        and not _capture_is_empty(e.get("out_path"))
                    ),
                    None,
                )
                if own is not None:
                    live = dead
        if live is not None:
            return self._attach(workspace, resolved, live, kind=kind, gate=gate, base=base)
        ours = [
            e
            for e in _registry_entries().values()
            if e.get("workspace") == workspace
            and e.get("target") == resolved
            and e.get("source", "spawned") == "spawned"
        ]
        ended = any(_entry_ended(e) for e in ours) or bool(_matches(listed, resolved))
        result = self._spawn(workspace, resolved, kind=kind, gate=gate, base=base)
        if ended:
            # A prior session for this target died without submitting; say
            # so, so a second session on the same target is not a mystery.
            # The old entries stay for workspace cleanup, which signals the
            # orphan server; the launch sweep may drop them first, after
            # which a re-open simply spawns.
            result["previous_ended_without_decision"] = True
        return result

    def _attach(
        self,
        workspace: str,
        target: str,
        live: dict[str, Any],
        *,
        kind: str,
        gate: bool,
        base: str | None,
    ) -> dict[str, Any]:
        """Reuse the live session for ``target`` (Plannotator does not dedup).

        A session we spawned owns a capture file, so the same ``result_path``
        comes back — including after a sandbox restart, with
        ``decision_captured`` set once that capture holds the decision.  A
        session whose CLI is gone with a still-empty capture can never
        deliver a decision (the human closed the tab, or the CLI died); its
        entry reports that plainly rather than offering a file that will stay
        empty.  A session the human started from their own terminal has no
        capture we can read; it is registered anyway so workspace removal can
        reap its server.
        """
        server_pid = int(live.get("pid", -1) or -1)
        entries = _registry_entries()
        handle = next(
            (
                h
                for h, e in entries.items()
                if e.get("workspace") == workspace
                and e.get("target") == target
                and int(e.get("server_pid", -1) or -1) == server_pid
            ),
            None,
        )
        if handle is None:
            # An entry whose URL came from the stderr ready line carries no
            # server pid to match; a live CLI process group for this target
            # can only be one this layer started, and it rules out entries
            # left by earlier, finished sessions on the same target.
            handle = next(
                (
                    h
                    for h, e in entries.items()
                    if e.get("workspace") == workspace
                    and e.get("target") == target
                    and _pgid_alive(int(e.get("pgid", 0) or 0))
                ),
                None,
            )
        if handle is not None:
            entry = entries[handle]
            source = entry.get("source", "spawned")
            if _entry_ended(entry):
                return {
                    "type": "open",
                    "handle": handle,
                    "url": None,
                    "result_path": entry.get("out_path"),
                    "err_path": entry.get("err_path"),
                    "reused": False,
                    "source": source,
                    "ended_without_decision": True,
                }
            return {
                "type": "open",
                "handle": handle,
                "url": live.get("url"),
                "result_path": entry.get("out_path") if source == "spawned" else None,
                "err_path": entry.get("err_path"),
                "reused": True,
                "source": source,
                "decision_captured": source == "spawned" and not _capture_is_empty(entry.get("out_path")),
            }
        handle = _claim_handle(target)
        _write_entry(
            handle,
            {
                "handle": handle,
                "workspace": workspace,
                "target": target,
                "kind": kind,
                "gate": gate,
                "base": base,
                "opened_at": time.time(),
                "url": live.get("url"),
                "source": "discovered",
                "pgid": 0,
                "server_pid": server_pid,
                "listed_target": live.get("target"),
            },
        )
        return {
            "type": "open",
            "handle": handle,
            "url": live.get("url"),
            "result_path": None,
            "reused": True,
            "source": "discovered",
        }

    def _spawn(
        self,
        workspace: str,
        target: str,
        *,
        kind: str,
        gate: bool,
        base: str | None,
    ) -> dict[str, Any]:
        binary = self._binary()
        handle = _claim_handle(target)
        out_path = _capture_path(handle, ".out")
        err_path = _capture_path(handle, ".err")
        if kind == "review":
            args = [binary, "review", target, "--json"]
            if base is not None:
                args += ["--base", base]
        else:
            args = [binary, "annotate", target, "--json"]
            if gate:
                args += ["--gate"]
        # The decision JSON reaches us on stdout when the human resolves the
        # session, so stdout is a file the agent can read later rather than a
        # pipe that closes with this connection. start_new_session detaches the
        # CLI from the transient session server's process group.
        with open(out_path, "wb") as out, open(err_path, "wb") as err:
            proc = subprocess.Popen(args, start_new_session=True, stdout=out, stderr=err)
        try:
            url, server_pid, listed_target = self._discover(target, err_path, proc)
        except RuntimeError:
            # A spawn that never opened a session is not a session waiting on
            # a human: drop the handle and its captures so nothing here looks
            # like one, and let the failure (with the CLI's advice) surface.
            for path in (out_path, err_path, _registry_path(handle)):
                try:
                    os.unlink(path)
                except OSError:
                    pass
            raise
        _write_entry(
            handle,
            {
                "handle": handle,
                "workspace": workspace,
                "target": target,
                "kind": kind,
                "gate": gate,
                "base": base,
                "opened_at": time.time(),
                "out_path": out_path,
                "err_path": err_path,
                "url": url,
                "source": "spawned",
                "pgid": proc.pid,
                "server_pid": server_pid,
                "listed_target": listed_target,
            },
        )
        return {
            "type": "open",
            "handle": handle,
            "url": url,
            "result_path": out_path,
            "err_path": err_path,
            "reused": False,
            "source": "spawned",
        }

    def _discover(
        self, target: str, err_path: str, proc: subprocess.Popen[bytes]
    ) -> tuple[str | None, int | None, str | None]:
        """Poll for the session's URL and, from the listing, its server pid
        and registered target.

        Each poll checks `sessions --json` first, then the CLI's stderr file:
        a PR-URL review's target never matches the listing, so its URL comes
        from the ``Plannotator session ready:`` line alone, and reading the
        line per-poll keeps that lookup off the budget's critical path.  The
        listing path accepts only this CLI's own registration — the listing
        ``pid`` is the registering CLI's process, so ``pid == proc.pid`` means
        the entry describes the spawn in flight, not an orphan of an earlier
        session on the same target.

        A CLI that exits without ever registering or printing the ready line
        opened no session — that is a failed spawn, not a slow one, and the
        reason lives in the stderr file.
        """
        deadline = time.monotonic() + _DISCOVER_BUDGET_S
        while True:
            mine = next(
                (s for s in _matches(_sessions_listing(), target) if int(s.get("pid", 0) or 0) == proc.pid),
                None,
            )
            if mine is not None:
                registered = mine.get("target")
                listed = registered if isinstance(registered, str) else None
                return str(mine.get("url")), proc.pid, listed
            try:
                with open(err_path, encoding="utf-8", errors="replace") as f:
                    ready = self._READY_RE.search(f.read())
            except OSError:
                ready = None
            if ready is not None:
                return ready.group(1), None, None
            if proc.poll() is not None:
                raise RuntimeError(
                    f"plannotator exited without opening a session; CLI stderr: {_stderr_tail(err_path)!r}"
                )
            if time.monotonic() >= deadline:
                return None, None, None
            time.sleep(_DISCOVER_POLL_S)
