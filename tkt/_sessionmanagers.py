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
from datetime import datetime
from pathlib import Path
from typing import Any

# Path of the visual-companion launcher inside this repo's pinned superpowers
# submodule, and the fallback location installed by ``tkt install-zed-agent``.
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


def session_registry_dir() -> str:
    """Directory of per-session pidfiles (``<session-id>.json``)."""
    base = os.path.join(os.path.expanduser("~"), ".local", "state", "tkt", "sessions")
    os.makedirs(base, exist_ok=True)
    return base


def _registry_entries() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name in os.listdir(session_registry_dir()):
        if name.endswith(".json"):
            try:
                with open(os.path.join(session_registry_dir(), name), encoding="utf-8") as f:
                    entry = json.load(f)
                out[name[: -len(".json")]] = entry
            except (OSError, ValueError):
                continue
    return out


def _pgid_alive(pgid: int) -> bool:
    if pgid > 0:
        try:
            # Reap our own finished children first: killpg(pgid, 0) succeeds
            # on a zombie group leader, which would make a dismissed session
            # look alive (result() "pending") forever.
            os.waitpid(pgid, os.WNOHANG)
        except ChildProcessError:
            pass  # not our child (entry written by another process) — probe anyway
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _write_entry(session_id: str, entry: dict[str, Any]) -> None:
    path = os.path.join(session_registry_dir(), f"{session_id}.json")
    # mkstemp: concurrent writers of the same session id never share one tmp
    # filename (a fixed "<path>.tmp" could interleave).
    fd, tmp = tempfile.mkstemp(dir=session_registry_dir(), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(entry, f)
        os.replace(tmp, path)  # atomic: parallel servers share this registry
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _status_ts(entry: dict[str, Any]) -> float:
    """Best-effort epoch seconds for sorting: ``startedAt`` (CLI) or
    ``opened_at`` (registry).

    Numbers pass through; strings parse as ISO-8601 (a trailing ``Z`` is
    understood; a naive string is interpreted in the host's local zone).
    0.0 when neither key is present or either parses.
    """
    for key in ("startedAt", "opened_at"):
        val = entry.get(key)
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            try:
                return datetime.fromisoformat(val).timestamp()
            except ValueError:
                continue
    return 0.0


def sweep_session_registry() -> int:
    """Drop registry entries whose process group is dead.  Never kills
    anything: live session servers intentionally outlive sandboxes.
    """
    removed = 0
    for sid, entry in _registry_entries().items():
        if not _pgid_alive(int(entry.get("pgid", -1))):
            try:
                os.unlink(os.path.join(session_registry_dir(), f"{sid}.json"))
                removed += 1
            except OSError:
                continue
    return removed


def cleanup_workspace_sessions(workspace: str) -> int:
    """Kill (process group) sessions we spawned; unregister discovered ones
    (pgid <= 0: killpg(0) would signal our own group, killpg(-1) broadcasts).
    Returns the number of registry entries removed.
    """
    ws = str(Path(workspace).resolve())
    removed = 0
    for sid, entry in _registry_entries().items():
        if entry.get("workspace") != ws:
            continue
        pgid = int(entry.get("pgid", 0))
        if pgid > 0:
            try:
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            os.unlink(os.path.join(session_registry_dir(), f"{sid}.json"))
        except OSError:
            pass
        removed += 1
    return removed


class PlannotatorManager:
    """Host-side lifecycle + results for ``plannotator`` review sessions."""

    def _binary(self) -> str:
        binary = shutil.which("plannotator")
        if binary is None:
            raise RuntimeError(
                "plannotator binary not found on the host PATH; install Plannotator "
                "on the host to use review tools from the sandbox."
            )
        return binary

    def _sessions(self) -> list[dict[str, Any]]:
        """Parse `plannotator sessions --json`; tolerate unknown keys and
        CLI absence.
        """
        try:
            proc = subprocess.run(
                [self._binary(), "sessions", "--json"],
                capture_output=True,
                text=True,
                timeout=_PLANNOTATOR_TIMEOUT_S,
                check=False,
            )
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            return []
        try:
            data = json.loads(proc.stdout)
        except ValueError:
            return []
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []

    def _match(self, sessions: list[dict[str, Any]], target: str) -> dict[str, Any] | None:
        """Find the live session whose target resolves to `target`.

        Matching is by resolved absolute target only: sessions whose target
        is a URL (a web review surface) are intentionally never matched —
        that surface is out of use by decision, so this stays
        target-absolute-path only rather than guessing a URL shape.
        """
        for s in sessions:
            if str(s.get("target", "")) == str(Path(target).resolve()):
                return s
        return None

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
        live = self._match(self._sessions(), resolved)
        if live is not None:
            sid = str(live.get("id") or "pn-unknown")
            entries = _registry_entries()
            prior = entries.get(sid, {})
            pgid = int(prior.get("pgid", 0) or 0)
            opened_at = float(prior.get("opened_at", time.time()))
            if pgid <= 0:
                # Late-registration race: our own spawn may still be keyed by
                # its local-<pid> id. Adopt that entry (pgid + opened_at) so
                # close() stays effective on the session we started.
                for osid, oent in entries.items():
                    if (
                        osid.startswith("local-")
                        and oent.get("workspace") == workspace
                        and oent.get("target") == resolved
                        and int(oent.get("pgid", 0) or 0) > 0
                    ):
                        pgid = int(oent["pgid"])
                        opened_at = float(oent.get("opened_at", opened_at))
                        try:
                            os.unlink(os.path.join(session_registry_dir(), f"{osid}.json"))
                        except OSError:
                            pass
                        break
            if prior.get("workspace") != workspace or pgid > 0:
                # Register (or re-home) discovered sessions: an entry must
                # exist for result()/close() to work on attach-or-start reuse.
                _write_entry(
                    sid,
                    {
                        "pgid": pgid,  # keep our prior spawn's pgid when we started it
                        "workspace": workspace,
                        "target": resolved,
                        "kind": prior.get("kind", kind),
                        "opened_at": opened_at,
                        "source": "spawned" if pgid > 0 else "discovered",
                    },
                )
            return {"type": "open", "url": live.get("url"), "session": sid, "reused": True}
        binary = self._binary()
        if kind == "review":
            args = [binary, "review", target]
            if base is not None:
                args += ["--base", base]
        else:
            args = [binary, "annotate", target]
            if gate:
                args += ["--gate"]
        # Anchored spawn: start_new_session=True detaches the
        # session from our (transient) connection's process group; close()
        # tears it down with killpg, which reaches every group member. The
        # pre-exec trap only covers the exec-failure window — POSIX resets
        # traps across exec, so it cannot guard the exec'd CLI itself.
        wrapper = ["sh", "-c", 'trap "kill -TERM 0" TERM INT; exec "$@"', "tkt-plannotator", *args]
        proc = subprocess.Popen(
            wrapper,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        session_id, url = "", None
        deadline = time.monotonic() + _DISCOVER_BUDGET_S
        while time.monotonic() < deadline:
            live = self._match(self._sessions(), resolved)
            if live is not None:
                session_id, url = str(live.get("id", "")), live.get("url")
                break
            time.sleep(_DISCOVER_POLL_S)
        session_id = session_id or f"local-{proc.pid}"
        entry = {
            "pgid": proc.pid,
            "workspace": workspace,
            "target": resolved,
            "kind": kind,
            "opened_at": time.time(),
            "source": "spawned",
        }
        _write_entry(session_id, entry)
        return {"type": "open", "url": url, "session": session_id, "reused": False}

    def status(self, workspace: str) -> list[dict[str, Any]]:
        """List this workspace's sessions (agents pull ``result`` per id).

        Returns the union of registry entries owned by the workspace and
        CLI-listed sessions matching it.  Every entry tied to a registry
        key carries ``id``: the exact key ``result()``/``close()`` accept.
        A registry entry still listed by the CLI is merged with that
        session's live fields (``url``, ``port``, ``pid``, ...) and marked
        ``state="live"``; a registry entry missing from the CLI list — a
        submitted review, which vanishes from ``sessions --json`` while
        ``result()`` still returns its feedback — is returned with
        ``state="closed"``.  CLI-listed sessions with no registry match
        (host-started, unregistered) pass through unchanged: they
        legitimately have no id: ``result``/``close`` routing covers
        registered (agent-started) sessions.  Live entries sort first, then
        newest-first by ``startedAt``/``opened_at``.
        """
        ws = str(Path(workspace).resolve())
        entries = {sid: e for sid, e in _registry_entries().items() if e.get("workspace") == ws}
        claimed: set[str] = set()
        out: list[dict[str, Any]] = []
        for s in self._sessions():
            tgt = str(s.get("target", ""))
            # Newest registry entry wins when an old closed one shares the
            # target with the current live session.
            sid = next(
                (
                    k
                    for k, e in sorted(
                        entries.items(),
                        key=lambda kv: _status_ts(kv[1]),
                        reverse=True,
                    )
                    if e.get("target") == tgt
                ),
                None,
            )
            if sid is None and not (tgt.startswith(ws + os.sep) or tgt == ws):
                continue  # not our workspace, and not registered by us
            if sid is not None:
                claimed.add(sid)
                merged = dict(s)
                merged["id"] = sid
                merged["state"] = "live"
                out.append(merged)
            else:
                out.append(dict(s))
        for sid, e in entries.items():
            if sid in claimed:
                continue
            out.append(
                {
                    "id": sid,
                    "target": e.get("target"),
                    "kind": e.get("kind"),
                    "state": "closed",
                    "startedAt": e.get("opened_at"),
                }
            )
        out.sort(key=lambda e: (e.get("state") != "live", -_status_ts(e)))
        return out

    def result(self, workspace: str, session: str) -> dict[str, Any]:
        entries = _registry_entries()
        entry = entries.get(session)
        if entry is None or entry.get("workspace") != str(Path(workspace).resolve()):
            raise ValueError(f"unknown session {session!r} for workspace {workspace!r}.")
        proj_dir = Path(os.environ.get("PLANNOTATOR_DATA_DIR", os.path.expanduser("~/.plannotator")))
        # Plannotator keys its feedback store by a target-derived project
        # label (a package dir under a multi-package workspace lands its
        # records under feedback/<package>/records/), not by tkt's workspace
        # name — so glob the target-derived directory first and fall back to
        # the workspace-named one (the two coincide in a single-repo
        # workspace).  Records are still keyed by Plannotator's own ids/
        # targets, not our registry id, so the per-session filter stays
        # time-based (opened_at) + the two record patterns. `pn-*` registry
        # ids matching a record name win when several records are fresh.
        target = entry.get("target")
        candidates: list[str] = []
        for raw in (target, entry["workspace"]):
            name = Path(str(raw)).name if isinstance(raw, str) and raw else ""
            if name and name not in candidates:
                candidates.append(name)
        patterns = ("*-annotate-feedback.md", "*-review-*")
        records = sorted(
            {
                p
                for name in candidates
                for pat in patterns
                for p in proj_dir.glob(f"feedback/{name}/records/{pat}")
            },
            key=lambda p: p.stat().st_mtime,
        )
        fresh = [p for p in records if p.stat().st_mtime > float(entry["opened_at"])]
        if session.startswith("pn-"):
            preferred = [p for p in fresh if session in p.name]
            fresh = preferred or fresh
        if fresh:
            return {"type": "feedback", "text": fresh[-1].read_text(encoding="utf-8")}
        pgid = int(entry.get("pgid", 0))
        if pgid > 0:
            alive = _pgid_alive(pgid)
        else:
            # Discovered (host-started) session: CLI truth decides — still
            # listed in `sessions --json` means pending, gone means closed.
            alive = any(str(s.get("id", "")) == session for s in self._sessions())
        return {"type": "pending"} if alive else {"type": "closed", "text": ""}

    def close(self, workspace: str, session: str) -> dict[str, Any]:
        entries = _registry_entries()
        entry = entries.get(session)
        if entry is None or entry.get("workspace") != str(Path(workspace).resolve()):
            raise ValueError(f"unknown session {session!r} for workspace {workspace!r}.")
        pgid = int(entry.get("pgid", 0))
        if pgid > 0:
            try:
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            outcome = "closed"
        else:
            outcome = "unregistered"  # host-started: drop our registration only
        try:
            os.unlink(os.path.join(session_registry_dir(), f"{session}.json"))
        except OSError:
            pass
        return {"type": outcome, "session": session}
