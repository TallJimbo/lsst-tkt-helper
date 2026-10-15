# Host-Side Session Services & the Sandbox MCP Boundary — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the visual-companion and Plannotator session servers onto the host, reached through a per-launch unix-socket MCP bridge, deleting the exclusive sandbox→host TCP bridge so N restricted sandboxes coexist.

**Architecture:** `Sandbox.run` spawns (in the existing bridge block) a `socat UNIX-LISTEN:net-*/session.sock,fork SYSTEM:"python -m tkt.session_server --workspace …"` listener; sandboxed OpenCode gets the `tkt-session` MCP server via injected `OPENCODE_CONFIG_CONTENT`. One host-side managers module backs both this server and the existing Zed MCP server. The `vc_port` reverse bridge is deleted.

**Tech Stack:** Python 3.13, `mcp` (FastMCP), `socat`/`bwrap`, pytest, ruff/mypy.

**Spec:** `docs/superpowers/specs/2026-10-12-host-session-services-design.md` — read it alongside this plan; it carries the decisions (numbered §2.1–§2.9) and the probes (P1–P3).

**Base branch:** `main` (post-jsonc `8f2e187`). Reference branch (read-only, never merge/rebase onto it): `feature/plannotator-integration` (repo) and its superpowers submodule branch at `76cc115`.

## Global Constraints

- Python 3.13; ruff line-length 110, doc-length 79, numpy docstring convention; `mypy tkt/` clean; `ruff check . && ruff format --check .` clean.
- Every new `.py` file carries the BSD-3-Clause header copied verbatim from an existing file (e.g., `tkt/utils.py` lines 1–26).
- No new dependencies (the `mcp` SDK is already used by `tkt/mcp_server.py`). No packaging config (no setup/pyproject changes for deps).
- Never `git add -A`: the `superpowers` submodule gitlink is deliberately dirty at times; stage explicit paths only.
- Gates (`ruff check . && ruff format --check . && mypy tkt/ && python -m pytest tests/ -q`) green at every commit; full suite ~286 tests, one known conda forkpty warning is acceptable.
- Tests must never touch the real `~/.plannotator` (always `PLANNOTATOR_DATA_DIR`) or bind standard ports (use the existing 18089-style ephemeral choices).
- Commit messages: `feat:`/`refactor:`/`test:`/`docs:` conventional style, as on `main`.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `tkt/_sessionmanagers.py` | Create. Host-side session lifecycle truth: `BrainstormManager` (moved) + `PlannotatorManager` + pidfile registry + sweeps. | 2, 3 |
| `tkt/_session_tools.py` | Create. `build_session_tools(mcp, *, workspace)` — registers `brainstorm_server`, `plannotator`, `plannotator_inbox` on any FastMCP; path containment; shared formatting. | 4, 5 |
| `tkt/session_server.py` | Create. `python -m tkt.session_server --workspace DIR`: `FastMCP("tkt-session")` over stdio; session tools only. | 4 |
| `tkt/mcp_server.py` | Edit. Delete moved brainstorm code; register Zed's session tools via the shared builder. | 4 |
| `tkt/_cli.py` | Edit. New `tkt session-server` command; extend stale-bridge cleanup command to also sweep session registry. | 4, 6 |
| `tkt/sandbox.py` | Edit. Session listener spawn + env injection; delete `vc_port` plumbing, vc forwarders (host + in-sandbox); `Sandbox.remove` cleanup; `render_agents_md` drops `vc_port`. | 6, 7 |
| `tkt/AGENTS.md.in` | Edit. Unified host-session-tool guidance; remove self-launch fallback and `{{vc_port}}`. | 7 |
| `AGENTS.md` (repo) | Edit. Sandbox bullet: drop `{{vc_port}}`, describe the session-socket bridge. | 7 |
| `harnesses/opencode/plugin/plannotator-wake.js` | Create. Wake layer: polls the session socket, injects `Plannotator:` messages in-process. | 8 |
| `tests/test_session_managers.py` | Create. Managers tests with fake-CLI fixtures. | 2, 3 |
| `tests/test_session_tools.py` | Create. Tool-handler, guard, containment, inbox-proxy tests. | 4, 5 |
| `tests/test_sandbox.py` | Edit. Replace vc_port tests; add session-listener/env-injection assertions. | 6, 7 |
| `tests/test_wake_plugin_client.py` | Create. Node smoke of the plugin's pure socket-client half (skips without node). | 8 |

---

### Task 1: Branch + commit spec and plan docs

**Files:**

- Create branch `feature/host-session-services` from `main`.
- Commit: `docs/superpowers/specs/2026-10-12-host-session-services-design.md`, `docs/superpowers/plans/2026-10-12-host-session-services.md`

**Interfaces:**

- Consumes: nothing.
- Produces: the branch every later task commits on; docs committed first (AGENTS.md workflow).

- [ ] **Step 1: Verify clean base**

```bash
git status --porcelain   # expect: only possibly " M superpowers" (leave it)
git log --oneline -1     # expect: 8f2e187 (jsonc) or a descendant on main
git checkout -b feature/host-session-services
```

- [ ] **Step 2: Commit the docs**

```bash
git add docs/superpowers/specs/2026-10-12-host-session-services-design.md \
        docs/superpowers/plans/2026-10-12-host-session-services.md
git commit -m "docs: spec and plan for host-side session services"
```

---

### Task 2: Move `BrainstormManager` into `tkt/_sessionmanagers.py`

Pure move so the host session server (Task 4) and Zed's server share one
implementation (spec §3.1). Also adds the `owner_pid` parameter (decision:
omit `--owner-pid` from the shared tool builder so companion servers follow the
spec §4 death paths — explicit close / idle timeout / sweep / rm — rather than
dying with the connecting client; the 240-min idle default bounds orphans).

**Files:**

- Create: `tkt/_sessionmanagers.py`
- Modify: `tkt/mcp_server.py` (delete lines 721–973 region: `_BRAINSTORM_SCRIPT_RELPATH`, `_BRAINSTORM_SCRIPT_HOME_RELPATH`, `_BRAINSTORM_START_TIMEOUT_S`, `_find_brainstorm_script`, `_last_json_object`, `class BrainstormManager`, `_format_brainstorm_info`; import from the new module instead)
- Test: `tests/test_session_managers.py` (create; move/adapt any brainstorm tests, then add the move-check test)

**Interfaces:**

- Produces: `tkt._sessionmanagers.BrainstormManager` with
  `start(self, project_dir: str, *, port: int | None = None, idle_timeout_minutes: int | None = None, open_browser: bool = True, owner_pid: int | None = None) -> dict[str, Any]`
  (new kwarg; `None` ⇒ the `--owner-pid` flag is **omitted**), `status(project_dir)`, `stop(project_dir)`, and `_format_brainstorm_info(info) -> str`.
- Consumes by later tasks: same names.

- [ ] **Step 1: Write the move-check test (fails first)**

```python
# tests/test_session_managers.py
"""Tests for the shared host-side session managers."""

from __future__ import annotations


def test_brainstorm_manager_moved() -> None:
    from tkt import _sessionmanagers, mcp_server

    assert _sessionmanagers.BrainstormManager is mcp_server.BrainstormManager
```

- [ ] **Step 2: Run it, verify ImportError/AttributeError failure**

Run: `python -m pytest tests/test_session_managers.py -q` → FAIL.

- [ ] **Step 3: Move the code**

Create `tkt/_sessionmanagers.py` with the BSD-3 header (copy from `tkt/utils.py`),
then move verbatim, adding only the `owner_pid` kwarg:

```python
"""Host-side session lifecycle managers shared by every MCP server surface.

``BrainstormManager`` (moved from ``tkt/mcp_server.py``) owns the visual
companion server; ``PlannotatorManager`` owns ``plannotator`` review sessions.
Servers they start are detached and outlive the MCP connection that spawned
them; they die by explicit close, idle timeout, the boot sweep, or ``tkt rm``.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from typing import Any
```

(plus `json`, `re`, `sys`, `tempfile`, `shutil` as needed by the moved code).
In `start()`, replace the fixed `--owner-pid` argv append with:

```python
        argv = ["bash", _find_brainstorm_script(), "--project-dir", project]
        if owner_pid is not None:
            argv += ["--owner-pid", str(int(owner_pid))]
```

and update its docstring accordingly. In `tkt/mcp_server.py` delete the moved
region and add `from ._sessionmanagers import BrainstormManager, _format_brainstorm_info`
(keep `BrainstormManager` re-exported under the same name so
`mcp_server.BrainstormManager` still resolves — Task 4 removes the local
`brainstorm_server` tool definition; do **not** touch it in this task).

- [ ] **Step 4: Run the full suite, verify green**

Run: `python -m pytest tests/ -q && ruff check . && ruff format --check . && mypy tkt/` → PASS (suite count unchanged).

- [ ] **Step 5: Commit**

```bash
git add tkt/_sessionmanagers.py tkt/mcp_server.py tests/test_session_managers.py
git commit -m "refactor: move BrainstormManager into shared _sessionmanagers module"
```

---

### Task 3: `PlannotatorManager` + pidfile registry + sweeps

TDD against a **fake `plannotator` CLI** (shell-script fixture) with
`PLANNOTATOR_DATA_DIR` relocated to tmp_path — the real archive is never
touched (Global Constraint). Shapes follow spec §3.1 and the P2 probe's
*expected* keys; the manager stays lenient (`.get`-style) so the real CLI can
only narrow, not break, these tests.

**Files:**

- Modify: `tkt/_sessionmanagers.py`
- Test: `tests/test_session_managers.py`

**Interfaces:**

- Consumes: nothing new.
- Produces:

```python
class PlannotatorManager:
    def open(self, workspace: str, target: str, *, kind: str = "annotate",
             gate: bool = False, base: str | None = None) -> dict[str, Any]: ...
    def status(self, workspace: str) -> list[dict[str, Any]]: ...
    def result(self, workspace: str, session: str) -> dict[str, Any]: ...
    def close(self, workspace: str, session: str) -> dict[str, Any]: ...

def session_registry_dir() -> str:              # ~/.local/state/tkt/sessions (respects $HOME)
def sweep_session_registry() -> int:            # drop entries whose pgid is dead; never kills live
def cleanup_workspace_sessions(workspace: str) -> int:  # killpg (spawned only) + drop entries
```

- [ ] **Step 1: Write the fake CLI fixture + failing tests**

```python
# tests/test_session_managers.py — append

import json
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from tkt._sessionmanagers import (
    PlannotatorManager,
    cleanup_workspace_sessions,
    session_registry_dir,
    sweep_session_registry,
)

FAKE_CLI = """#!/usr/bin/env bash
# Fake plannotator for tests. State lives in $FAKE_STATE.
cmd="$1"; shift
case "$cmd" in
  sessions)
    if [ -f "$FAKE_STATE/sessions.json" ]; then cat "$FAKE_STATE/sessions.json"; else echo "[]"; fi
    ;;
  annotate|review)
    # A well-behaved server cleans up its own group when TERMed. Needed here
    # because the sweep test signals only the leader; production close() uses
    # killpg, which reaches every group member regardless of any trap.
    trap 'kill -TERM 0; exit 143' TERM INT
    echo '{"launched": true}'
    mkdir -p "$FAKE_STATE"; sleep "${FAKE_SLEEP:-60}" &   # stand-in for the blocking session
    echo $! > "$FAKE_STATE/sleep.pid"; wait
    ;;
  *) echo "unknown: $cmd" >&2; exit 2 ;;
esac
"""


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    """Fake `plannotator` on PATH, data dir + registry relocated to tmp_path."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cli = bin_dir / "plannotator"
    cli.write_text(FAKE_CLI)
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PLANNOTATOR_DATA_DIR", str(tmp_path / "plannotator-data"))
    monkeypatch.setenv("HOME", str(tmp_path))  # session_registry_dir lands here
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("FAKE_STATE", str(state))
    return tmp_path


def _sessions_json(tmp_path, entries):
    (tmp_path / "state" / "sessions.json").write_text(json.dumps(entries))


def _touch_record(tmp_path, workspace, name, *, age=0.0):
    rec_dir = tmp_path / "plannotator-data" / "feedback" / Path(workspace).name / "records"
    rec_dir.mkdir(parents=True, exist_ok=True)
    rec = rec_dir / name
    rec.write_text("Reviewer wanted X changed.")
    stamp = time.time() - age
    os.utime(rec, (stamp, stamp))
    return rec


def test_open_discovers_existing_session(fake_cli):
    _sessions_json(fake_cli, [{"id": "pn-abc", "target": str(fake_cli / "spec.md"),
                              "url": "http://localhost:41234/abc"}])
    mgr = PlannotatorManager()
    out = mgr.open(str(fake_cli), str(fake_cli / "spec.md"))
    assert out["reused"] is True
    assert out["url"].startswith("http://localhost:")
    assert out["session"] == "pn-abc"


def test_open_spawns_when_absent_and_registers(fake_cli):
    mgr = PlannotatorManager()
    (fake_cli / "spec.md").write_text("# plan")
    # Fake CLI never registers a session; open must still return promptly with
    # a local id and a registry entry (url None until discovered).
    out = mgr.open(str(fake_cli), str(fake_cli / "spec.md"))
    assert out["session"].startswith("pn-") or out["session"].startswith("local-")
    assert (Path(session_registry_dir())).exists()
    entries = [json.loads(p.read_text()) for p in Path(session_registry_dir()).glob("*.json")]
    assert len(entries) == 1 and entries[0]["workspace"] == str(fake_cli)


def test_open_missing_binary_raises(fake_cli, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent-bin-that-does-not-exist")
    (fake_cli / "spec.md").write_text("# p")
    with pytest.raises(RuntimeError, match="plannotator"):
        PlannotatorManager().open(str(fake_cli), str(fake_cli / "spec.md"))


def test_result_scan_mtime_after_open(fake_cli):
    _sessions_json(fake_cli, [{"id": "pn-1", "target": "spec.md", "url": "http://localhost:1/"}])
    mgr = PlannotatorManager()
    (fake_cli / "spec.md").write_text("# p")
    out = mgr.open(str(fake_cli), str(fake_cli / "spec.md"))
    sid = out["session"]
    assert mgr.result(str(fake_cli), sid)["type"] == "pending"
    _touch_record(fake_cli, str(fake_cli), f"{sid}-annotate-feedback.md")
    res = mgr.result(str(fake_cli), sid)
    assert res["type"] == "feedback"
    assert "X changed" in res["text"]


def test_result_ignores_older_records(fake_cli):
    """A record older than opened_at must not count as this session's feedback."""
    mgr = PlannotatorManager()
    (fake_cli / "spec.md").write_text("# p")
    sid = mgr.open(str(fake_cli), str(fake_cli / "spec.md"))["session"]
    # Write the record with a 1-hour-old mtime, after opening the session.
    _touch_record(fake_cli, str(fake_cli), f"{sid}-annotate-feedback.md", age=3600)
    assert mgr.result(str(fake_cli), sid)["type"] == "pending"


def test_close_kills_process_group(fake_cli):
    mgr = PlannotatorManager()
    (fake_cli / "spec.md").write_text("# p")
    out = mgr.open(str(fake_cli), str(fake_cli / "spec.md"))
    res = mgr.close(str(fake_cli), out["session"])
    assert res["type"] == "closed"
    assert mgr.status(str(fake_cli)) == []


def test_close_discovered_session_unregisters(fake_cli):
    """Host-started sessions (discovered, pgid 0) are un-killable by us: close
    drops our registration and reports 'unregistered', never kills a pgid."""
    _sessions_json(fake_cli, [{"id": "pn-7", "target": str(fake_cli / "spec.md"),
                              "url": "http://localhost:7/"}])
    mgr = PlannotatorManager()
    sid = mgr.open(str(fake_cli), str(fake_cli / "spec.md"))["session"]
    entry = json.loads((Path(session_registry_dir()) / f"{sid}.json").read_text())
    assert entry["pgid"] == 0 and entry["source"] == "discovered"
    assert mgr.result(str(fake_cli), sid)["type"] == "pending"  # CLI still lists pn-7
    assert mgr.close(str(fake_cli), sid)["type"] == "unregistered"
    assert list(Path(session_registry_dir()).glob("*.json")) == []
    _sessions_json(fake_cli, [])  # CLI truth: gone
    with pytest.raises(ValueError, match="unknown session"):
        mgr.result(str(fake_cli), sid)


def test_sweep_drops_dead_keeps_live(fake_cli):
    mgr = PlannotatorManager()
    (fake_cli / "spec.md").write_text("# p")
    sid = mgr.open(str(fake_cli), str(fake_cli / "spec.md"))["session"]
    assert sweep_session_registry() == 0          # live fake server still running
    pid = json.loads((Path(session_registry_dir()) / f"{sid}.json").read_text())["pgid"]
    subprocess.run(["kill", str(pid)], check=False)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and (Path(session_registry_dir()) / f"{sid}.json").exists():
        assert sweep_session_registry() >= 0
        time.sleep(0.1)
    assert not (Path(session_registry_dir()) / f"{sid}.json").exists()


def test_cleanup_workspace_kills_sessions(fake_cli):
    """Workspace teardown kills spawned sessions and empties the registry."""
    mgr = PlannotatorManager()
    (fake_cli / "spec.md").write_text("# p")
    mgr.open(str(fake_cli), str(fake_cli / "spec.md"))
    assert cleanup_workspace_sessions(str(fake_cli)) >= 1
    assert list(Path(session_registry_dir()).glob("*.json")) == []


def test_result_closed_after_group_killed(fake_cli):
    """Dismissed-session regression: once the spawned group is killed and
    reaped, result() must report closed — not hang on pending via an
    unreaped zombie group leader (killpg(pgid, 0) succeeds on zombies)."""
    mgr = PlannotatorManager()
    (fake_cli / "spec.md").write_text("# p")
    sid = mgr.open(str(fake_cli), str(fake_cli / "spec.md"))["session"]
    pgid = json.loads((Path(session_registry_dir()) / f"{sid}.json").read_text())["pgid"]
    os.killpg(pgid, signal.SIGTERM)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if mgr.result(str(fake_cli), sid)["type"] == "closed":
            break
        time.sleep(0.25)
    else:
        pytest.fail("result() never reported closed after the group died")


def test_cleanup_workspace_never_signals_own_group(fake_cli):
    """A discovered (pgid 0) entry must be unregistered, never killpg'd:
    killpg(0) would SIGTERM the calling process's own group."""
    _sessions_json(fake_cli, [{"id": "pn-8", "target": str(fake_cli / "spec.md"),
                              "url": "http://localhost:8/"}])
    mgr = PlannotatorManager()
    mgr.open(str(fake_cli), str(fake_cli / "spec.md"))  # discovered -> pgid 0
    assert cleanup_workspace_sessions(str(fake_cli)) == 1
    assert list(Path(session_registry_dir()).glob("*.json")) == []
```

- [ ] **Step 2: Run; verify failure** (import errors for the new names)

Run: `python -m pytest tests/test_session_managers.py -q` → FAIL.

- [ ] **Step 3: Implement** (append to `tkt/_sessionmanagers.py`)

```python
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
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entry, f)
    os.replace(tmp, path)  # atomic: parallel servers share this registry (R1)


def sweep_session_registry() -> int:
    """Drop registry entries whose process group is dead.  Never kills anything:
    live session servers intentionally outlive sandboxes (spec §4)."""
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
    Returns the number of registry entries removed."""
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
        """Parse `plannotator sessions --json`; tolerate unknown keys and CLI absence."""
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
        for s in sessions:
            if str(s.get("target", "")) == str(Path(target).resolve()):
                return s
        return None

    def open(self, workspace, target, *, kind="annotate", gate=False, base=None):
        workspace = str(Path(workspace).resolve())
        if kind == "review" and re.match(r"https?://", target):
            resolved = target  # PR URLs allowed verbatim (spec §8)
        else:
            resolved = str(Path(target).resolve())
            if not resolved.startswith(workspace + os.sep) and resolved != workspace:
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
        # Anchored spawn (spec §2.4): start_new_session=True detaches the
        # session from our (transient) connection's process group; close()
        # tears it down with killpg, which reaches every group member. The
        # pre-exec trap only covers the exec-failure window — POSIX resets
        # traps across exec, so it cannot guard the exec'd CLI itself.
        wrapper = ["sh", "-c", 'trap "kill -TERM 0" TERM INT; exec "$@"', "tkt-plannotator", *args]
        proc = subprocess.Popen(
            wrapper, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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
        entry = {"pgid": proc.pid, "workspace": workspace, "target": resolved,
                 "kind": kind, "opened_at": time.time(), "source": "spawned"}
        _write_entry(session_id, entry)
        return {"type": "open", "url": url, "session": session_id, "reused": False}

    def status(self, workspace):
        ws = str(Path(workspace).resolve())
        entries = _registry_entries()
        out = []
        for s in self._sessions():
            tgt = str(s.get("target", ""))
            registered = any(e["target"] == tgt and e["workspace"] == ws for e in entries.values())
            if registered or tgt.startswith(ws + os.sep) or tgt == ws:
                out.append(s)
        return out

    def result(self, workspace, session):
        entries = _registry_entries()
        entry = entries.get(session)
        if entry is None or entry.get("workspace") != str(Path(workspace).resolve()):
            raise ValueError(f"unknown session {session!r} for workspace {workspace!r}.")
        proj_dir = Path(os.environ.get("PLANNOTATOR_DATA_DIR", os.path.expanduser("~/.plannotator")))
        name = Path(entry["workspace"]).name
        # Records are keyed by Plannotator's own ids/targets, not our registry
        # id, so the per-session filter is time-based (opened_at) + the two
        # spec name patterns. `pn-*` registry ids matching a record name win
        # when several records are fresh (P2 probe narrows this exactly).
        patterns = ("*-annotate-feedback.md", "*-review-*")
        records = sorted(
            {p for pat in patterns for p in proj_dir.glob(f"feedback/{name}/records/{pat}")},
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

    def close(self, workspace, session):
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
```

Module imports needed by this addition: `shutil`, `re`, `json`, `signal`, `subprocess`, `time`, `pathlib.Path` (merge with Task 2's import block; mypy: add annotations matching `BrainstormManager` style).

- [ ] **Step 4: Run tests; verify PASS** — `python -m pytest tests/test_session_managers.py -q`; then gates.

- [ ] **Step 5: Commit**

```bash
git add tkt/_sessionmanagers.py tests/test_session_managers.py
git commit -m "feat: PlannotatorManager with anchored spawn, records-scan results, pidfile registry"
```

---

### Task 4: Shared session-tool builder, `session_server.py`, `tkt session-server`, Zed rewiring

One builder function registers the identical session tools on any FastMCP
instance (spec §3.3: manager single, shells thin). Zed's `run_server` drops its
local `brainstorm_server` definition and calls the builder; the sandbox-facing
server registers nothing else.

**Files:**

- Create: `tkt/_session_tools.py`, `tkt/session_server.py`
- Modify: `tkt/mcp_server.py` (delete the `@mcp.tool() def brainstorm_server(...)` definition inside `run_server`, ~lines 1190–1245; call the builder instead), `tkt/_cli.py` (new command after `mcp-server`)
- Test: `tests/test_session_tools.py`

**Interfaces:**

- Consumes: `BrainstormManager`, `_format_brainstorm_info`, `PlannotatorManager` (Tasks 2–3, exact signatures above).
- Produces:
  - `tkt._session_tools.build_session_tools(mcp: FastMCP, *, workspace: str) -> None`
  - `tkt._session_tools.safe_subpath(workspace: str, candidate: str) -> str` (raises `ValueError` on escape)
  - `tkt.session_server.run_session_server(workspace: str) -> NoReturn`
  - CLI: `tkt session-server --workspace DIR [-v]` (hidden from help is fine; socat invokes it)
  - Tool names registered: `brainstorm_server`, `plannotator` (+ `plannotator_inbox` in Task 5).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_session_tools.py
"""Tool-surface tests: registration, guards, containment, delegation."""

from __future__ import annotations

import pytest

from tkt._session_tools import build_session_tools, safe_subpath

pytest.importorskip("mcp")
from mcp.server.fastmcp import FastMCP  # noqa: E402


def _tools(mcp: FastMCP) -> set[str]:
    import asyncio

    return set(asyncio.run(mcp.list_tools()).__class__ and {t.name for t in asyncio.run(mcp.list_tools())})


def test_session_tools_registered() -> None:
    mcp = FastMCP("t")
    build_session_tools(mcp, workspace="/tmp/ws")
    names = _tools(mcp)
    assert {"brainstorm_server", "plannotator"} <= names


def test_no_host_danger_tools() -> None:
    """Spec §3.2 guard: the session surface must never expose host execution."""
    mcp = FastMCP("t")
    build_session_tools(mcp, workspace="/tmp/ws")
    forbidden = {"bash", "read", "write", "edit", "glob", "grep", "ls", "todo_write"}
    assert not forbidden & _tools(mcp)


def test_safe_subpath(tmp_path) -> None:
    (tmp_path / "ok.md").write_text("x")
    assert safe_subpath(str(tmp_path), "ok.md") == str(tmp_path / "ok.md")
    with pytest.raises(ValueError, match="outside workspace"):
        safe_subpath(str(tmp_path), "../evil.md")
    with pytest.raises(ValueError, match="outside workspace"):
        safe_subpath(str(tmp_path), "/etc/passwd")  # absolute escape (R1)
    (tmp_path / "link.md").symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="outside workspace"):
        safe_subpath(str(tmp_path), "link.md")  # symlink resolution escapes too


def test_plannotator_tool_delegates_and_contains(monkeypatch) -> None:
    import asyncio

    from tkt import _session_tools

    calls: list[tuple] = []

    class FakeMgr:
        def open(self, workspace, target, **kw):
            calls.append((workspace, target, kw))
            return {"type": "open", "url": "http://localhost:9/x", "session": "pn-1", "reused": False}

    monkeypatch.setattr(_session_tools, "PlannotatorManager", lambda: FakeMgr())
    mcp = FastMCP("t")
    build_session_tools(mcp, workspace="/ws")
    tool = {t.name: t for t in asyncio.run(mcp.list_tools())}["plannotator"]
    out = asyncio.run(tool.fn(action="open", target="../escape.md"))
    assert "outside workspace" in out
    out = asyncio.run(tool.fn(action="open", target="spec.md"))
    assert "pn-1" in out and calls[0][0] == "/ws"
```

(The FakeMgr monkeypatch fixes the workspace the tool passes; `tool.fn` is the
registered callable — verify against the installed `mcp` SDK's FastMCP API at
implementation time and adjust to `tool.function` if the SDK differs. This is
the P1-adjacent pinning the spec asks for; if FastMCP's introspection API
differs, adapt, but keep all four assertions.)

- [ ] **Step 2: Run; verify failure** — `python -m pytest tests/test_session_tools.py -q`.

- [ ] **Step 3: Implement `tkt/_session_tools.py`** (header + below)

```python
"""Session tool registration shared by the Zed MCP server and the sandbox
session server (spec §3.3).  Registers ONLY session-lifecycle tools; the
sandbox-facing surface must never grow host-execution tools (spec §3.2)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from ._sessionmanagers import BrainstormManager, PlannotatorManager, _format_brainstorm_info


def safe_subpath(workspace: str, candidate: str) -> str:
    """Resolve ``candidate`` under ``workspace``; refuse escapes."""
    ws = Path(workspace).resolve()
    resolved = (ws / candidate).resolve() if not os.path.isabs(candidate) else Path(candidate).resolve()
    if resolved != ws and not str(resolved).startswith(str(ws) + os.sep):
        raise ValueError(f"path {candidate!r} is outside workspace {str(ws)!r}.")
    return str(resolved)


def build_session_tools(mcp: FastMCP, *, workspace: str) -> None:
    """Register brainstorm_server / plannotator (+ inbox in Task 5) on ``mcp``."""
    brainstorm = BrainstormManager()
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

        THE DOCSTRING MOVED VERBATIM from tkt/mcp_server.py's brainstorm_server
        (lines with 'The companion server runs outside the sandbox...' through
        the Args block), with one sentence added: also never run it with
        --owner-pid semantics tied to a client; the host owns its lifecycle.

        Args: (verbatim from main)
        """
        action = action.strip().lower()
        try:
            project = safe_subpath(workspace, project_dir)
            if action == "start":
                # owner_pid omitted on purpose: session servers die by close /
                # idle timeout / sweep / tkt rm, never by client disconnect (spec §4).
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
        ``status`` lists this workspace's live sessions; ``result`` reads the
        on-disk records for ``session`` (``type``: feedback/pending/closed);
        ``close`` terminates it. ``kind`` is ``annotate`` or ``review``;
        ``gate`` adds the approval UI; ``base`` sets the review compare ref.
        Non-URL ``target`` paths must live inside the workspace.

        Args: (write full per-arg docs in the same style as brainstorm_server)
        """
        try:
            if action == "open":
                if target is None:
                    return "plannotator open requires a target (file, folder, or PR URL)."
                if not target.startswith("http"):  # PR URLs allowed (spec §8)
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


def _json_block(obj: Any) -> str:
    import json

    return json.dumps(obj, indent=2, default=str)
```

(The two "verbatim from main" markers mean: copy the actual strings from
`tkt/mcp_server.py` — the brainstorm docstring text is on `main` at the
`brainstorm_server` tool, lines ~1198–1225. Copy, then apply the one stated
addition. Do not paraphrase.)

- [ ] **Step 4: Implement `tkt/session_server.py`** (header + below) and the CLI

```python
"""Host-side MCP server reached from a restricted sandbox over the bridge
unix socket (``net-*/session.sock``).  Exposes session tools ONLY (spec §3.2).
Spawned per connection by socat; holds no state (truth lives on disk)."""

from __future__ import annotations

from typing import NoReturn

from mcp.server.fastmcp import FastMCP

from ._session_tools import build_session_tools


def run_session_server(workspace: str) -> NoReturn:
    mcp = FastMCP(name="tkt-session")
    build_session_tools(mcp, workspace=str(workspace))
    mcp.run()  # stdio; EOF on socket close ends the process
    raise SystemExit(0)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(prog="tkt.session_server")
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    run_session_server(args.workspace)
```

`tkt/_cli.py` — add after the `mcp_server` command (mirroring its logging
setup; no Environment/tool config needed — managers work from the host env):

```python
@cli.command("session-server", help="Run the stdio MCP server exposing host session tools.", hidden=True)
@click.option("--workspace", required=True, type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.option("-v", "--verbose", count=True, help="Increase logging verbosity (-vv for debug).")
def session_server(*, workspace: str, verbose: int = 0) -> None:
    _setup_logging(verbose)
    from .session_server import run_session_server

    run_session_server(workspace)
```

- [ ] **Step 5: Rewire Zed's `run_server`**

In `tkt/mcp_server.py` `run_server(...)`: delete the local
`@mcp.tool() def brainstorm_server(...)` definition (keep the `brainstorm =
BrainstormManager()` line deleted too — the builder instantiates), and just
after `mcp = FastMCP(name="tkt")` add:

```python
    from ._session_tools import build_session_tools

    build_session_tools(mcp, workspace=cwd)
```

Keep `bash`/file/todo tools exactly as they are. Zed behavior change (surfaced
decision): the companion no longer dies when the Zed MCP process exits — it
now follows the spec §4 death paths (idle timeout bounds it at 240 min).

- [ ] **Step 6: Run everything; verify green**

`python -m pytest tests/test_session_tools.py tests/ -q && ruff check . && ruff format --check . && mypy tkt/`.
Also smoke the CLI: `python -m tkt.session_server --workspace . </dev/null` must exit cleanly (EOF), no traceback.

- [ ] **Step 7: Commit**

```bash
git add tkt/_session_tools.py tkt/session_server.py tkt/mcp_server.py tkt/_cli.py tests/test_session_tools.py
git commit -m "feat: shared session-tool builder, sandbox session server, Zed registration via builder"
```

---

### Task 5: `plannotator_inbox` proxy (allow-listed upstream)

Spawns `plannotator inbox mcp` (stdio, host side) **per call** (decision:
stateless simplicity; ~1 s node startup is acceptable for these actions;
the spawn-and-filter seam is exactly what the deferred general proxy needs,
spec §12) and forwards to the eight allow-listed upstream tools.

**Files:**

- Modify: `tkt/_session_tools.py`
- Test: `tests/test_session_tools.py`

**Interfaces:**

- Consumes: `build_session_tools(mcp, *, workspace)` (Task 4).
- Produces: tool `plannotator_inbox(action, text=None, thread=None, reply_to=None, payload=None, description=None)`.

- [ ] **Step 1: Write failing tests (fake upstream)**

```python
# tests/test_session_tools.py — append

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


def test_inbox_send_maps_and_forwards(fake_cli, tmp_path, monkeypatch):
    # fake_cli from Task 3 (PATH override); add a fake `plannotator` whose
    # `inbox mcp` invocation execs the fake upstream above.
    import asyncio, json as _json, os, stat

    bin_dir = tmp_path / "bin"
    (bin_dir / "plannotator").write_text(
        '#!/usr/bin/env bash\nif [ "$1" = "inbox" ] && [ "$2" = "mcp" ]; then exec python3 '
        f"{bin_dir}/fake_inbox_mcp.py" + '; fi\nexit 0\n'
    )
    (bin_dir / "plannotator").chmod(0o755)
    (bin_dir / "fake_inbox_mcp.py").write_text(FAKE_INBOX_MCP)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    from tkt._session_tools import build_session_tools

    mcp = FastMCP("t")
    build_session_tools(mcp, workspace="/ws")
    tool = {t.name: t for t in asyncio.run(mcp.list_tools())}["plannotator_inbox"]
    out = asyncio.run(tool.fn(action="send", text="hello"))
    forwarded = _json.loads(out)
    assert forwarded["tool"] == "send_message" and forwarded["args"]["text"] == "hello"


def test_inbox_action_map() -> None:
    from tkt._session_tools import INBOX_ACTION_TOOLS

    assert INBOX_ACTION_TOOLS == {
        "send": "send_message", "read": "read_thread", "wait": "wait_for_reply",
        "resolve": "resolve_message", "decisions": "list_decisions",
        "record": "record_decision", "guide_brief": "get_guide_brief",
        "guide_submit": "submit_guide",
    }
```

(Adapt the fake-upstream stdio handshake to the installed `mcp` SDK's client
behavior — the shape above is newline JSON-RPC as stdio MCP specifies; if the
SDK demands anything extra, add it to the fake, never to the implementation.)

- [ ] **Step 2: Run; verify failure.**

- [ ] **Step 3: Implement** — add to `tkt/_session_tools.py`:

```python
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
    """One-shot stdio MCP call against `plannotator inbox mcp` (host side)."""
    import asyncio
    import json

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def _run() -> str:
        params = StdioServerParameters(command="plannotator", args=["inbox", "mcp"])
        async with asyncio.timeout(_INBOX_UPSTREAM_TIMEOUT_S):  # > upstream's ~50 s cursor
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    res = await session.call_tool(upstream_tool, arguments)
                texts = [c.text for c in res.content if getattr(c, "type", None) == "text"]
                joined = "\n".join(texts)
                if getattr(res, "isError", False):
                    return f"plannotator_inbox upstream error: {joined}"
                return joined

    try:
        return asyncio.run(_run())
    except FileNotFoundError as exc:
        return f"plannotator_inbox failed: plannotator binary not found on host ({exc})."
    except Exception as exc:  # noqa: BLE001 — surface any upstream breakage as tool text
        return f"plannotator_inbox failed: {exc}."
```

and inside `build_session_tools` (after `plannotator`):

```python
    @mcp.tool()
    def plannotator_inbox(
        action: str,  # "send"|"read"|"wait"|"resolve"|"decisions"|"record"|"guide_brief"|"guide_submit"
        text: str | None = None,
        thread: str | None = None,
        reply_to: str | None = None,
        payload: dict[str, Any] | str | None = None,
        description: str | None = None,  # present for human approvals of tool actions
    ) -> str:
        """Leave non-blocking messages/questions for the human in the Plannotator
        Inbox, and drive the guided-review flow: ``guide_brief`` fetches the
        diff brief for authoring a guided review walkthrough, ``guide_submit``
        submits the authored guide JSON (in ``payload``).  ``wait`` blocks up
        to ~50 s for a reply and may return a cursor — call it again.
        Nothing here wakes the human's browser at the session; replies arrive
        to you via ``wait``/``read`` or as injected ``Plannotator:`` messages.

        Args: (full per-arg docs; text for send, thread/reply_to routing,
        payload carries structured input for record/guide_submit.)
        """
        name = INBOX_ACTION_TOOLS.get(action.strip().lower())
        if name is None:
            return f"plannotator_inbox: unknown action {action!r}; use one of {sorted(INBOX_ACTION_TOOLS)}."
        args = {k: v for k, v in {"text": text, "thread": thread, "reply_to": reply_to}.items() if v is not None}
        if payload is not None:
            args["payload"] = payload
        return _inbox_call(name, args)
```

(`_inbox_call`'s per-arg forwarding is deliberately generic — upstream tool
schemas own their arguments; P3 probe confirms exact names.)

- [ ] **Step 4: Run; verify green + gates.**

- [ ] **Step 5: Commit**

```bash
git add tkt/_session_tools.py tests/test_session_tools.py
git commit -m "feat: plannotator_inbox proxy (allow-listed upstream stdio MCP relay)"
```

---

### Task 6: Bridge swap — session listener, env injection, `vc_port` deletion, sweeps

The s2h direction dies here (spec §3.4–§3.5, §7). After this task a restricted
sandbox contains **zero** host `TCP-LISTEN` binds.

**Files:**

- Modify: `tkt/sandbox.py` (bridge block ~lines 553–667, `_build_common_argv` ~789–900, `run`/`run_single_repo` ~445–550, `__init__`/`from_json_data` ~285–333, reaper region ~204–236, add `remove()`), `tkt/_cli.py` (the stale-bridge cleanup command handler)
- Test: `tests/test_sandbox.py` (replace `test_vc_port_default_and_override`, update `test_bridge_lifecycle` and the two `vc_port=18090` constructions)

**Interfaces:**

- Consumes: `tkt.session_server` (Task 4; invoked as `sys.executable -m tkt.session_server`).
- Produces:
  - `Sandbox._start_bridge(self, workspace: str) -> tuple[str, list[subprocess.Popen[Any]]]` (signature change — takes the workspace root)
  - module constants/functions: `SESSION_SOCKET_NAME = "session.sock"`, `session_server_command(workspace: str) -> list[str]`, `session_mcp_config(net_dir: str) -> dict[str, Any]` (all in `tkt/sandbox.py`)
  - `Sandbox.remove(directory: str) -> None` (overrides `Tool.remove`).

- [ ] **Step 1: Rewrite the failing tests**

In `tests/test_sandbox.py`: delete `test_vc_port_default_and_override`;
update `test_bridge_lifecycle` and any `Sandbox(..., vc_port=...)` constructions
(drop the kwarg). Then add:

```python
def test_from_json_rejects_vc_port(tmp_path):
    with pytest.raises(ValueError, match="Unexpected entries"):
        Sandbox.from_json_data({"command": [], "vc_port": 9090})


def test_start_bridge_session_listener(workspace, tmp_path, monkeypatch):
    """The bridge spawns a session listener; session.sock exists and serves
    the spawn command over the socket."""
    monkeypatch.setenv("HOME", str(tmp_path))
    sandbox = Sandbox(command=[], port=18089)
    net_dir, procs = sandbox._start_bridge(str(tmp_path / "ws"))
    try:
        sock = os.path.join(net_dir, SESSION_SOCKET_NAME)
        assert os.path.exists(sock)
        # No host-side TCP-LISTEN survives anywhere in the bridge.
        for p in procs:
            with open(f"/proc/{p.pid}/cmdline", "rb") as f:
                assert b"TCP-LISTEN" not in f.read()
        # The socket actually SERVES the session server (socat fork SYSTEM):
        # a real MCP initialize handshake must name us back.
        init = (
            '{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
            '{"protocolVersion":"2024-11-05","capabilities":{},'
            '"clientInfo":{"name":"probe","version":"0"}}}\n'
        )
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(10.0)
        try:
            conn.connect(sock)
            conn.sendall(init.encode())
            reply = b""
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and b"tkt-session" not in reply:
                try:
                    chunk = conn.recv(65536)
                except TimeoutError:
                    break
                if not chunk:
                    break
                reply += chunk
        finally:
            conn.close()
        assert b"tkt-session" in reply
    finally:
        sandbox._stop_bridge(net_dir, procs)
    assert not os.path.exists(net_dir)


def test_common_argv_injects_session_env(workspace, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    net_dir = tmp_path / "net-x"
    net_dir.mkdir()
    argv = Sandbox(command=[], env={"FOO": "bar"})._build_common_argv(
        home=str(tmp_path), mounts=[], inner="exec true", network=False,
        net_dir=str(net_dir), ports=(18089,),
    )
    assert "--unsetenv" in argv
    i = argv.index("TKT_SESSION_SOCKET")
    assert argv[i + 1] == str(net_dir / "session.sock")
    j = argv.index("OPENCODE_CONFIG_CONTENT")
    cfg = json.loads(argv[j + 1])
    assert cfg["mcp"]["tkt-session"]["type"] == "local"
    assert cfg["mcp"]["tkt-session"]["command"][-1].endswith("session.sock")
    # ours come after configured env so local.jsonc cannot clobber them
    assert argv.index("FOO") < argv.index("TKT_SESSION_SOCKET")
    joined = " ".join(argv)
    assert "TCP-LISTEN" not in joined  # in-sandbox vc socat is gone, too


def test_inner_script_has_no_vc_socat(workspace, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    net_dir = tmp_path / "net-y"
    net_dir.mkdir()
    argv = Sandbox(command=[])._build_common_argv(
        home=str(tmp_path), mounts=[], inner="exec true", network=False,
        net_dir=str(net_dir), ports=(18089,),
    )
    inner = argv[-1]
    assert "vc.sock" not in inner and "UNIX-LISTEN" not in inner


def test_remove_cleans_workspace_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from tkt.sandbox import Sandbox as S

    S(command=[]).remove(str(tmp_path / "ws"))  # no registry -> no-op, no raise
```

- [ ] **Step 2: Run; verify failures.** `python -m pytest tests/test_sandbox.py -q`.

- [ ] **Step 3: Implement `tkt/sandbox.py` changes**

Constants (module level, replacing `_DEFAULT_VC_PORT` block — delete lines
67–72 comment + constant):

```python
# Name of the per-launch MCP session-server socket inside the bridge net dir.
# The host listener (a socat in _start_bridge) spawns `tkt.session_server`
# per connection; the sandbox reaches it through the bind-mounted net dir.
SESSION_SOCKET_NAME = "session.sock"


def session_server_command(workspace: str) -> list[str]:
    """Host-side command the session listener spawns per MCP connection."""
    return [sys.executable, "-m", "tkt.session_server", "--workspace", workspace]


def session_mcp_config(net_dir: str) -> dict[str, Any]:
    """OpenCode config (OPENCODE_CONFIG_CONTENT) wiring the session socket."""
    sock = os.path.join(net_dir, SESSION_SOCKET_NAME)
    cfg: dict[str, Any] = {
        "mcp": {
            "tkt-session": {
                "type": "local",
                "command": ["socat", "-", f"UNIX-CONNECT:{sock}"],
            }
        }
    }
    plugin = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                          "harnesses", "opencode", "plugin", "plannotator-wake.js")
    if os.path.isfile(plugin):  # wake layer lands in Task 8
        cfg["plugin"] = [f"file://{plugin}"]
    return cfg
```

`_start_bridge(self, workspace: str)` — delete the `vc_sock`/`vc_log`/
`TCP-LISTEN` block (and the "no startup race" comment above it); keep the LLM
loop and its readiness wait, and extend the early-exit log list to
`+ [os.path.join(net_dir, "host-session-socat.log")]`. Add after the LLM loop:

```python
        session_sock = os.path.join(net_dir, SESSION_SOCKET_NAME)
        session_log = os.path.join(net_dir, "host-session-socat.log")
        with open(session_log, "w", encoding="utf-8") as f:
            procs.append(
                subprocess.Popen(
                    ["socat", f"UNIX-LISTEN:{session_sock},fork",
                     "SYSTEM:" + shlex.join(session_server_command(workspace))],
                    stdout=f, stderr=subprocess.STDOUT,
                    start_new_session=True, preexec_fn=_set_pdeathsig,
                )
            )
```

Update the docstring bullet list (LLM bullets stay; replace the `vc.sock`
bullet with the session-listener description, drop the "Direction: host ->
sandbox" language). Sweep stale registries at bridge start (best-effort):

```python
        try:
            sweep_session_registry()
        except Exception:  # sweep is hygiene, never fatal (incl. corrupt entries)
            logging.debug("session registry sweep failed", exc_info=True)
```

(Import `sweep_session_registry` from `._sessionmanagers`.)

`_build_common_argv`: drop the `vc_port` parameter and the in-sandbox vc socat
lines (keep the per-LLM-port socat lines); after the configured-env loop add:

```python
        # Session-server bridge env.  Emitted after the configured --setenv
        # block: tkt owns the session channel; local.jsonc must not clobber it.
        if not network and bridge_llm:
            argv += ["--setenv", "TKT_SESSION_SOCKET", os.path.join(net_dir, SESSION_SOCKET_NAME)]
            argv += ["--setenv", "OPENCODE_CONFIG_CONTENT", json.dumps(session_mcp_config(net_dir))]
```

(add `import json` to sandbox.py if absent).

`run`/`run_single_repo`: `net_dir, procs = self._start_bridge(workspace.directory)`
/ `self._start_bridge(repo_dir)`; drop `vc_port=self._vc_port` from the two
`_build_*_argv` calls. `warm_holder_argv` unchanged (no bridge). Delete the
`vc_port` kwarg from `_build_bwrap_argv`, `_build_single_repo_argv`,
`_build_common_argv` signatures and `Sandbox.__init__`, `from_json_data`
(delete its parse+validation lines), and the class docstring entry.

`Sandbox.remove` (new method, after `write`):

```python
    def remove(self, directory: str) -> None:
        """Kill session servers registered for this workspace (``tkt rm`` hook)."""
        from ._sessionmanagers import cleanup_workspace_sessions

        cleanup_workspace_sessions(directory)
```

`tkt/_cli.py`: in the existing stale-bridge cleanup command handler (the one
whose help says "Kill orphaned bridge socats…"), call
`cleanup_stale_bridges(...)` as today **and** `sweep_session_registry()` —
but the sweep runs ONLY when `--dry-run` was NOT given (a preview flag must
not mutate the registry; under dry-run, echo that the registry was left
untouched instead of sweeping). Report both counts; update its help text to
"…and stale session-server registrations".

- [ ] **Step 4: Run; verify green.** Full suite + gates. Also a real smoke (no
network needed): `python -m tkt.sandbox` never runs standalone — instead
`python -c "from tkt.sandbox import session_mcp_config; print(session_mcp_config('/x'))"`
prints the expected JSON.

- [ ] **Step 5: Commit**

```bash
git add tkt/sandbox.py tkt/_cli.py tests/test_sandbox.py
git commit -m "feat: per-launch session-socket bridge; delete exclusive vc_port s2h bridge"
```

---

### Task 7: Guidance + rendering — `AGENTS.md.in`, `render_agents_md`, repo docs

**Files:**

- Modify: `tkt/sandbox.py` (`render_agents_md`), `tkt/AGENTS.md.in`, `AGENTS.md`
- Test: `tests/test_sandbox.py` (existing `render_agents_md` tests — update calls/expectations)

**Interfaces:**

- Produces: `render_agents_md(template: str, *, shared: bool) -> str` (param `vc_port` removed; `{{vc_port}}` substitution deleted from the final return line).

- [ ] **Step 1: Update render tests (fail first)**

Existing tests calling `render_agents_md(..., vc_port=8081)` become
`render_agents_md(template, shared=...)`; add:

```python
def test_render_agents_md_no_vc_port_guidance() -> None:
    from tkt.sandbox import _AGENTS_MD_TEMPLATE, render_agents_md

    with open(_AGENTS_MD_TEMPLATE, encoding="utf-8") as f:
        out = render_agents_md(f.read(), shared=False)
    assert "{{vc_port}}" not in out and "vc_port" not in out
    assert "start-server.sh" in out  # the prohibition sentence references it to forbid it
    assert "brainstorm_server" in out and "plannotator" in out
```

- [ ] **Step 2: Run; verify failures.**

- [ ] **Step 3: Rewrite the guidance**

In `tkt/AGENTS.md.in`, replace the entire "If you offer a browser-based visual
companion … give the human the returned URL verbatim (no auto-open — there is
no display in the sandbox)." paragraph with exactly:

```markdown
If you offer a browser-based visual companion (see the brainstorming skill) or
Plannotator reviews, use the `brainstorm_server` / `plannotator` MCP tools: they
run the session servers on the *host*, where the human's browser reaches them
directly. Hand the human any URL the tool returns **verbatim** and end your
turn — feedback arrives later as `Plannotator:`-prefixed messages or when you
ask the `plannotator` tool for the session `result`. If you offer the Plannotator
Inbox / guided reviews, use the `plannotator_inbox` tool (`guide_brief` then
`guide_submit` to author a guided review — ask your human to request it rather
than clicking the web-UI button). Never launch `start-server.sh` or the
`plannotator` CLI yourself: an in-sandbox server is unreachable by design, and
the host tools own session lifecycles (attach-or-start, no duplicates). If a
session tool is missing from your toolset, stop and report it instead of
improvising.
```

In `tkt/sandbox.py`: drop `vc_port: int` from `render_agents_md`, drop
`{{vc_port}}` from its return (now `return "".join(out)`) and its docstring.
Update the one call site in `write()` (line ~349).

Repo `AGENTS.md`: in the `tkt/sandbox.py` layout bullet, replace "plus
`{{vc_port}}`" with "`. Restricted runs bridge a per-launch unix socket "
"(`net-*/session.sock`) to a host-side MCP session server hosting the "
"companion and Plannotator tools; there is no sandbox-to-host TCP bridge.`"
(Keep the shared-worktree sentences intact.)

- [ ] **Step 4: Run; verify green.** Full suite + gates.

- [ ] **Step 5: Commit**

```bash
git add tkt/sandbox.py tkt/AGENTS.md.in AGENTS.md tests/test_sandbox.py
git commit -m "docs: unified host-session-tool guidance; drop vc_port from AGENTS rendering"
```

---

### Task 8: Wake plugin (`plannotator-wake.js`)

Separable layer (spec §3.6). Prior art first: `git show
feature/plannotator-integration:docs/superpowers/specs/2026-10-10-plannotator-integration-design.md`
(§ plugin) and `git -C superpowers log feature/plannotator-integration --oneline`
(phase-1 corpus at `76cc115`) — reuse their message-injection approach; **only
the transport changes** (bridged HTTP → MCP-over-unix-socket calls to the
`plannotator` tool). If injection into a live `opencode acp` session proves
impossible, the sanctioned fallback is deleting the `plugin` entry (baseline
pull semantics remain — spec §3.6); document the outcome in the commit message
either way.

**Files:**

- Create: `harnesses/opencode/plugin/plannotator-wake.js`
- Test: `tests/test_wake_plugin_client.py` (node smoke of the pure client half)

**Interfaces:**

- Consumes: `TKT_SESSION_SOCKET` env (Task 6), the `plannotator` tool surface (Task 4) over newline JSON-RPC on the socket.
- Produces: the plugin file `session_mcp_config` already references (Task 6 gated on its existence).

- [ ] **Step 1: Write the client half + failing node smoke**

```js
// harnesses/opencode/plugin/plannotator-wake.js
// In-sandbox wake layer: polls the host session server over the bridge unix
// socket for new Plannotator feedback and injects `Plannotator:` messages.
// Pure-MCP client half is exported for tests; only the OpenCode plugin half
// touches the plugin API. Active only when TKT_SESSION_SOCKET is set (i.e.
// inside a tkt restricted sandbox).
import net from "node:net";

export class SessionSocketClient {
  constructor(socketPath) { this.path = socketPath; this.buf = ""; this.nextId = 1; this.pending = new Map(); }
  connect() {
    return new Promise((resolve, reject) => {
      this.sock = net.connect(this.path);
      this.sock.on("connect", resolve);
      this.sock.on("error", reject);
      this.sock.on("data", (d) => this._ onData(d));
    });
  }
  _onData(chunk) {
    this.buf += chunk.toString("utf8");
    let idx;
    while ((idx = this.buf.indexOf("\n")) >= 0) {
      const line = this.buf.slice(0, idx).trim();
      this.buf = this.buf.slice(idx + 1);
      if (!line) continue;
      let msg; try { msg = JSON.parse(line); } catch { continue; }
      const p = this.pending.get(msg.id);
      if (p) { this.pending.delete(msg.id); msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result); }
    }
  }
  rpc(method, params, timeoutMs = 20_000) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        this.sock.destroy();
        reject(new Error(`rpc ${method} timed out`));
      }, timeoutMs);
      timer.unref?.();
      this.pending.set(id, {
        resolve: (v) => { clearTimeout(timer); resolve(v); },
        reject: (e) => { clearTimeout(timer); reject(e); },
      });
      this.sock.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
    });
  }
  _rejectPending(err) {
    for (const { reject } of this.pending.values()) reject(err);
    this.pending.clear();
  }
  async callTool(name, args) {
    const res = await this.rpc("tools/call", { name, arguments: args });
    return (res?.content ?? []).map((c) => c.text ?? "").join("\n");
  }
  close() { this.sock?.end(); }
}
```

(`_ onData` above is `_onData` — write it as a single identifier; spacing here
is a plan-rendering artifact, not code.) `rpc()` must ALWAYS settle: the 20 s
timeout destroys the socket, and `connect()` must additionally wire
`sock.on("close", () => this._rejectPending(new Error("socket closed")))`
(so a host server stalling or restarting mid-tick can never hang a tick
forever or leak sockets; the next tick reconnects fresh).

Smoke test (node is on the mounts; skip gracefully if absent):

```python
# tests/test_wake_plugin_client.py
import shutil, subprocess, textwrap
from pathlib import Path

import pytest

PLUGIN = str(Path(__file__).resolve().parents[1] / "harnesses/opencode/plugin/plannotator-wake.js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_socket_client_roundtrip(tmp_path):
    """Client half speaks MCP framing over the socket against a stub server."""
    server = tmp_path / "stub.mjs"
    server.write_text(textwrap.dedent("""
        import net from "node:net";
        import { createInterface } from "node:readline";
        const srv = net.createServer((c) => {
          createInterface({ input: c }).on("line", (l) => {
            const r = JSON.parse(l);
            if (r.method === "initialize")
              c.write(JSON.stringify({ jsonrpc: "2.0", id: r.id, result: { protocolVersion: "2024-11-05", capabilities: {}, serverInfo: { name: "stub" } } }) + "\\n");
            if (r.method === "tools/call")
              c.write(JSON.stringify({ jsonrpc: "2.0", id: r.id, result: { content: [{ type: "text", text: "Plannotator: hello" }] } }) + "\\n");
          });
        });
        srv.listen(process.argv[2], () => console.log("ready"));
    """))
    client = tmp_path / "client.mjs"
    client.write_text(
        f'import {{ SessionSocketClient, parseResult }} from "{tmp_path}/plugin.js";\n'
        'const c = new SessionSocketClient(process.argv[2]);\n'
        'await c.connect();\n'
        'await c.rpc("initialize", {});\n'
        'const t = await c.callTool("plannotator", { action: "status" });\n'
        'if (!t.includes("hello")) { console.error("BAD " + t); process.exit(1); }\n'
        'const rec = parseResult("```json\\n{\\"type\\":\\"feedback\\",\\"text\\":\\"x\\"}\\n```");\n'
        'if (rec.type !== "feedback" || rec.text !== "x") { console.error("BADPARSE"); process.exit(1); }\n'
        'c.close(); process.exit(0);\n'
    )
    (tmp_path / "plugin.js").write_text(open(PLUGIN).read())
    sock = str(tmp_path / "s.sock")
    srv = subprocess.Popen(["node", str(server), sock], stdout=subprocess.DEVNULL)
    import time, os
    for _ in range(50):
        if os.path.exists(sock):
            break
        time.sleep(0.1)
    try:
        subprocess.run(["node", str(client), sock], check=True, timeout=20)
    finally:
        srv.terminate()
```

- [ ] **Step 2: Run; verify failure** (file missing).

- [ ] **Step 3: Implement the plugin half** (append to the JS file)

Ground the injection call in the installed OpenCode v2 plugin API (the vendored
`investigations/opencode-src` on `origin/2.0` and the phase-1 corpus;
`opencode plugin` docs): a plugin is an exported async function receiving
`{ client, ... }`; post the injected message via the client session API exactly
as phase 1 did. The polling half is fully specified here:

```js
export const parseResult = (s) =>
  JSON.parse(String(s).replace(/^```json\n?/m, "").replace(/\n?```$/m, ""));

export const PlannotatorWake = async ({ client, directory }) => {
  const sockPath = process.env.TKT_SESSION_SOCKET;
  if (!sockPath) return {}; // host runs: inert (spec §3.6)
  const seen = new Map(); // session id -> last feedback text seen
  const timer = setInterval(async () => {
    let c;
    try {
      c = new SessionSocketClient(sockPath);
      await c.connect();
      await c.rpc("initialize", {});
      const sessions = parseResult(await c.callTool("plannotator", { action: "status" }));
      for (const s of sessions) {
        const id = s.id ?? s.session;
        if (!id) continue;
        try {
          const out = await c.callTool("plannotator", { action: "result", session: id });
          const rec = parseResult(out);
          if (rec.type === "feedback" && seen.get(id) !== rec.text) {
            seen.set(id, rec.text);
            await inject(client, "Plannotator: feedback on review " + id + ":\n\n" + rec.text);
          }
        } catch { /* one bad session (e.g. unparseable result) must not stop the rest */ }
      }
    } catch { /* host tools unreachable: retry next tick */ }
    finally { c?.close(); }
  }, 30_000);
  timer.unref?.();
  return {};
};
```

`inject(client, text)` is the single function to adapt from the phase-1
reference (posting a `Plannotator:`-prefixed message into the live session);
if phase 1's injection path does not exist under this build, implement the
sanctioned fallback (drop the `plugin` entry from `session_mcp_config` and note
it in the commit message).

- [ ] **Step 4: Run node smoke; gates.** Then a live check belongs to a human
turn (P2-style: open a review from a sandbox, submit feedback, watch the
message arrive) — record the outcome in the task report; not a test gate.

- [ ] **Step 5: Commit**

```bash
git add harnesses/opencode/plugin/plannotator-wake.js tests/test_wake_plugin_client.py
git commit -m "feat: in-sandbox wake plugin (MCP-over-socket poll, in-process injection)"
```

---

### Task 9: Whole-branch verification

- [ ] **Step 1:** `ruff check . && ruff format --check . && mypy tkt/ && python -m pytest tests/ -q` — all green (one conda forkpty warning acceptable).
- [ ] **Step 2:** grep-gate the deletions: `grep -rn "vc_port\|TCP-LISTEN" tkt/ | grep -v test` → no hits; `grep -rn "{{vc_port}}" tkt/ AGENTS.md` → none.
- [ ] **Step 3:** Probe backlog handoff (spec §11): P1/P3 are automatable next human turn (no `--network`); P2 needs the host `plannotator` — script it under `investigations/` (untracked OK) and hand the human the one-liner to run.
- [ ] **Step 4:** `git log --oneline main..HEAD` sanity; then proceed to the SDD whole-branch review (sp-review, xhigh), fix wave, and the finishing-a-development-branch flow (offer squash + rebase onto `main`; offer Plannotator human browser review if host access available; never push unasked).

---

## Plan-level decisions made beyond the spec (surface in chat, approved at Gate 2)

1. **`open_browser` dropped from the `plannotator` tool** (spec §3.1 stub had
   it): the CLI opens the browser natively with no documented suppress
   contract; inventing one is worse than the parameter's value. Companion keeps
   its `open_browser` (the launcher script honors it).
2. **`--owner-pid` omitted for companion servers spawned via the shared
   builder** — Zed-visible behavior change: companions now bound by the 240-min
   idle timeout instead of dying with the Zed MCP process (spec §4 death-paths
   purity; attach-or-start dedupes).
3. **Inbox proxy spawns its upstream per call** — stateless simplicity; keeps
   the generalization seam intact.
4. **Wake plugin lives at `harnesses/opencode/plugin/` referenced by repo
   path** (no net-dir copy): any working OpenCode sandbox already mounts the
   tkt repo read-only (the agents/skills symlink chain requires it).
5. **Registry sweep never kills live session servers** (removes dead entries
   only) — the restart-survival property (spec §4) outranks eager cleanup;
   `tkt rm` and explicit close handle intent.



---

## Wake-layer debug and Design A landing (post-branch, 2026-10-14)

The live wake miss was two independent bugs: manager-side (`status()`
entries carried no session id, and submitted sessions vanished from the
CLI list — fixed in bc9ac49, registry ∪ CLI with `id` on registry-backed
entries) and delivery-side (installed OpenCode 2.0.26 ignores the env-config
`plugin` array, requires `export default { id, setup }`, autoloads only from
`~/.config/opencode/plugin/`, and silently skips symlinks — all established
by three host probe rounds in `investigations/host_session_probes/wake/`).
An interim bwrap-bind delivery failed at launch (bwrap cannot mkdir under a
remounted-ro config dir) and was reset out. Final design (ruling with human,
2c3848a): per-launch channel wiring stays injected; the wake plugin is a
real copied file installed by `tkt install-opencode-agent`, with a
non-fatal launch warning on missing/stale copies, and a declared one-commit
exit (delete plugin, pull-only) if live acceptance misbehaves. Spec §3.5/§3.6
rewritten to match. Acceptance (human turn): run install, restart restricted
sandbox, submit a review, expect an unprompted `Plannotator:` message.

## Human Plannotator review fix round (post-branch, 2026-10-13)

Human review (launched through the new `plannotator` session tool) returned one
annotation: drop spec/plan references from in-code docs and comments — code must
stand on its own. Two comment-only fix commits, each with a scoped re-review
PASS: 55c55a6 (13 "spec §N"/"Task N" sites, 7 files, invariants rewritten
inline) and 8900fa9 (3 residual "(R1)"/"(R2)" requirement tags). Test fixture
filenames (`spec.md`) and the spec/plan docs themselves are intentionally
unaffected. Full suite green on 8900fa9 (302 passed). Open follow-on logged in
the ledger: the review's submission produced no `Plannotator:` wake message in
the sandboxed session (record + `action="result"` fallback worked as designed);
wake-layer probe pending.

## Final whole-branch review fix wave (post-task reviews, 2026-10-12)

Final review verdict "With fixes"; these land on top of the task reviews
(1–2 are reflected in the Task 3 / Task 8 snippets above):

1. `open()` reuse-branch adopts `pgid` + `opened_at` from a matching
   `local-<pid>` entry (migrates the registry key) so a late-discovered
   session stays killable; new test
   `test_open_adopts_local_entry_after_late_registration`.
2. Wake plugin: per-session loop body wrapped in `try/catch` — one bad
   session must not abort the whole poll tick.
3. `tests/test_mcp_server.py`: spy `tkt.mcp_server.build_session_tools`
   during the `run_server` test — must be called with the workspace and the
   three session tools registered (pins the Zed surface; deleting the call
   must fail the suite).
4. `tests/test_session_tools.py`: `guide_submit` payload-echo assertion
   (dict reaches upstream verbatim), mirroring the existing `send` test.
5. `_write_entry` uses `tempfile.mkstemp(dir=session_registry_dir(),
   suffix=".tmp")` — no shared tmp filename between concurrent writers.
6. `tkt/_session_tools.py` URL sniffing uses the manager's
   `re.match(r"https?://", target)` predicate (was `startswith("http")`).

Accepted without change (adjudicated): `result()` target-based fallback for
late-discovery ids (next `open()` adopts; plugin now tolerant); PR-URL
`_match` key shape (P2-probe-dependent, parked); all remaining
ledger→final-review items.
