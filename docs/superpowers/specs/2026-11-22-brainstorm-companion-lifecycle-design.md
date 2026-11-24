# Brainstorming Companion Lifecycle — Design Spec

Date: 2026-11-22
Status: Approved (Gate 1, in-chat)
Companion spec: `2026-10-12-host-session-services-design.md` §4's lifecycle
model remains the intent this spec finally implements; its "session servers
… die by explicit close, idle timeout, the boot sweep, or `tkt rm`" promise
for brainstorm servers was never true and is replaced by the reapers below.
Branches: `brainstorm-lifecycle` in the superpowers fork and
`feature/brainstorm-lifecycle` in tkt2, merged together (fork first).

## 1. Background

The visual-companion server (`superpowers/skills/brainstorming/scripts/server.cjs`)
"times out much too quickly": it dies while the human still wants the tab.
It has three death modes, two of which are bugs:

1. **Idle timeout** — 4 h default (`BRAINSTORM_IDLE_TIMEOUT_MS`), checked
   every 60 s (`LIFECYCLE_CHECK_MS`). An open but quiet browser tab does not
   count as activity (`touchActivity()` fires only on authorized HTTP
   requests, browser WS messages, and screen pushes).
2. **Owner-death watchdog** — `server.cjs` exits ≤ 60 s after
   `BRAINSTORM_OWNER_PID` dies. `start-server.sh`'s grandparent heuristic
   (`OWNER_PID=$(ps -o ppid= -p $PPID)`, line 165) resolves it to a process
   in the session-MCP connection chain (the bridge socat's forked child /
   its `sh -c`), contradicting `BrainstormManager`'s own docstring: servers
   are supposed to outlive the MCP connection, "never by client disconnect".
3. **Signal kill, no marker** — `BrainstormManager.start` runs the launcher
   with a plain `subprocess.run` (no `start_new_session`), and the script's
   `nohup node server.cjs &` never leaves the launcher's process group —
   which is the session-listener socat's group (`start_new_session=True` at
   `tkt/sandbox.py:685–697`). `_stop_bridge`'s `os.killpg`
   (`tkt/sandbox.py:768–787`) therefore SIGTERMs/SIGKILLs the companion at
   every sandbox/bridge teardown.

Forensics (2026-11-19, `tkt2/.superpowers/brainstorm/21498-1795143911/state/`):
server died ~9 min after start with **no `server-stopped` marker** — neither
timer fired; it was signal-killed, matching mode 3.

There is also no reaper behind any of this: the "boot sweep" in the
`BrainstormManager` docstrings is Plannotator-only (`sweep_session_registry`),
and the `tkt rm` path (`tkt/sandbox.py:439–441` →
`cleanup_workspace_sessions`) never touches brainstorm servers.

Requirements:

- **R1 (hard)**: The human's browser tab is the primary lifecycle signal.
  Closing the last tab reaps the server cleanly within ~1 minute — the
  Plannotator model the human asked for.
- **R2 (hard)**: No idle cap by default. A deliberately-open tab may pin a
  companion for days (dense replies read over a weekend).
- **R3 (hard)**: Companion servers survive sandbox/bridge teardown and MCP
  reconnect churn (documented intent, finally made true).
- **R4 (hard)**: Every started server still terminates via exactly one of:
  tab-close reaper, never-connected reaper, explicit `stop`, `tkt rm`. No
  unbounded lifetime that the human did not choose by keeping a tab open.
- **R5**: No regression of the session-socket bridge or Plannotator
  lifecycle (`sweep_session_registry`, `cleanup_workspace_sessions` untouched).
- **R6**: The superpowers fork keeps upstream-compatible defaults; the
  aggressive changes are opt-in and tkt opts into them.

## 2. Central decisions

1. **Browser-connection reaping is the lifecycle.** The hand-rolled
   WebSocket layer already maintains a `clients` set; tab close closes the
   socket reliably. Last client gone → 60 s grace (absorbs reloads and
   `reload` broadcasts) → clean `shutdown('browser-closed')`. Armed only
   after the first client ever connects, so the start → push → `--open`
   sequence is never raced.
2. **Idle timeout: mechanism stays, default off in tkt.** `0` becomes a
   legal value (`BRAINSTORM_IDLE_TIMEOUT_MS=0`, `--idle-timeout-minutes 0`)
   meaning "do not expire on idle"; unset keeps the 4 h upstream default
   (R6). `BrainstormManager.start` passes `0` unless the agent supplies a
   value.
3. **Never-connected reaper bounds the pre-first-open window.** Without it,
   decision 2 makes abandoned-before-first-open servers (headless host,
   failed `--open`) permanent. No client ever connects → clean
   `shutdown('no-browser-connected')` after 2 h default.
4. **The launcher runs in its own session.** `start_new_session=True` on the
   `BrainstormManager.start` `subprocess.run` detaches bash → `nohup node`
   from the bridge socat's group, so `_stop_bridge`'s `killpg` can no longer
   touch the companion (mode 3 deleted).
5. **Owner monitoring is explicitly disabled via `--owner-pid none`.**
   `none` → empty `BRAINSTORM_OWNER_PID` → `ownerPid = null`, the state the
   Windows path already uses (idle/tab reapers only). The sentinel is the
   string `none`, never `0`: `process.kill(0, 0)` signals the caller's own
   process group — a loaded footgun in the validation path. `start-server.sh`
   rejects `0` outright.
6. **`tkt rm` reaps its workspace's companions** via a new
   `cleanup_workspace_brainstorm`, alongside the existing Plannotator
   cleanup. Content dirs vanish with the workspace, so surviving servers
   would be serving corpses.
7. **Matching feature branches, merged fork-first.** `brainstorm-lifecycle`
   in TallJimbo/superpowers → merged to fork `main`; then tkt2's
   `feature/brainstorm-lifecycle` lands the submodule pin bump plus the tkt
   changes in one history (the pin must not point at an unmerged fork
   commit). `tkt install-harness` after both merges refreshes the
   `~/.agents/skills/brainstorming` copy that `BrainstormManager` falls back
   to and that in-sandbox agents read for `visual-companion.md` guidance.
8. **Skill guidance: tab close means done.** A `server-stopped` with reason
   `browser-closed` is the human opting out; the agent restarts only when it
   has a genuinely new screen, and always with the same `--project-dir` so
   the re-opened tab reconnects on the reused port.

## 3. Components and interface stubs

### 3.1 `server.cjs` (superpowers fork)

Config block (replaces the `ms > 0` floor that forces the 4 h default):

```js
// ========== Lifecycle Configuration ==========

// Idle timeout: 0 disables the idle reaper; unset keeps the 4 h default.
// tkt runs with 0 (tab-close reaping is the lifecycle; an open tab may pin
// the server for days by design).
const IDLE_TIMEOUT_MS = readMsEnv('BRAINSTORM_IDLE_TIMEOUT_MS', 4 * 60 * 60 * 1000);
// Grace period with zero connected browser clients before shutdown, and the
// deadline for the first client ever to connect. Both 0 = disabled.
const CLOSE_GRACE_MS = readMsEnv('BRAINSTORM_CLOSE_GRACE_MS', 60 * 1000);
const NO_BROWSER_TIMEOUT_MS = readMsEnv('BRAINSTORM_NO_BROWSER_TIMEOUT_MS', 2 * 60 * 60 * 1000);

function readMsEnv(name, fallback) {
  const raw = process.env[name];
  if (raw === undefined || raw === '') return fallback;
  const ms = Number(raw);
  return Number.isFinite(ms) && ms >= 0 ? ms : fallback;
}
```

Client tracking (all four existing `clients.delete(socket)` sites in
`handleUpgrade` become `removeClient(socket)`; `clients.add` becomes
`addClient`):

```js
const clients = new Set();
let everConnected = false;
let closeGraceTimer = null;
let noBrowserTimer = null;

function addClient(socket) {
  clients.add(socket);
  everConnected = true;
  if (closeGraceTimer) { clearTimeout(closeGraceTimer); closeGraceTimer = null; }
  if (noBrowserTimer) { clearTimeout(noBrowserTimer); noBrowserTimer = null; }
}

function removeClient(socket) {
  clients.delete(socket);
  if (clients.size === 0 && everConnected && CLOSE_GRACE_MS > 0) {
    closeGraceTimer = setTimeout(() => shutdown('browser-closed'), CLOSE_GRACE_MS);
    closeGraceTimer.unref();
  }
}
```

Lifecycle wiring inside `startServer()` (and `shutdown()` clears both timers
alongside `clearInterval(lifecycleCheck)`):

```js
if (NO_BROWSER_TIMEOUT_MS > 0) {
  noBrowserTimer = setTimeout(() => shutdown('no-browser-connected'), NO_BROWSER_TIMEOUT_MS);
  noBrowserTimer.unref();
}
const lifecycleCheck = setInterval(() => {
  if (!ownerAlive()) shutdown('owner process exited');
  else if (IDLE_TIMEOUT_MS > 0 && Date.now() - lastActivity > IDLE_TIMEOUT_MS) shutdown('idle timeout');
}, LIFECYCLE_CHECK_MS);
```

Owner-PID parse (`BRAINSTORM_OWNER_PID` unset or `none` → no monitoring):

```js
const ownerPidRaw = process.env.BRAINSTORM_OWNER_PID;
let ownerPid = ownerPidRaw && ownerPidRaw !== 'none' ? Number(ownerPidRaw) : null;
if (ownerPid !== null && !(Number.isInteger(ownerPid) && ownerPid > 0)) ownerPid = null;
```

`server-stopped` keeps its `{reason, timestamp}` shape; reasons are now
`'idle timeout' | 'owner process exited' | 'browser-closed' |
'no-browser-connected'`. `server-info` gains nothing; agents read reasons via
`BrainstormManager.status`.

### 3.2 `start-server.sh` (superpowers fork)

- `--idle-timeout-minutes <n>`: validation becomes a non-negative integer;
  `0` exports `BRAINSTORM_IDLE_TIMEOUT_MS=0`.
- `--owner-pid <pid|none>`: `none` sets `OWNER_PID=""` (applies after the
  grandparent heuristic, replacing today's `-n "$OWNER_PID_OVERRIDE"` test,
  which silently ignores the empty string). A literal `0` exits with an
  error JSON (own-group signal footgun).
- New flags: `--close-grace-seconds <n>` → `BRAINSTORM_CLOSE_GRACE_MS`,
  `--no-browser-timeout-minutes <n>` → `BRAINSTORM_NO_BROWSER_TIMEOUT_MS`
  (both non-negative integers; `0` = disabled).
- Usage-comment block updated accordingly.

### 3.3 `BrainstormManager.start` (`tkt/_sessionmanagers.py`)

The never-exercised `owner_pid` parameter is deleted — the host owns the
lifecycle, full stop:

```python
# tkt runs companions tab-close-driven: idle expiry off unless the agent
# explicitly asks for a cap.
_BRAINSTORM_DEFAULT_IDLE_MINUTES = 0

def start(
    self,
    project_dir: str,
    *,
    port: int | None = None,
    idle_timeout_minutes: int | None = None,
    open_browser: bool = True,
) -> dict[str, Any]:
    ...
    argv = [
        "bash",
        _find_brainstorm_script(),
        "--project-dir",
        project,
        "--owner-pid",
        "none",
        "--idle-timeout-minutes",
        str(int(idle_timeout_minutes if idle_timeout_minutes is not None else _BRAINSTORM_DEFAULT_IDLE_MINUTES)),
    ]
    if open_browser:
        argv += ["--open"]
    # start_new_session: the companion must outlive the bridge. Without it
    # the server inherits this session-listener socat's process group and
    # _stop_bridge's killpg signal-kills it at sandbox teardown.
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=_BRAINSTORM_START_TIMEOUT_S,
        env=env,
        check=False,
        start_new_session=True,
    )
```

`_is_alive`, `_read_pid`, `_read_info` become `@staticmethod`s (they already
use no instance state) so the rm hook can reuse them.

### 3.4 `tkt rm` hook (`tkt/_sessionmanagers.py`, `tkt/sandbox.py`)

```python
def cleanup_workspace_brainstorm(workspace: str) -> int:
    """``tkt rm`` hook: terminate companion servers started under ``workspace``.

    Their content dirs live inside the workspace and vanish with it, so a
    surviving server would only serve a corpse until its tab closes.
    """
    killed = 0
    base = Path(str(Path(workspace).resolve()), ".superpowers", "brainstorm")
    for state_dir in sorted(p for p in base.glob("*/state") if p.is_dir()):
        if not BrainstormManager._is_alive(str(state_dir)):
            continue
        try:
            os.kill(int(BrainstormManager._read_pid(str(state_dir))), signal.SIGTERM)
            killed += 1
        except (OSError, TypeError):
            pass
    return killed
```

Wired beside the Plannotator call in `tkt/sandbox.py` (rm path, ~line 439):

```python
cleanup_workspace_sessions(directory)
cleanup_workspace_brainstorm(directory)
```

### 3.5 Session tool (`tkt/_session_tools.py`)

Surface unchanged (no new params needed); docstring corrected: `idle_timeout_minutes`
`None` now means "no idle cap" (tkt passes `0`); `browser-closed` semantics
noted; the "never by client disconnect" comment becomes true, so it stays.

### 3.6 Skill guidance (`visual-companion.md`, superpowers fork)

- The lifecycle paragraph becomes tab-based: server exits ~1 min after the
  last tab closes; check `server-stopped`/`server-info` before referring to
  the URL (existing instruction); restart with the same `--project-dir`
  reuses the port and an open/reopened tab reconnects.
- New: treat `reason: "browser-closed"` as the human being done — do not
  resurrect the server until you have a genuinely new screen to push.
- No changes to `tkt/AGENTS.md.in` (its "use the `brainstorm_server` tool"
  pointer is unaffected).

## 4. Lifecycle matrix

| Situation | Outcome |
| --- | --- |
| Human closes last tab | `shutdown('browser-closed')` after ~60 s grace |
| Human reloads / `reload` broadcast | Grace timer armed then disarmed; server lives |
| Tab never opened (`--open` failed, headless) | `shutdown('no-browser-connected')` after ~2 h |
| Tab left open for days (weekend reading) | Server lives (R2; reaps on tab close) |
| Sandbox/`sandbox-run` exits | Server survives (own session; owner monitoring off) |
| OpenCode MCP reconnect churn | Server survives |
| Agent/sandbox later calls `start` again | Reuse via `_is_alive`; or restart reuses `.last-port`/`.last-token` |
| `tkt rm` | Server SIGTERMed with its content dirs |
| Explicit `brainstorm_server action="stop"` | SIGTERM (no marker; dead pid is authoritative for `_is_alive`) |

Clean-shutdown path (any `shutdown(reason)`): destroy client sockets, remove
`server-info`, write `server-stopped`, exit — no lingering node.

## 5. Failure posture

- **tkt against an un-upgraded launcher script** (stale `~/.agents/skills`
  copy): old `start-server.sh` errors on `--idle-timeout-minutes 0` and
  unknown flags; `start()` already surfaces the script's error JSON as
  `RuntimeError` → `brainstorm_server failed: …`. Mitigated by the pin bump +
  `tkt install-harness` in the merge choreography (decision 7).
- **Grace-timer race with a mid-shutdown client**: `shutdown()` clears the
  timers; late reconnections after shutdown begin are refused by the closing
  server (`server.close()` + socket destroy).
- **`start_new_session` + start timeout**: on `TimeoutExpired` only the
  launcher bash is killed; a node child it already spawned survives as
  today. Acceptable (pre-existing behavior); `_is_alive` adoption on the next
  `start` recovers it.
- **Workspace deleted without `tkt rm`** (manual `rm -rf`): server serves a
  vanished dir until its tab closes; no port or memory growth beyond that one
  server. No extra reaper for out-of-band destruction (YAGNI, R4 holds via
  the tab reaper).

## 6. Security posture

Unchanged. No new listeners, no changes to the key/cookie/Origin auth
(2026-06 auth hardening intact); `server-stopped` is written under the
existing `umask 077` state dir; `--owner-pid 0` is rejected rather than
merely avoided, so the group-signal footgun can't be reached through the CLI
either.

## 7. Deletions and corrections

- `BrainstormManager.start(..., owner_pid=...)` parameter (dead on the
  tkt path, contradicted by R3).
- `BrainstormManager` module/class docstrings and `_session_tools.py`
  comments claiming a brainstorm "boot sweep" / `tkt rm` reaping — replaced
  by the §4 matrix (the new `tkt rm` hook makes the `rm` half true).
- The `--idle-timeout-minutes` "must be a positive integer" rejection of 0.

## 8. Testing plan

Fork (`tests/brainstorm-server/`, using the existing fast-timer env knobs —
plus the new `BRAINSTORM_CLOSE_GRACE_MS` / `BRAINSTORM_NO_BROWSER_TIMEOUT_MS`):

- `lifecycle.test.js`: last-client WS close → `server-stopped` reason
  `browser-closed` within grace; reconnect within grace cancels; no client
  ever → `no-browser-connected`; `IDLE_TIMEOUT_MS=0` never idles out while
  idle-timeout regression test stays green with unset default (4 h).
- `start-server.test.sh`: `--idle-timeout-minutes 0` accepted and exported;
  `--owner-pid none` clears `BRAINSTORM_OWNER_PID`; `--owner-pid 0` rejected
  with error JSON; new flags validate and export.
- `windows-lifecycle.test.sh` continues to pass (ownerless path unchanged).

tkt (`tests/test_session_managers.py`):

- `BrainstormManager.start` (monkeypatched `subprocess.run`): argv contains
  `--owner-pid none` and `--idle-timeout-minutes 0`; agent-supplied value
  passed through; `start_new_session=True`; `owner_pid` param gone.
- `cleanup_workspace_brainstorm`: live pid (fake with marker-free state dir)
  SIGTERMed, dead-pid and stopped sessions skipped, missing base dir returns 0.
- rm-path wiring covered at the unit level (call into the new function); no
  full-sandbox integration test added.

Style gates: `ruff check .`, `ruff format --check .`, `mypy tkt/`.

## 9. Pinned facts (verified 2026-11-22)

- `server.cjs` client set transitions: `clients.add` on upgrade only;
  `clients.delete` at the four `handleUpgrade` sites + `close`/`error`
  handlers — the `addClient`/`removeClient` refactor points.
- `_stop_bridge` signals the session-listener socat's group
  (`start_new_session=True` at spawn), and the companion node inherits that
  group through the launcher chain (no `start_new_session`/`setpgid` in
  `BrainstormManager.start`, and `nohup` does not change pgid).
- Forensic case: `tkt2/.superpowers/brainstorm/21498-1795143911/state/` —
  `server.log` ends mid-session with no `server-stopped`; signal death, not
  a timer.
- Upstream defaults: idle 4 h (`IDLE_TIMEOUT_MS`), lifecycle check 60 s
  (`LIFECYCLE_CHECK_MS`); start-server.sh rejects `--idle-timeout-minutes <1`.
- `tkt rm` currently calls only `cleanup_workspace_sessions`
  (`tkt/sandbox.py:439–441`).

## 10. Resolved during design

- **Primary reaper**: Plannotator-style tab-close, per the human's explicit
  preference ("letting me close tabs and killing server processes when I do").
- **Idle timeout dropped** (not just raised): reading a dense reply over a
  weekend is a normal use case; the never-connected reaper covers the
  zombie window it was backstopping.
- **Fork changes accepted** with matching feature branches, merged together,
  fork first; upstream-compatible defaults in the fork.
- **Watchdog sentinel `none`, not `0`**: `process.kill(0, 0)` semantics.
- **`tkt install-harness` refresh** is part of the merge choreography (the
  `~/.agents/skills` copy is both the launcher fallback and the guidance the
  in-sandbox agent reads).
