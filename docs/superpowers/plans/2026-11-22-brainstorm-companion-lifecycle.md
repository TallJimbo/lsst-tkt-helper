# Brainstorming Companion Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the visual companion's browser tab the primary lifecycle signal — close the tab, the server exits — while removing the idle timeout (tkt default) and the bridge-killpg / owner-PID deaths that currently kill it early.

**Architecture:** Two coordinated feature branches. In the pinned superpowers fork (branch `brainstorm-lifecycle`), `server.cjs` gains two clean reapers driven by the existing WebSocket `clients` set (last-tab-close with grace; never-connected timeout), accepts `BRAINSTORM_IDLE_TIMEOUT_MS=0` and `BRAINSTORM_OWNER_PID=none`, and `start-server.sh` exposes them as flags. In tkt2 (branch `feature/brainstorm-lifecycle`), `BrainstormManager.start` launches the companion in its own process group with `--owner-pid none --idle-timeout-minutes 0`, and `tkt rm` reaps its workspace's companions.

**Tech Stack:** Node (server.cjs, zero runtime deps; the `ws` npm package is a test-only dependency already used by the fork's tests), bash, Python 3.13 / pytest.

**Spec:** `docs/superpowers/specs/2026-11-22-brainstorm-companion-lifecycle-design.md`

## Global constraints

- tkt2 lint gates before every tkt commit: `ruff check .`, `ruff format --check .`, `mypy tkt/` (line-length 110, numpy docstring convention). All `.py` files keep their BSD-3 header (existing files are edited, none created).
- tkt2 tests: `python -m pytest tests/test_session_managers.py -v` from the repo root.
- Fork constraints: no new runtime dependencies under `skills/`; follow `server.cjs`'s existing style (plain helpers, `JSON.stringify` stdout lines). Fork tests run from the tkt2 root: `node superpowers/tests/brainstorm-server/lifecycle.test.js` and `bash superpowers/tests/brainstorm-server/start-server.test.sh`.
- Fork work happens in the `superpowers/` submodule checkout on branch `brainstorm-lifecycle`, **created from the pinned commit** `1e995b819434ec1a0c3d44e30910bb38f72cb73c` (not from a possibly-ahead `origin/main`), so tkt2 tests before the merge exercise exactly the pinned scripts plus this plan's changes.
- Merge choreography (spec §2.7): fork branch → pushed and **merged to the fork's `main` by the human** → only then Task 7 bumps the submodule pin. Never push, open PRs, or merge autonomously.
- Reaper default polarity: reapers that **shorten** lifetime are default-on in the fork (tab-close 60 s grace, never-connected 2 h); anything that **extends** lifetime is opt-in from tkt only (idle-off via `0`; the fork's idle default stays 4 h). This is spec R6 made operational.
- `0` means "disable" for every new time knob; unset means the documented default.

## Fork test port assignments

`lifecycle.test.js` already uses 3401/3402/3415/3417/3418/3419/3421/3422/3423. New tests use 3461–3462 (Task 1) and 3451–3454 (Task 2).

---

### Task 1: server.cjs lifecycle configuration — idle `0` disables, owner `none` accepted

**Repo/branch:** `superpowers/` submodule, branch `brainstorm-lifecycle` (created in Step 0).

**Files:**

- Modify: `superpowers/skills/brainstorming/scripts/server.cjs` (line 113 owner-PID parse; lines 550–563 lifecycle config block; line 642 idle check)
- Test: `superpowers/tests/brainstorm-server/lifecycle.test.js` (append two tests)

**Interfaces:**

- Consumes: nothing (first task).
- Produces: module constants `IDLE_TIMEOUT_MS` (`0` = idle reaper disabled; unset = 4 h), `CLOSE_GRACE_MS` (default 60000), `NO_BROWSER_TIMEOUT_MS` (default 7200000), helper `readMsEnv(name, fallback)`; `ownerPid` tolerant of `'none'` (→ `null`). Task 2 consumes the two new constants; Task 3 exports them from `start-server.sh`.

- [ ] **Step 0: Create the fork branch from the pinned commit**

```bash
cd /home/jbosch/LSST/tkt2/superpowers
git status --porcelain   # must be empty
git checkout -b brainstorm-lifecycle 1e995b819434ec1a0c3d44e30910bb38f72cb73c
node tests/brainstorm-server/lifecycle.test.js   # baseline: all PASS
```

If the baseline shows pre-existing failures, stop and report — do not proceed on a red baseline. (`bash tests/brainstorm-server/start-server.test.sh` should also be green; record the baseline.)

- [ ] **Step 1: Write the failing tests** — append to `superpowers/tests/brainstorm-server/lifecycle.test.js`, immediately before the `console.log(\`\n--- Results:\` line:

```js
  await test('idle reaper disabled with BRAINSTORM_IDLE_TIMEOUT_MS=0', async () => {
    const dir = fs.mkdtempSync('/tmp/bs-idle0-');
    const srv = spawn('node', [SERVER], { env: { ...process.env, BRAINSTORM_PORT: 3461, BRAINSTORM_DIR: dir, BRAINSTORM_IDLE_TIMEOUT_MS: 0, BRAINSTORM_NO_BROWSER_TIMEOUT_MS: 0, BRAINSTORM_LIFECYCLE_CHECK_MS: 50 } });
    let out = ''; srv.stdout.on('data', d => out += d.toString());
    let exited = false; srv.on('exit', () => { exited = true; });
    for (let i = 0; i < 60 && !out.includes('server-started'); i++) await sleep(50);
    let info = null;
    try { info = firstServerStarted(out); } catch (e) {}
    await sleep(1200);
    const survivedWindow = !exited; // snapshot before killAndWait sets exited
    await killAndWait(srv);
    fs.rmSync(dir, { recursive: true, force: true });
    assert(info, 'server should have started');
    assert.strictEqual(info.idle_timeout_ms, 0, 'server-info must report the disabled reaper as 0');
    assert(survivedWindow, 'disabled idle reaper must keep the server up well past a fast-check window');
  });

  await test('owner monitoring disabled with BRAINSTORM_OWNER_PID=none', async () => {
    const dir = fs.mkdtempSync('/tmp/bs-owner-');
    const srv = spawn('node', [SERVER], { env: { ...process.env, BRAINSTORM_PORT: 3462, BRAINSTORM_DIR: dir, BRAINSTORM_OWNER_PID: 'none', BRAINSTORM_IDLE_TIMEOUT_MS: 0, BRAINSTORM_NO_BROWSER_TIMEOUT_MS: 0, BRAINSTORM_LIFECYCLE_CHECK_MS: 50 } });
    let out = ''; srv.stdout.on('data', d => out += d.toString());
    let exited = false; srv.on('exit', () => { exited = true; });
    for (let i = 0; i < 60 && !out.includes('server-started'); i++) await sleep(50);
    await sleep(1200);
    const survivedWindow = !exited; // snapshot before killAndWait sets exited
    await killAndWait(srv);
    fs.rmSync(dir, { recursive: true, force: true });
    assert(survivedWindow, 'BRAINSTORM_OWNER_PID=none must never trigger the owner-death shutdown');
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node superpowers/tests/brainstorm-server/lifecycle.test.js`
Expected: `idle reaper disabled` FAILS (today `0` falls back to 4 h, so `idle_timeout_ms` is `14400000`, not `0`). The owner test may PASS at baseline — today `Number('none')` → NaN, and the startup validation's non-EPERM branch nulls `ownerPid` (monitoring off by accident rather than contract); it pins the explicit `none` semantics the `start-server.sh` flag depends on.

- [ ] **Step 3: Implement** — in `server.cjs`:

3a. Replace line 113 (`let ownerPid = process.env.BRAINSTORM_OWNER_PID ? ...`) with:

```js
// Owner-death watchdog anchor. 'none' (or any unparseable/nonpositive value)
// means no owner monitoring: the browser-connection reapers own the
// lifecycle (tkt's contract — the companion never dies by client disconnect).
const ownerPidRaw = process.env.BRAINSTORM_OWNER_PID;
let ownerPid = ownerPidRaw && ownerPidRaw !== 'none' ? Number(ownerPidRaw) : null;
if (ownerPid !== null && !(Number.isInteger(ownerPid) && ownerPid > 0)) ownerPid = null;
```

3b. Add the helper immediately above the `// ========== Activity Tracking ==========` section header:

```js
// ========== Lifecycle Configuration ==========

// Non-negative integer ms from an env var; unset/empty → fallback. 0 is a
// meaningful value (disable) and must not fall back.
function readMsEnv(name, fallback) {
  const raw = process.env[name];
  if (raw === undefined || raw === '') return fallback;
  const ms = Number(raw);
  return Number.isFinite(ms) && ms >= 0 ? ms : fallback;
}
```

3c. Replace the timeout definitions (lines 552–563: the `// Idle timeout:` comment through the `LIFECYCLE_CHECK_MS` IIFE) with:

```js
// Idle timeout: shut down after this long with no activity. 0 disables the
// reaper (tkt runs tab-close-driven: an open tab may pin the server for days
// by design). Unset keeps the 4 h default; override with
// BRAINSTORM_IDLE_TIMEOUT_MS (start-server.sh: --idle-timeout-minutes).
const IDLE_TIMEOUT_MS = readMsEnv('BRAINSTORM_IDLE_TIMEOUT_MS', 4 * 60 * 60 * 1000);
// Grace with zero connected browser clients before shutdown('browser-closed'),
// and the deadline for the first client ever to connect
// (shutdown('no-browser-connected')). 0 disables each reaper.
const CLOSE_GRACE_MS = readMsEnv('BRAINSTORM_CLOSE_GRACE_MS', 60 * 1000);
const NO_BROWSER_TIMEOUT_MS = readMsEnv('BRAINSTORM_NO_BROWSER_TIMEOUT_MS', 2 * 60 * 60 * 1000);
// How often the watchdog checks for owner-death / idleness. Configurable mainly
// so tests can run fast; production default is 60s.
const LIFECYCLE_CHECK_MS = readMsEnv('BRAINSTORM_LIFECYCLE_CHECK_MS', 60 * 1000);
```

3d. In `startServer()`, change the lifecycle check's idle branch (line 642) to honor the disable value:

```js
    else if (IDLE_TIMEOUT_MS > 0 && Date.now() - lastActivity > IDLE_TIMEOUT_MS) shutdown('idle timeout');
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node superpowers/tests/brainstorm-server/lifecycle.test.js`
Expected: all PASS, including the pre-existing idle tests (4 h default and the `1234567` override untouched).

- [ ] **Step 5: Commit**

```bash
cd /home/jbosch/LSST/tkt2/superpowers
git add skills/brainstorming/scripts/server.cjs tests/brainstorm-server/lifecycle.test.js
git commit -m "Allow BRAINSTORM_IDLE_TIMEOUT_MS=0 to disable idle expiry and BRAINSTORM_OWNER_PID=none to skip the watchdog"
```

---

### Task 2: server.cjs browser-connection reapers

**Files:**

- Modify: `superpowers/skills/brainstorming/scripts/server.cjs` (lines 440–501 client handling; lines 616–644 `shutdown`/lifecycle inside `startServer`)
- Test: `superpowers/tests/brainstorm-server/lifecycle.test.js` (append four tests)

**Interfaces:**

- Consumes: `CLOSE_GRACE_MS`, `NO_BROWSER_TIMEOUT_MS`, `IDLE_TIMEOUT_MS` (Task 1).
- Produces: module-level `addClient(socket)` / `removeClient(socket)` as the only writers of `clients`; `shutdownFn` module-level indirection to `startServer`'s `shutdown`; `shuttingDown` re-entry guard; `server-stopped` reasons `'browser-closed'` and `'no-browser-connected'` alongside the existing `'idle timeout'` / `'owner process exited'`. Task 3 exposes the two new env knobs as CLI flags; Task 4 documents the reasons.

- [ ] **Step 1: Write the failing tests** — append before the Results line:

```js
  await test('last tab closed shuts down with reason browser-closed', async () => {
    const dir = fs.mkdtempSync('/tmp/bs-tab-');
    const srv = spawn('node', [SERVER], { env: { ...process.env, BRAINSTORM_PORT: 3451, BRAINSTORM_DIR: dir, BRAINSTORM_TOKEN: 'tabtok', BRAINSTORM_CLOSE_GRACE_MS: 200, BRAINSTORM_NO_BROWSER_TIMEOUT_MS: 0, BRAINSTORM_IDLE_TIMEOUT_MS: 0, BRAINSTORM_LIFECYCLE_CHECK_MS: 100000 } });
    let out = ''; srv.stdout.on('data', d => out += d.toString());
    let exited = false, code = null; srv.on('exit', c => { exited = true; code = c; });
    for (let i = 0; i < 60 && !out.includes('server-started'); i++) await sleep(50);
    const ws = new WebSocket('ws://localhost:3451/?key=tabtok');
    await new Promise((res, rej) => { ws.on('open', res); ws.on('error', rej); });
    ws.close();
    for (let i = 0; i < 40 && !exited; i++) await sleep(100);
    try {
      assert(exited, 'process must exit after the last tab closes');
      assert.strictEqual(code, 0, 'tab-close shutdown must exit cleanly');
      const stopped = JSON.parse(fs.readFileSync(path.join(dir, 'state', 'server-stopped'), 'utf8'));
      assert.strictEqual(stopped.reason, 'browser-closed');
    } finally {
      if (!exited) await killAndWait(srv);
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  await test('reconnect within the close grace cancels the shutdown', async () => {
    const dir = fs.mkdtempSync('/tmp/bs-tab-');
    const srv = spawn('node', [SERVER], { env: { ...process.env, BRAINSTORM_PORT: 3452, BRAINSTORM_DIR: dir, BRAINSTORM_TOKEN: 'tabtok2', BRAINSTORM_CLOSE_GRACE_MS: 500, BRAINSTORM_NO_BROWSER_TIMEOUT_MS: 0, BRAINSTORM_IDLE_TIMEOUT_MS: 0, BRAINSTORM_LIFECYCLE_CHECK_MS: 100000 } });
    let out = ''; srv.stdout.on('data', d => out += d.toString());
    let exited = false; srv.on('exit', () => { exited = true; });
    for (let i = 0; i < 60 && !out.includes('server-started'); i++) await sleep(50);
    const ws1 = new WebSocket('ws://localhost:3452/?key=tabtok2');
    await new Promise((res, rej) => { ws1.on('open', res); ws1.on('error', rej); });
    ws1.close();
    await sleep(200); // still inside the 500ms grace
    const ws2 = new WebSocket('ws://localhost:3452/?key=tabtok2');
    await new Promise((res, rej) => { ws2.on('open', res); ws2.on('error', rej); });
    await sleep(800); // past the original grace deadline
    try {
      assert(!exited, 'a reconnect inside the grace window must cancel the shutdown');
      ws2.close();
      for (let i = 0; i < 40 && !exited; i++) await sleep(100);
      assert(exited, 'server must exit after the final tab close');
    } finally {
      if (!exited) await killAndWait(srv);
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  await test('no browser ever connecting shuts down with reason no-browser-connected', async () => {
    const dir = fs.mkdtempSync('/tmp/bs-nob-');
    const srv = spawn('node', [SERVER], { env: { ...process.env, BRAINSTORM_PORT: 3453, BRAINSTORM_DIR: dir, BRAINSTORM_NO_BROWSER_TIMEOUT_MS: 200, BRAINSTORM_CLOSE_GRACE_MS: 100000, BRAINSTORM_IDLE_TIMEOUT_MS: 0, BRAINSTORM_LIFECYCLE_CHECK_MS: 100000 } });
    let out = ''; srv.stdout.on('data', d => out += d.toString());
    let exited = false, code = null; srv.on('exit', c => { exited = true; code = c; });
    for (let i = 0; i < 60 && !out.includes('server-started'); i++) await sleep(50);
    for (let i = 0; i < 40 && !exited; i++) await sleep(100);
    try {
      assert(exited, 'process must exit when no browser ever connects');
      assert.strictEqual(code, 0, 'never-connected shutdown must exit cleanly');
      const stopped = JSON.parse(fs.readFileSync(path.join(dir, 'state', 'server-stopped'), 'utf8'));
      assert.strictEqual(stopped.reason, 'no-browser-connected');
    } finally {
      if (!exited) await killAndWait(srv);
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });

  await test('first client connection disarms the no-browser reaper', async () => {
    const dir = fs.mkdtempSync('/tmp/bs-nob-');
    const srv = spawn('node', [SERVER], { env: { ...process.env, BRAINSTORM_PORT: 3454, BRAINSTORM_DIR: dir, BRAINSTORM_TOKEN: 'nobtok', BRAINSTORM_NO_BROWSER_TIMEOUT_MS: 300, BRAINSTORM_CLOSE_GRACE_MS: 0, BRAINSTORM_IDLE_TIMEOUT_MS: 0, BRAINSTORM_LIFECYCLE_CHECK_MS: 100000 } });
    let out = ''; srv.stdout.on('data', d => out += d.toString());
    let exited = false; srv.on('exit', () => { exited = true; });
    for (let i = 0; i < 60 && !out.includes('server-started'); i++) await sleep(50);
    const ws = new WebSocket('ws://localhost:3454/?key=nobtok');
    await new Promise((res, rej) => { ws.on('open', res); ws.on('error', rej); });
    await sleep(1200); // well past the 300ms no-browser deadline
    try {
      assert(!exited, 'a connected client must disarm the never-connected reaper');
    } finally {
      try { ws.close(); } catch (e) {}
      await killAndWait(srv);
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node superpowers/tests/brainstorm-server/lifecycle.test.js`
Expected: the two `browser-closed` tests and `no-browser-connected` FAIL (no reapers exist); 'first client connection disarms' PASSes trivially (baseline has no reaper) — that is fine, it pins the interaction once reapers exist.

- [ ] **Step 3: Implement** — in `server.cjs`:

3a. After `const clients = new Set();` (line 442), add:

```js
let everConnected = false;
let closeGraceTimer = null;
let noBrowserTimer = null;
// shutdown() closes over the http server and watcher inside startServer();
// the module-scope close-grace reaper reaches it through this reference.
let shutdownFn = null;

function addClient(socket) {
  clients.add(socket);
  everConnected = true;
  if (closeGraceTimer) { clearTimeout(closeGraceTimer); closeGraceTimer = null; }
  if (noBrowserTimer) { clearTimeout(noBrowserTimer); noBrowserTimer = null; }
}

function removeClient(socket) {
  // A tab close arrives twice (CLOSE frame, then the socket 'close' event);
  // only a real removal may re-arm the grace timer, or the second call
  // orphans the first timer past any reconnect that clears only the latest.
  if (!clients.delete(socket)) return;
  // The browser holds this socket open for the tab's whole life, so zero
  // clients after a real connection means every tab is gone. The grace
  // period lets a reload (or a reload broadcast) reconnect unshutdowned.
  if (clients.size === 0 && everConnected && CLOSE_GRACE_MS > 0 && shutdownFn) {
    closeGraceTimer = setTimeout(() => {
      closeGraceTimer = null;
      shutdownFn('browser-closed');
    }, CLOSE_GRACE_MS);
    closeGraceTimer.unref();
  }
}
```

3b. Route every `clients` mutation through the helpers — six sites total: `clients.add(socket)` in `handleUpgrade` → `addClient(socket)`; the five `handleUpgrade` deletes (frame-decode error, CLOSE frame, unsupported opcode, `socket.on('close')`, `socket.on('error')`) plus `broadcast`'s write-failure delete → `removeClient(socket)`. After the edit, `grep -n 'clients\.' server.cjs` shows the set touched directly only inside the two helper bodies and the read-only iteration in `shutdown`.

3c. In `startServer()`, add the re-entry guard and timer cleanup to `shutdown` (replacing lines 616–632), and record `shutdownFn` right after it:

```js
  let shuttingDown = false;
  function shutdown(reason) {
    if (shuttingDown) return; // reapers can race (grace timer vs lifecycle check)
    shuttingDown = true;
    console.log(JSON.stringify({ type: 'server-stopped', reason }));
    const infoFile = path.join(STATE_DIR, 'server-info');
    if (fs.existsSync(infoFile)) fs.unlinkSync(infoFile);
    fs.writeFileSync(
      path.join(STATE_DIR, 'server-stopped'),
      JSON.stringify({ reason, timestamp: Date.now() }) + '\n'
    );
    if (closeGraceTimer) { clearTimeout(closeGraceTimer); closeGraceTimer = null; }
    if (noBrowserTimer) { clearTimeout(noBrowserTimer); noBrowserTimer = null; }
    watcher.close();
    clearInterval(lifecycleCheck);
    // Close any upgraded WebSocket sockets so server.close() can complete and
    // the process actually exits instead of lingering on an open connection.
    for (const socket of clients) {
      try { socket.destroy(); } catch (e) { /* already gone */ }
    }
    server.close(() => process.exit(0));
  }
  shutdownFn = shutdown;
```

3d. After the `lifecycleCheck` block (lines 640–644), arm the never-connected reaper:

```js
  // A browser that never connected was never wanted (headless host, failed
  // auto-open, ignored URL): reap rather than leak the server. addClient
  // disarms this, so a tab that opens later than the deadline still counts.
  if (NO_BROWSER_TIMEOUT_MS > 0) {
    noBrowserTimer = setTimeout(() => shutdown('no-browser-connected'), NO_BROWSER_TIMEOUT_MS);
    noBrowserTimer.unref();
  }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node superpowers/tests/brainstorm-server/lifecycle.test.js`
Expected: all PASS. The existing 'idle shutdown closes an open WebSocket' test still passes: the idle path now also carries the `shuttingDown` guard.

Run: `node superpowers/tests/brainstorm-server/auth.test.js && node superpowers/tests/brainstorm-server/ws-protocol.test.js`
Expected: all PASS (upgrade-path refactor is behavior-preserving).

- [ ] **Step 5: Commit**

```bash
cd /home/jbosch/LSST/tkt2/superpowers
git add skills/brainstorming/scripts/server.cjs tests/brainstorm-server/lifecycle.test.js
git commit -m "Reap the companion server when the last browser tab closes or no browser ever connects"
```

---

### Task 3: start-server.sh lifecycle flags

**Files:**

- Modify: `superpowers/skills/brainstorming/scripts/start-server.sh` (usage comment lines 3–22; arg defaults lines 27–33; case block lines 34–73; idle validation lines 83–89; owner-pid override lines 178–182)
- Test: `superpowers/tests/brainstorm-server/start-server.test.sh` (append a flag section)

**Interfaces:**

- Consumes: env var names `BRAINSTORM_IDLE_TIMEOUT_MS`, `BRAINSTORM_CLOSE_GRACE_MS`, `BRAINSTORM_NO_BROWSER_TIMEOUT_MS`, `BRAINSTORM_OWNER_PID` (Task 1/2 semantics: `0` = disabled, empty owner = no watchdog).
- Produces: CLI contract consumed by tkt (Task 5): `--idle-timeout-minutes 0`, `--owner-pid none`, `--close-grace-seconds <n>`, `--no-browser-timeout-minutes <n>`; rejects `--owner-pid 0` and non-integer times with an error-JSON line on stdout and exit 1.

- [ ] **Step 1: Write the failing tests** — append to `superpowers/tests/brainstorm-server/start-server.test.sh`, before the final `echo "--- Results:` block:

```bash
echo ""
echo "--- start-server.sh lifecycle flags ---"

mkdir -p "$TEST_DIR/fake-bin2"
cat > "$TEST_DIR/fake-bin2/node" <<'EOF'
#!/usr/bin/env bash
echo "CAPTURED idle=${BRAINSTORM_IDLE_TIMEOUT_MS:-__UNSET__} grace=${BRAINSTORM_CLOSE_GRACE_MS:-__UNSET__} nobrowser=${BRAINSTORM_NO_BROWSER_TIMEOUT_MS:-__UNSET__} owner=${BRAINSTORM_OWNER_PID:-__UNSET__}"
exit 0
EOF
chmod +x "$TEST_DIR/fake-bin2/node"

check_eq() {
  local name="$1" expected="$2" actual="$3"
  if [[ "$actual" == "$expected" ]]; then pass "$name"; else fail "$name" "expected '$expected', got '$actual'"; fi
}

field() { sed -n "s/.*$1=\([^ ]*\).*/\1/p" <<<"$2"; }

capture_flags() {
  rm -rf "${TEST_DIR:?}/project"
  mkdir -p "$TEST_DIR/project"
  PATH="$TEST_DIR/fake-bin2:$PATH" \
    bash "$START_SCRIPT" --project-dir "$TEST_DIR/project" --foreground "$@" 2>/dev/null \
    | grep '^CAPTURED' | head -1
}

capture_reject() {
  rm -rf "${TEST_DIR:?}/project"
  mkdir -p "$TEST_DIR/project"
  PATH="$TEST_DIR/fake-bin2:$PATH" \
    bash "$START_SCRIPT" --project-dir "$TEST_DIR/project" --foreground "$@" 2>/dev/null || true
}

line=$(capture_flags --idle-timeout-minutes 0)
check_eq "--idle-timeout-minutes 0 disables the idle reaper" "0" "$(field idle "$line")"

line=$(capture_flags --idle-timeout-minutes 30)
check_eq "--idle-timeout-minutes still exports positive minutes" "1800000" "$(field idle "$line")"

line=$(capture_flags --close-grace-seconds 30)
check_eq "--close-grace-seconds exports ms" "30000" "$(field grace "$line")"

line=$(capture_flags --no-browser-timeout-minutes 0)
check_eq "--no-browser-timeout-minutes 0 disables the reaper" "0" "$(field nobrowser "$line")"

line=$(capture_flags --owner-pid none)
check_eq "--owner-pid none clears BRAINSTORM_OWNER_PID" "__UNSET__" "$(field owner "$line")"

line=$(capture_flags --owner-pid 12345)
check_eq "--owner-pid <pid> is passed through" "12345" "$(field owner "$line")"

out=$(capture_reject --owner-pid 0)
if [[ "$out" == *'"error"'* && "$out" == *owner-pid* ]]; then
  pass "--owner-pid 0 rejected (0 signals the caller's own process group)"
else
  fail "--owner-pid 0 rejected (0 signals the caller's own process group)" "got: $out"
fi

out=$(capture_reject --close-grace-seconds abc)
if [[ "$out" == *'"error"'* && "$out" == *close-grace-seconds* ]]; then
  pass "--close-grace-seconds rejects non-integers"
else
  fail "--close-grace-seconds rejects non-integers" "got: $out"
fi
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `bash superpowers/tests/brainstorm-server/start-server.test.sh`
Expected: FAILs on the new checks (`--idle-timeout-minutes 0` currently exits with the "positive integer" error and never launches node; `--close-grace-seconds` / `--no-browser-timeout-minutes` hit "Unknown argument"; `--owner-pid none` sets `OWNER_PID=none`; `--owner-pid 0` is silently accepted).

- [ ] **Step 3: Implement** — in `start-server.sh`:

3a. In the Options usage comment (lines 8–22), replace the `--idle-timeout-minutes` line and add the new flags plus the `none` option for `--owner-pid`:

```bash
#   --idle-timeout-minutes <n>  Shut down after n minutes idle; 0 disables
#                         the idle reaper (default 240 = 4h).
#   --close-grace-seconds <n>  Shut down n seconds after the last browser tab
#                         disconnects; 0 disables (default 60).
#   --no-browser-timeout-minutes <n>  Shut down after n minutes with no
#                         browser ever connecting; 0 disables (default 120).
#   --owner-pid <pid|none>  Harness PID whose death the server's watchdog
#                         reacts to (default: auto-detected grandparent);
#                         "none" disables owner monitoring (host-owned
#                         lifecycle — use when the launcher's parent chain
#                         is a client connection).
```

3b. After `IDLE_TIMEOUT_MINUTES=""` (line 32) add:

```bash
CLOSE_GRACE_SECONDS=""
NO_BROWSER_TIMEOUT_MINUTES=""
```

3c. In the `case` block, after the `--idle-timeout-minutes)` entry, add:

```bash
    --close-grace-seconds)
      CLOSE_GRACE_SECONDS="$2"
      shift 2
      ;;
    --no-browser-timeout-minutes)
      NO_BROWSER_TIMEOUT_MINUTES="$2"
      shift 2
      ;;
```

3d. Replace the idle validation block (lines 83–89) and add the two new validations after it:

```bash
if [[ -n "$IDLE_TIMEOUT_MINUTES" ]]; then
  if ! [[ "$IDLE_TIMEOUT_MINUTES" =~ ^[0-9]+$ ]]; then
    echo "{\"error\": \"--idle-timeout-minutes must be a non-negative integer (0 disables the reaper)\"}"
    exit 1
  fi
  export BRAINSTORM_IDLE_TIMEOUT_MS=$(( IDLE_TIMEOUT_MINUTES * 60 * 1000 ))
fi

if [[ -n "$CLOSE_GRACE_SECONDS" ]]; then
  if ! [[ "$CLOSE_GRACE_SECONDS" =~ ^[0-9]+$ ]]; then
    echo "{\"error\": \"--close-grace-seconds must be a non-negative integer (0 disables the reaper)\"}"
    exit 1
  fi
  export BRAINSTORM_CLOSE_GRACE_MS=$(( CLOSE_GRACE_SECONDS * 1000 ))
fi

if [[ -n "$NO_BROWSER_TIMEOUT_MINUTES" ]]; then
  if ! [[ "$NO_BROWSER_TIMEOUT_MINUTES" =~ ^[0-9]+$ ]]; then
    echo "{\"error\": \"--no-browser-timeout-minutes must be a non-negative integer (0 disables the reaper)\"}"
    exit 1
  fi
  export BRAINSTORM_NO_BROWSER_TIMEOUT_MS=$(( NO_BROWSER_TIMEOUT_MINUTES * 60 * 1000 ))
fi
```

3e. Replace the explicit-override block (lines 178–182) — `none` disables monitoring; `0` is rejected because `process.kill(0, 0)` signals the caller's own process group:

```bash
# Explicit --owner-pid wins over the grandparent heuristic (applied last so an
# explicit value is not cleared by the Windows detection above). 'none'
# disables owner monitoring entirely; 0 is refused (signals the whole group).
if [[ -n "$OWNER_PID_OVERRIDE" ]]; then
  if [[ "$OWNER_PID_OVERRIDE" == "none" ]]; then
    OWNER_PID=""
  elif [[ "$OWNER_PID_OVERRIDE" =~ ^[0-9]+$ ]] && [[ "$OWNER_PID_OVERRIDE" -ge 1 ]]; then
    OWNER_PID="$OWNER_PID_OVERRIDE"
  else
    echo "{\"error\": \"--owner-pid must be a positive integer or none\"}"
    exit 1
  fi
fi
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash superpowers/tests/brainstorm-server/start-server.test.sh`
Expected: all PASS (original platform checks plus the new flag section).

Run: `bash superpowers/tests/brainstorm-server/windows-lifecycle.test.sh && bash superpowers/tests/brainstorm-server/stop-server.test.sh`
Expected: all PASS (owner-PID ordering unchanged for the Windows path).

- [ ] **Step 5: Commit**

```bash
cd /home/jbosch/LSST/tkt2/superpowers
git add skills/brainstorming/scripts/start-server.sh tests/brainstorm-server/start-server.test.sh
git commit -m "Add lifecycle flags to start-server.sh: idle 0, owner-pid none, close-grace, no-browser-timeout"
```

---

### Task 4: visual-companion.md tab-lifecycle guidance

**Files:**

- Modify: `superpowers/skills/brainstorming/visual-companion.md` (lines 105–106 and the last sentence of line 130)

**Interfaces:**

- Consumes: the `server-stopped` reasons from Task 2 (`browser-closed`).
- Produces: agent-facing behavior contract (restart only with a new screen; never resurrect after the human closes the tab).

- [ ] **Step 1: Replace the host-lifetime sentence** (lines 105–106, ending the `tkt workspaces` paragraph):

From:

```
the server down. The host owns the server's lifetime: it self-exits after
its idle timeout (4 hours by default), independent of your tool process.
```

To:

```
the server down. The host owns the server's lifetime: it self-exits about a
minute after the human closes the last browser tab, or shortly after startup
if no browser ever connects — independent of your tool process. There is no
idle cap while a tab is open: the human may be reading a screen for days.
```

- [ ] **Step 2: Replace the restart-loop sentence** — in the Step-1 bullet of "The Loop" (line 130), replace the final sentence (`The server auto-exits after 4 hours idle (configurable with \`--idle-timeout-minutes\`).`) with:

```
A `server-stopped` whose reason is `browser-closed` means the human closed
the tab and is done: do not restart until you have a genuinely new screen to
push (a reopened tab reconnects on its own; `--open` relaunches the browser).
```

- [ ] **Step 3: Verify**

Run: `grep -n "browser-closed" superpowers/skills/brainstorming/visual-companion.md`
Expected: one match, in the restart bullet. Also `grep -n "4 hours" superpowers/skills/brainstorming/visual-companion.md` must return nothing.

- [ ] **Step 4: Commit**

```bash
cd /home/jbosch/LSST/tkt2/superpowers
git add skills/brainstorming/visual-companion.md
git commit -m "Document tab-close lifecycle for the visual companion"
```

---

### Task 5: tkt BrainstormManager — detached launch, owner off, idle off by default

**Repo/branch:** tkt2 `feature/brainstorm-lifecycle` (current branch).

**Files:**

- Modify: `tkt/_sessionmanagers.py` (module docstring lines 29–30; `BrainstormManager` class docstring lines 111–115; `start()` lines 206–257; `_is_alive` line 191 → `@staticmethod`)
- Modify: `tkt/_session_tools.py` (`brainstorm_server` docstring lines 160–164; body comment lines 171–173)
- Test: `tests/test_session_managers.py` (rewrite `test_brainstorm_start_launches_launcher`, delete `test_brainstorm_start_owner_pid_is_passed_through`, add `test_brainstorm_start_idle_timeout_passthrough`)

**Interfaces:**

- Consumes: launcher CLI contract from Task 3 (`--owner-pid none`, `--idle-timeout-minutes 0`). Unit tests mock `subprocess.run`, so this task does not require the fork merge.
- Produces: `BrainstormManager.start(self, project_dir: str, *, port: int | None = None, idle_timeout_minutes: int | None = None, open_browser: bool = True) -> dict[str, Any]` — **no `owner_pid` parameter**; module constant `_BRAINSTORM_DEFAULT_IDLE_MINUTES = 0`; `_is_alive` / `_read_pid` / `_read_info` are `@staticmethod`s (Task 6 calls them unqualified). Task 6's cleanup hook consumes them.

- [ ] **Step 1: Write the failing tests** — in `tests/test_session_managers.py`:

1a. Replace `test_brainstorm_start_launches_launcher` (lines 126–155) with:

```python
def test_brainstorm_start_launches_launcher(tmp_path):
    """start() launches detached, with owner monitoring and idle cap off."""
    from tkt._sessionmanagers import BrainstormManager

    mgr = BrainstormManager(root=str(tmp_path))
    started = {
        "type": "server-started",
        "port": 52999,
        "url": "http://localhost:52999/?key=k2",
        "screen_dir": str(tmp_path / ".superpowers" / "brainstorm" / "9" / "content"),
        "state_dir": str(tmp_path / ".superpowers" / "brainstorm" / "9" / "state"),
    }
    completed = sp.CompletedProcess(args=[], returncode=0, stdout=json.dumps(started) + "\n", stderr="")
    with (
        mock.patch("tkt._sessionmanagers.subprocess.run", return_value=completed) as run,
        mock.patch("tkt._sessionmanagers._find_brainstorm_script", return_value="/fake/start-server.sh"),
    ):
        info = mgr.start(".", port=52999)
    assert info == started
    argv = run.call_args[0][0]
    env = run.call_args.kwargs["env"]
    assert argv[:2] == ["bash", "/fake/start-server.sh"]
    assert argv[argv.index("--project-dir") + 1] == os.path.realpath(tmp_path)
    # The human's browser tab owns the lifecycle: no owner-PID anchor, no
    # idle cap by default, and its own process group so the bridge's killpg
    # at sandbox teardown cannot reach it.
    assert argv[argv.index("--owner-pid") + 1] == "none"
    assert argv[argv.index("--idle-timeout-minutes") + 1] == "0"
    assert run.call_args.kwargs["start_new_session"] is True
    assert "--open" in argv
    assert env["BRAINSTORM_PORT"] == "52999"
```

1b. Delete `test_brainstorm_start_owner_pid_is_passed_through` (lines 158–175) — the parameter is removed; the browser tab is the only lifecycle anchor (spec §7).

1c. Append:

```python
def test_brainstorm_start_idle_timeout_passthrough(tmp_path):
    """An explicit idle_timeout_minutes replaces the disabled default."""
    from tkt._sessionmanagers import BrainstormManager

    mgr = BrainstormManager(root=str(tmp_path))
    completed = sp.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps({"type": "server-started", "state_dir": str(tmp_path)}) + "\n",
        stderr="",
    )
    with (
        mock.patch("tkt._sessionmanagers.subprocess.run", return_value=completed) as run,
        mock.patch("tkt._sessionmanagers._find_brainstorm_script", return_value="/fake/start-server.sh"),
    ):
        mgr.start(".", idle_timeout_minutes=30)
    argv = run.call_args[0][0]
    assert argv[argv.index("--idle-timeout-minutes") + 1] == "30"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_session_managers.py -v -k "brainstorm_start"`
Expected: the rewritten launcher test FAILS (`ValueError` from `argv.index("--owner-pid")` — absent today; and `start_new_session` is not passed). `test_brainstorm_start_idle_timeout_passthrough` PASSes at baseline (passthrough already works) — it guards against the new default logic swallowing explicit values. Deleting `test_brainstorm_start_owner_pid_is_passed_through` must not mask anything: the `owner_pid` kwarg has no other callers (verified: `brainstorm_server` in `_session_tools.py` never passed it).

- [ ] **Step 3: Implement** — in `tkt/_sessionmanagers.py`:

3a. Module docstring (lines 29–30): replace

```
Servers they start are detached and outlive the MCP connection that spawned
them; they die by explicit close, idle timeout, the boot sweep, or ``tkt rm``.
```

with

```
Servers they start are detached and outlive the MCP connection and the
sandbox that spawned them. Companion servers exit when the human closes the
last browser tab, or when no browser ever connects; Plannotator sessions
follow their own lifecycle. Both are also reaped by explicit close and by
``tkt rm``.
```

3b. `BrainstormManager` class docstring (lines 111–115): replace

```
    Lifetime is normally bounded by the companion server's own idle timeout
    (and the boot sweep or ``tkt rm``) rather than by the MCP connection that
    spawned it, so a session survives reconnects.  ``start()`` optionally
    anchors the server's lifetime to a specific process via ``--owner-pid``,
    for callers that want it to self-exit when their process dies.
```

with

```
    Lifetime belongs to the human's browser: the server exits a short grace
    after the last tab closes, or when no browser ever connects.  It is never
    bound to the MCP connection or the sandbox, so it survives reconnects and
    sandbox teardown; ``stop()`` and ``tkt rm`` reap it early.
```

3c. Add the module constant next to `_BRAINSTORM_START_TIMEOUT_S` (line 57):

```python
# tkt runs companions tab-close-driven: the idle reaper is off (0) unless the
# agent explicitly asks for a cap.
_BRAINSTORM_DEFAULT_IDLE_MINUTES = 0
```

3d. Make `_is_alive` a static method (line 191): add `@staticmethod` and change `def _is_alive(self, state_dir: str | None) -> bool:` to `def _is_alive(state_dir: str | None) -> bool:`. (`_read_pid` and `_read_info` are already `@staticmethod`s. Instance calls `self._is_alive(...)` keep working.)

3e. Replace `start()` (lines 206–257) with:

```python
    def start(
        self,
        project_dir: str,
        *,
        port: int | None = None,
        idle_timeout_minutes: int | None = None,
        open_browser: bool = True,
    ) -> dict[str, Any]:
        """Start the companion server, or return the live session's info.

        The launcher script does the backgrounding (``nohup``/``disown``) and
        waits for the server's startup JSON, so this call returns once the
        server is confirmed up (or failed).  A still-running server for this
        project is reused rather than duplicated.

        The server's lifetime is the human's browser tab: owner monitoring is
        disabled (``--owner-pid none``) and the idle reaper is off unless
        ``idle_timeout_minutes`` is given, so closing the tab — not a client
        process dying — is what reaps it.
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
        argv = [
            "bash",
            _find_brainstorm_script(),
            "--project-dir",
            project,
            "--owner-pid",
            "none",
            "--idle-timeout-minutes",
            str(
                int(idle_timeout_minutes)
                if idle_timeout_minutes is not None
                else _BRAINSTORM_DEFAULT_IDLE_MINUTES
            ),
        ]
        if open_browser:
            argv += ["--open"]
        # start_new_session: the companion must outlive the bridge. Without
        # it the server inherits the session-listener socat's process group
        # and _stop_bridge's killpg signal-kills it at sandbox teardown.
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_BRAINSTORM_START_TIMEOUT_S,
            env=env,
            check=False,
            start_new_session=True,
        )
        started = _last_json_object(proc.stdout)
        if started is None:
            detail = proc.stdout.strip() or proc.stderr.strip() or f"exit code {proc.returncode}"
            raise RuntimeError(f"brainstorm server failed to start: {detail}")
        if "error" in started:
            return started
        self._sessions[project] = str(started["state_dir"])
        return started
```

3f. In `tkt/_session_tools.py`, update the `brainstorm_server` docstring's `idle_timeout_minutes` entry (lines 160–161) to:

```
            idle_timeout_minutes: "start" only; auto-shutdown after this many
                idle minutes. Default is no idle cap — the server exits when
                the human closes the browser tab (or no browser ever
                connects), so a tab left open for days keeps the session.
```

and the body comment (lines 171–173) to:

```python
                # Owner monitoring and the idle cap are off on purpose: the
                # companion's lifecycle is the human's browser tab (last tab
                # closed → exit), never a client process.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_session_managers.py -v`
Expected: all PASS (including the untouched reuse/error tests).

- [ ] **Step 5: Lint and commit**

```bash
cd /home/jbosch/LSST/tkt2
ruff check . && ruff format --check . && mypy tkt/
git add tkt/_sessionmanagers.py tkt/_session_tools.py tests/test_session_managers.py
git commit -m "Detach the visual companion from the bridge process group and anchor its lifecycle to the browser tab"
```

---

### Task 6: tkt rm reaps its workspace's companion servers

**Files:**

- Modify: `tkt/_sessionmanagers.py` (add `cleanup_workspace_brainstorm` after `cleanup_workspace_sessions`, ~line 663)
- Modify: `tkt/sandbox.py` (`Sandbox.remove`, lines 437–441)
- Test: `tests/test_session_managers.py` (append)

**Interfaces:**

- Consumes: `BrainstormManager._is_alive` / `._read_pid` as `@staticmethod`s (Task 5); the `_brainstorm_session` test helper (existing, line 59).
- Produces: `cleanup_workspace_brainstorm(workspace: str) -> int` (count of SIGTERMed servers), called by `Sandbox.remove` alongside `cleanup_workspace_sessions`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_session_managers.py`:

```python
def _spawn_sleep():
    """A disposable live process to stand in for a companion server pid."""
    return sp.Popen(["sleep", "30"])


def test_cleanup_workspace_brainstorm_kills_live_skips_dead(tmp_path):
    """Only sessions with a live pid and no stop marker are terminated."""
    from tkt._sessionmanagers import cleanup_workspace_brainstorm

    live = _spawn_sleep()
    _brainstorm_session(tmp_path, "1-1", pid=live.pid)
    dead = _spawn_sleep()
    dead.kill()
    dead.wait()
    _brainstorm_session(tmp_path, "2-2", pid=dead.pid)
    _brainstorm_session(tmp_path, "3-3", pid=dead.pid, stopped=True)
    _brainstorm_session(tmp_path, "4-4")  # no pid file at all

    killed = cleanup_workspace_brainstorm(str(tmp_path))
    try:
        assert killed == 1
        deadline = time.monotonic() + 5.0
        while live.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert live.poll() is not None, "the live companion must have been SIGTERMed"
    finally:
        live.kill()


def test_cleanup_workspace_brainstorm_missing_workspace(tmp_path):
    """No .superpowers dir is a clean no-op."""
    from tkt._sessionmanagers import cleanup_workspace_brainstorm

    assert cleanup_workspace_brainstorm(str(tmp_path / "nope")) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_session_managers.py -v -k cleanup_workspace_brainstorm`
Expected: FAIL with `ImportError: cannot import name 'cleanup_workspace_brainstorm'`.

- [ ] **Step 3: Implement** — in `tkt/_sessionmanagers.py`, add after `cleanup_workspace_sessions`:

```python
def cleanup_workspace_brainstorm(workspace: str) -> int:
    """``tkt rm`` hook: terminate companion servers started under ``workspace``.

    Their content dirs live inside the workspace and vanish with it, so a
    surviving server would only serve a corpse until its tab closes.
    """
    base = Path(str(Path(workspace).resolve()), ".superpowers", "brainstorm")
    killed = 0
    for state_dir in sorted(p for p in base.glob("*/state") if p.is_dir()):
        if not BrainstormManager._is_alive(str(state_dir)):
            continue
        pid = BrainstormManager._read_pid(str(state_dir))
        if pid is None:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue
        killed += 1
    return killed
```

And in `tkt/sandbox.py`, extend the rm hook (lines 437–441):

```python
    def remove(self, directory: str) -> None:
        """Kill session servers for this workspace (``tkt rm`` hook)."""
        from ._sessionmanagers import cleanup_workspace_brainstorm, cleanup_workspace_sessions

        cleanup_workspace_sessions(directory)
        cleanup_workspace_brainstorm(directory)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_session_managers.py -v`
Expected: all PASS — Plannotator's `cleanup_workspace_sessions` and sweep tests untouched (spec R5).

- [ ] **Step 5: Lint and commit**

```bash
cd /home/jbosch/LSST/tkt2
ruff check . && ruff format --check . && mypy tkt/
git add tkt/_sessionmanagers.py tkt/sandbox.py tests/test_session_managers.py
git commit -m "Reap workspace companion servers on tkt rm"
```

---

### Task 7: fork merge, submodule pin bump, end-to-end verification

**Files:**

- Modify: `superpowers` (submodule gitlink — pin bump only, after the human merges the fork branch)

**Interfaces:**

- Consumes: merged `brainstorm-lifecycle` commits on the fork's `main` (Tasks 1–4); tkt Tasks 5–6 code.
- Produces: tkt2 branch with submodule + code aligned to the merged fork scripts; verified end-to-end companion lifecycle.

- [ ] **Step 1: Hand off the fork merge (human gate — stop here until done)**

Push the fork branch and ask the human to review the four fork commits and merge `brainstorm-lifecycle` into `TallJimbo/superpowers` `main`:

```bash
cd /home/jbosch/LSST/tkt2/superpowers
git log --oneline brainstorm-lifecycle   # four commits, base 1e995b8
git push origin brainstorm-lifecycle
```

Do **not** bump the pin against the unmerged branch (spec §2.7). Wait for the human's confirmation that the merge landed.

- [ ] **Step 2: Bump the submodule pin to the merged main**

```bash
cd /home/jbosch/LSST/tkt2/superpowers
git fetch origin
git checkout main && git pull --ff-only origin main
cd /home/jbosch/LSST/tkt2
git submodule status   # expect the new main sha, no +/- prefix surprises
```

- [ ] **Step 3: Full verification against the merged scripts**

```bash
cd /home/jbosch/LSST/tkt2
node superpowers/tests/brainstorm-server/lifecycle.test.js
bash superpowers/tests/brainstorm-server/start-server.test.sh
python -m pytest tests/test_session_managers.py tests/test_sandbox.py -v
ruff check . && ruff format --check . && mypy tkt/
```

Expected: all green.

- [ ] **Step 4: End-to-end companion smoke (no browser needed)**

```bash
cd /home/jbosch/LSST/tkt2
python - <<'EOF'
import json, os, subprocess, tempfile
from tkt._sessionmanagers import BrainstormManager

ws = tempfile.mkdtemp(prefix="brainstorm-smoke-")
mgr = BrainstormManager(root=ws)
info = mgr.start(".", open_browser=False)
assert info["type"] == "server-started", info
pid = int(open(os.path.join(info["state_dir"], "server.pid")).read().strip())
# Detached: the companion must not share our process group (bridge killpg model).
assert os.getpgid(pid) != os.getpgid(os.getpid()), "companion is still in the launcher group"
# Idle reaper off, reported by the server itself.
assert info["idle_timeout_ms"] == 0, info
print(json.dumps(mgr.status("."), indent=2))
print("stopped:", json.dumps(mgr.stop(".")))
EOF
```

Expected: `server-running` from `status`, `server-stopped` from `stop`, both asserts pass.

- [ ] **Step 5: Commit the pin bump**

```bash
cd /home/jbosch/LSST/tkt2
git add superpowers
git commit -m "Bump superpowers pin to the brainstorm-lifecycle merge"
```

- [ ] **Step 6: Remind the human (do not run yourself)**

`tkt install-harness` must be run by the human after both merges: it refreshes `~/.agents/skills/brainstorming/` — the launcher fallback for `BrainstormManager` and the `visual-companion.md` the in-sandbox agents read.

---

## Resolved during planning

Post-review amendments (final whole-branch review, tkt2 `8878428` + fork `ec87aeb`):
`BrainstormManager.status` now discovers `server-stopped`-only session dirs
(`_scan_state_dir` ranks on either marker) and returns the reap `reason`/`timestamp`;
`_format_brainstorm_info` renders the stopped type with its reason (no "not … yet"
after a reap). `cleanup_workspace_brainstorm` scans both the workspace-root and the
depth-1 (`*/.superpowers/brainstorm/*/state`) session patterns, deduped. In
`server.cjs`, `shutdown()` clears the reaper timers after the socket-destroy loop
(the destroy loop can re-arm via `removeClient`), and two comments were reworded.
The task texts below predate these amendments where they touch those spots.

- **`shutdownFn` indirection + `shuttingDown` guard (server.cjs):** `shutdown()` closes over `server`/`watcher` inside `startServer()`, but the grace timer arms from module-scope `removeClient()` — a module-level `let shutdownFn` assigned once in `startServer()` is the minimal bridge; the guard covers grace-timer/lifecycle-check races.
- **Fork branch base:** from the pinned commit `1e995b8`, not possibly-ahead `origin/main`.
- **`--owner-pid 0` rejected in the script too** (not just unused by tkt): `process.kill(0, 0)` signals the caller's own process group; the CLI must make the footgun unreachable.
- **Existing tests rewritten rather than kept green:** `test_brainstorm_start_launches_launcher` pinned `--owner-pid not in argv` (spec §7 deletes that behavior) and `test_brainstorm_start_owner_pid_is_passed_through` pinned the removed `owner_pid` parameter.
- **Reaper default polarity:** lifetime-shortening reapers default-on in the fork; the lifetime-extending idle-off is tkt-opt-in only (fork idle default stays 4 h) — operationalizes spec R6.
- **`_is_alive` → `@staticmethod`:** already uses no instance state; lets `cleanup_workspace_brainstorm` reuse it instead of duplicating the liveness probe.
