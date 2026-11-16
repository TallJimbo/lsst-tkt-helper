# Plannotator Adapter Rework — Implementation Plan

Date: 2026-11-16
Spec: `docs/superpowers/specs/2026-11-16-plannotator-rework-design.md` (commit `f6c4c99`)
Branch: `feature/dm-memory` (continue on it; do not branch from `main` — see spec/AGENTS.md)

## What we are building

Replace the Plannotator result-retrieval path with the documented CLI contract:

1. `plannotator_open` (host tool) spawns `plannotator annotate|review … --json`
   detached, with **stdout redirected to a tkt-owned file**. That file *is* the
   result artifact — a decision JSON like
   `{"decision":"annotated","feedback":"…","annotationCount":2,"target":"…"}`.
2. Restricted sandbox runs bind-mount `~/.local/state/tkt/plannotator/`
   read-only at the same path and export `TKT_PLANNOTATOR_RESULTS`. The agent's
   only way to get feedback is `read` on the path the tool handed it. There is
   no `result`, `status`, or `close` action any more.
3. The Inbox is the upstream `plannotator inbox mcp` tool list re-exposed
   verbatim, so the surface tracks upstream releases without tkt code.

Delete, do not rewrite: the feedback-store scan, mtime heuristics, project-label
guessing, Plannotator-session-id routing, pgid-only teardown, the
`plannotator_inbox` action map, and `PLANNOTATOR_SKILL_PATCHES`.

## Ground rules for every task

- Work from the repo root (`/home/jbosch/LSST/tkt2`) on `feature/dm-memory`.
- TDD: write the failing test first, run it, then implement.
- Before each commit: `ruff check . && ruff format --check . && mypy tkt/ && python -m pytest`.
  Baseline on this branch is 392 passing tests; the count changes only where this
  plan says a test is deleted or added.
- ruff: line length 110, numpy docstring convention, doc-line length 79. New
  `.py` files copy the BSD-3 header verbatim from `tkt/_session_tools.py`.
- Comments and docstrings describe the code as it is, for a reader arriving via
  `git blame` in two years. No references to this plan, to the old design, or to
  what the previous implementation did.
- The `plannotator` binary and `~/.plannotator` are **not visible from the agent
  sandbox**. Anything that needs them is Task 7 (the human runs it).

## File map

| File | Change |
|---|---|
| `tkt/_sessionmanagers.py` | New result-dir/handle/registry layer (Task 1); `PlannotatorManager.open` rewrite; delete `result()`, `status()`, `close()`, `_status_ts`, `_pgid_alive`-only teardown, old `session_registry_dir` layout (Task 2) |
| `tkt/_session_tools.py` | `plannotator` action enum → `plannotator_open`; delete `plannotator_inbox`, `INBOX_ACTION_TOOLS`, `_inbox_call`; keep `safe_subpath`, `_git_diff_spec` (Task 3) |
| `tkt/_inbox_proxy.py` | **New.** Verbatim re-expose of `plannotator inbox mcp` (Task 4) |
| `tkt/sandbox.py` | Read-only mount + `TKT_PLANNOTATOR_RESULTS` for restricted runs (Task 5) |
| `tkt/AGENTS.md.in` | Rewrite the host-session paragraph (Task 6) |
| `tkt/install.py` | Delete `PLANNOTATOR_SKILL_PATCHES` and its application sites (Task 6) |
| `AGENTS.md` (repo root) | Update the one sentence describing the adapter (Task 6) |
| `tests/fixtures/plannotator/` | **New.** Golden stdout captures copied from the probe corpus (Task 2) |
| `tests/test_session_managers.py`, `tests/test_session_tools.py`, `tests/test_sandbox.py`, `tests/test_install.py`, `tests/test_mcp_server.py` | Follow the above |

Unchanged on purpose: the session-socket bridge, `tkt/session_server.py`,
`BrainstormManager`, `tkt/mcp_server.py:740-742` (Zed shares
`build_session_tools`), `PLANNOTATOR_HOST_ONLY_SKILLS`.

---

## Task 1 — Result directory, handles, registry, sweep and cleanup

**File:** `tkt/_sessionmanagers.py` (module-level layer used by the manager,
`tkt rm`, and the sandbox launch sweep)
**Tests:** `tests/test_session_managers.py`

Everything in this task lives in the module-level block that currently starts at
`_PLANNOTATOR_TIMEOUT_S` (line ~315) and runs through `cleanup_workspace_sessions`
(line ~428). `BrainstormManager` (lines 102-312) is untouched.

### 1.1 Replace the registry layer

Delete `session_registry_dir`, `_registry_entries`, `_write_entry`, `_status_ts`,
`sweep_session_registry`, `cleanup_workspace_sessions` as they stand, and put this
in their place (keep `_PLANNOTATOR_TIMEOUT_S`, `_DISCOVER_POLL_S`,
`_DISCOVER_BUDGET_S`, and keep `_pgid_alive` exactly as it is):

```python
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


def _match(sessions: list[dict[str, Any]] | None, target: str) -> dict[str, Any] | None:
    """The live session whose target resolves to ``target``, if any.

    Sessions whose target is a URL (a web review surface) never match, so a
    PR-URL review is always a fresh spawn.
    """
    if not sessions:
        return None
    resolved = str(Path(target).resolve())
    for s in sessions:
        if str(s.get("target", "")) == resolved:
            return s
    return None


def _terminate(entry: dict[str, Any], listed: list[dict[str, Any]] | None) -> None:
    """TERM the CLI process group and the session server recorded in ``entry``.

    The CLI's own process group never contains the session server: Plannotator
    spawns it outside the CLI's group, so killing the CLI leaves the browser
    session serving.  An entry with no recorded server pid (its URL came from
    the stderr ready line before the CLI registered the session) gets its
    server from ``listed``, the current `sessions --json` output, matched on
    the entry's target.  A pid the listing does not confirm is never
    signalled, which is what keeps pid reuse from making us hit an unrelated
    process; with no recorded pid and nothing listed, there is nothing to
    signal.
    """
    pgid = int(entry.get("pgid", 0) or 0)
    if pgid > 0:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    server_pid = int(entry.get("server_pid", 0) or 0)
    if server_pid <= 0:
        live = _match(listed, str(entry.get("target", "")))
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
    still shows its target, and an unusable listing never makes any entry
    look dead.  A live session server outlives its sandbox on purpose — the
    human may still be reviewing.
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
            if listed is None or _match(listed, str(entry.get("target", ""))) is not None:
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
```

`_pgid_alive` keeps its current body; only its callers change. Its comment about
"result() 'pending'" refers to a function this rework deletes — reword it to
"a dead CLI would make an entry look live".

### 1.2 Tests

Replace the registry-path references throughout `tests/test_session_managers.py`
(`session_registry_dir()` → `plannotator_result_dir()`, `f"{sid}.json"` →
`f"{handle}.registry.json"`), delete the tests for the deleted surface
(`_status_ts`, `status`, `result`, `close` — Tasks 1-2 remove them in one pass so
the file never holds both), and add:

```python
def test_claim_handle_is_unique_per_call(fake_cli):
    """Two opens of the same target in the same second get distinct handles."""
    from tkt._sessionmanagers import _claim_handle

    first = _claim_handle("/ws/plan.md")
    second = _claim_handle("/ws/plan.md")
    assert first != second
    assert first.startswith("pna-plan-md-")
    assert (Path(plannotator_result_dir()) / f"{first}.registry.json").exists()


def test_claim_handle_slug_survives_odd_names(fake_cli):
    from tkt._sessionmanagers import _handle_slug

    assert _handle_slug("My Plan (v2)!!") == "my-plan-v2"
    assert _handle_slug("!!!") == "session"


def test_sweep_drops_dead_entry_keeps_captures(fake_cli):
    """A finished session loses its entry but keeps its decision file."""
    from tkt._sessionmanagers import _write_entry

    out = Path(plannotator_result_dir()) / "pna-dead-1.out"
    out.write_text('{"decision":"approved"}')
    _write_entry("pna-dead-1", {"handle": "pna-dead-1", "workspace": "/ws", "pgid": 0, "server_pid": 0})
    assert sweep_session_registry() == 1
    assert out.exists()
    assert not (Path(plannotator_result_dir()) / "pna-dead-1.registry.json").exists()


def test_sweep_keeps_entry_with_live_server(fake_cli):
    from tkt._sessionmanagers import _write_entry

    live = subprocess.Popen(["sleep", "30"])
    try:
        _write_entry("pna-live-1", {"handle": "pna-live-1", "workspace": "/ws", "pgid": 0, "server_pid": live.pid})
        assert sweep_session_registry() == 0
        assert (Path(plannotator_result_dir()) / "pna-live-1.registry.json").exists()
    finally:
        live.kill()
        live.wait()


def test_sweep_prunes_old_captures_and_legacy_dir(fake_cli):
    from tkt._sessionmanagers import _legacy_registry_dir, _write_entry

    old = Path(plannotator_result_dir()) / "pna-old-1.out"
    old.write_text("{}")
    stamp = time.time() - 31 * 24 * 3600
    os.utime(old, (stamp, stamp))
    legacy = Path(_legacy_registry_dir())
    legacy.mkdir(parents=True)
    (legacy / "pn-stale.json").write_text("{}")
    _write_entry("pna-old-1", {"handle": "pna-old-1", "workspace": "/ws", "pgid": 0, "server_pid": 0})
    sweep_session_registry()
    assert not old.exists()
    assert not legacy.exists()


def test_cleanup_terminates_server_pid(fake_cli):
    """``tkt rm`` reaches the server: killing only the CLI leaves it serving."""
    from tkt._sessionmanagers import _write_entry, cleanup_workspace_sessions

    server = subprocess.Popen(["sleep", "30"])
    _sessions_json(
        fake_cli,
        [{"target": "/ws/plan.md", "url": "http://localhost:4321/", "port": 4321, "pid": server.pid}],
    )
    _write_entry(
        "pna-plan-md-1",
        {"handle": "pna-plan-md-1", "workspace": str(fake_cli), "target": "/ws/plan.md", "server_pid": server.pid},
    )
    assert cleanup_workspace_sessions(str(fake_cli)) == 1
    assert server.wait(timeout=5).returncode < 0  # terminated by a signal
    assert not (Path(plannotator_result_dir()) / "pna-plan-md-1.registry.json").exists()


def test_cleanup_skips_server_pid_the_cli_no_longer_lists(fake_cli):
    """Pid reuse must not make us signal an unrelated process."""
    from tkt._sessionmanagers import _write_entry, cleanup_workspace_sessions

    unrelated = subprocess.Popen(["sleep", "30"])
    try:
        _sessions_json(fake_cli, [])  # listing works, and our pid is not in it
        _write_entry(
            "pna-plan-md-2",
            {"handle": "pna-plan-md-2", "workspace": str(fake_cli), "target": "/ws/plan.md", "server_pid": unrelated.pid},
        )
        assert cleanup_workspace_sessions(str(fake_cli)) == 1
        assert unrelated.poll() is None
    finally:
        unrelated.kill()
        unrelated.wait()
```

`tests/test_sandbox.py`'s sweep-related test (if it asserts on the old registry
dir) is updated here too. Run the gates, then commit:

```
git add tkt/_sessionmanagers.py tests/test_session_managers.py tests/test_sandbox.py
git commit -m "Base Plannotator sessions on captured stdout in a shared result dir

Sessions are keyed by a tkt-minted handle whose capture and registry files
live in ~/.local/state/tkt/plannotator. Teardown signals the session-server
pid as well as the CLI process group, since the server is spawned outside the
CLI's group. Sweep drops entries whose processes are gone and prunes captures
past 30 days."
```

---

## Task 2 — `PlannotatorManager.open`: spawn with stdout capture

**File:** `tkt/_sessionmanagers.py` (`PlannotatorManager`, lines ~431-701)
**Tests:** `tests/test_session_managers.py`, new `tests/fixtures/plannotator/`

### 2.1 Golden fixtures first

Copy the probe corpus captures — real Plannotator 0.28.65 stdout, not invented
JSON — and record where each came from:

```sh
mkdir -p tests/fixtures/plannotator
A=investigations/plannotator-rework/artifacts
cp $A/rework1-p2-gated-approve/stdout.txt   tests/fixtures/plannotator/annotate-approved.json
cp $A/rework1-p2-gated-dismiss/stdout.txt   tests/fixtures/plannotator/annotate-dismissed.json
cp $A/rework1-p2-review-approve/stdout.txt  tests/fixtures/plannotator/review-approved.json
cp $A/rework1-p2-review-base/stdout.txt     tests/fixtures/plannotator/review-annotated.json
: > tests/fixtures/plannotator/annotate-closed-empty.json   # plain close: CLI exits with no output
```

The `annotated` shape (comments submitted) has no on-disk capture in the corpus —
the tiebreaker run's stdout was recorded in the session log instead. Write it
verbatim:

```sh
cat > tests/fixtures/plannotator/annotate-annotated.json <<'EOF'
{"decision":"annotated","feedback":"# File Feedback\n\nI've reviewed this file and have 2 pieces of feedback:\n\n## 1. General feedback about the file\n> There is no \"submit feedback\" first-class option, just \"Approve\" and \"Request changes\".  This is \"Request changes\".\n\n## 2. (lines 3–6) Feedback on: \"annotate\"\n> comment on tiebreaker for gated-submit\n\n---\n","annotationCount":2,"target":"/home/jbosch/LSST/tkt2/investigations/plannotator-rework/artifacts/tiebreak-gated-submit/plan-probe.md"}
EOF
```

Task 7 re-runs a commented submit for real; if that capture differs, it replaces
this file byte-for-byte (that is the point of keeping the fixtures as files).

`tests/fixtures/plannotator/README.md` (provenance for the two-year-later reader):

```markdown
# Plannotator decision-JSON fixtures

Captured stdout of `plannotator` 0.28.65 resolving a session, one file per
decision shape. Generated by the probes in `investigations/plannotator-rework`
(see its README); `annotate-annotated.json` is the stdout of a gated
request-changes submit recorded on 2026-11-16.

`annotate` (`--json`, with or without `--gate`) emits
`{decision, feedback?, annotationCount?, target}`; `review --json` emits
`{decision, message}`. `decision` is `approved`, `annotated`, or `dismissed`
(closed without submitting). Closing a session without resolving it exits with
an empty stdout.
```

### 2.2 Fake CLI replacement

The fake in `tests/test_session_managers.py` (`FAKE_CLI`, line ~366) is replaced
wholesale — it now behaves like the real CLI: registers itself so `open()` can
discover the session, prints the readiness line on stderr, and prints the
decision JSON on stdout when the session resolves:

```python
FAKE_CLI = """#!/usr/bin/env bash
# Fake plannotator for tests. State lives in $FAKE_STATE.
cmd="$1"; shift
case "$cmd" in
  sessions)
    if [ -f "$FAKE_STATE/sessions.json" ]; then cat "$FAKE_STATE/sessions.json"; else echo "[]"; fi
    ;;
  annotate|review)
    printf '%s\\n' "$cmd" "$@" > "$FAKE_STATE/argv.txt"
    target="$1"
    port=$((41000 + ($$ % 1000)))
    # Self-register the way the real CLI does. $$ is the CLI's process-group
    # leader; the server Plannotator spawns shares the session entry.
    printf '[{"target":"%s","url":"http://localhost:%s/","port":%s,"pid":%s,"startedAt":"now"}]' \\
      "$target" "$port" "$port" "$$" > "$FAKE_STATE/sessions.json"
    echo "  Plannotator session ready: http://localhost:$port/" >&2
    if [ -n "$FAKE_DECISION" ]; then
      printf '%s' "$FAKE_DECISION"        # the human resolved it: decision JSON on stdout
    else
      sleep "${FAKE_SLEEP:-60}" &         # stand-in for a session still open
      echo $! > "$FAKE_STATE/sleep.pid"
      trap 'kill -TERM 0; exit 143' TERM INT
      wait
    fi
    ;;
  *) echo "unknown: $cmd" >&2; exit 2 ;;
esac
"""
```

(The `\\` escaping is because the script is inside a Python string literal.)
`FAKE_SESSION` is gone: registration is unconditional. `FAKE_DECISION` replaces
it as the opt-in for a session that resolves immediately, and `argv.txt` lets a
test assert the argv the manager built. `FAKE_REGISTER_DELAY_S` optionally
withholds that self-registration past the first discovery poll, which is how the
tests that need the listing to win the discovery race (and therefore a recorded
`server_pid`) stay deterministic.

### 2.3 The rewritten manager

Delete `result()`, `status()`, `close()`, the class's own `_sessions`/`_match`
(now module-level from Task 1) and the id-adoption block. What remains:

```python
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
        live = _match(_sessions_listing(), resolved)
        if live is not None:
            return self._attach(workspace, resolved, live, kind=kind, gate=gate, base=base)
        return self._spawn(workspace, resolved, kind=kind, gate=gate, base=base)

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
        comes back — including after a sandbox restart.  A session the human
        started from their own terminal has no capture we can read; it is
        registered anyway so workspace removal can reap its server.
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
            return {
                "type": "open",
                "handle": handle,
                "url": live.get("url"),
                "result_path": entry.get("out_path") if source == "spawned" else None,
                "err_path": entry.get("err_path"),
                "reused": True,
                "source": source,
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
        url, server_pid = self._discover(target, err_path)
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

    def _discover(self, target: str, err_path: str) -> tuple[str | None, int | None]:
        """Poll for the session's URL and, from the listing, its server pid.

        Each poll checks `sessions --json` first, then the CLI's stderr file:
        a PR-URL review's target never matches the listing, so its URL comes
        from the ``Plannotator session ready:`` line alone, and reading the
        line per-poll keeps that lookup off the budget's critical path.
        """
        deadline = time.monotonic() + _DISCOVER_BUDGET_S
        while True:
            live = _match(_sessions_listing(), target)
            if live is not None:
                pid = live.get("pid")
                return str(live.get("url")), int(pid) if isinstance(pid, int) else None
            try:
                with open(err_path, encoding="utf-8", errors="replace") as f:
                    ready = self._READY_RE.search(f.read())
            except OSError:
                ready = None
            if ready is not None:
                return ready.group(1), None
            if time.monotonic() >= deadline:
                return None, None
            time.sleep(_DISCOVER_POLL_S)
```

`datetime` (only used by the deleted `_status_ts`) comes out of the imports.

### 2.4 Tests

Keep and adapt `test_open_extra_roots_allows_target_outside_workspace`,
`test_open_target_outside_all_roots_raises`, `test_open_missing_binary_raises`,
`test_open_discovers_existing_session`, `test_open_spawns_when_absent_and_registers`;
delete `test_open_adopts_local_entry_after_late_registration` (the handle is minted
before spawning, so there is no late-registration window) and every
`result`/`status`/`close` test. Add:

```python
def test_open_captures_stdout_as_result(fake_cli, monkeypatch):
    """The decision JSON is the CLI's stdout, already in place at open."""
    monkeypatch.setenv("FAKE_DECISION", '{"decision":"approved","annotationCount":0,"target":"/x"}')
    ws = str(fake_cli)
    (fake_cli / "plan.md").write_text("# plan\n")
    out = PlannotatorManager().open(ws, str(fake_cli / "plan.md"))
    assert out["source"] == "spawned" and out["reused"] is False
    assert Path(out["result_path"]).read_text() == '{"decision":"approved","annotationCount":0,"target":"/x"}'
    assert out["result_path"] == str(Path(plannotator_result_dir()) / f"{out['handle']}.out")
    entry = json.loads((Path(plannotator_result_dir()) / f"{out['handle']}.registry.json").read_text())
    assert entry["pgid"] > 0 and entry["server_pid"] > 0 and entry["url"].startswith("http://localhost:")


def test_open_always_requests_json(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_DECISION", "{}")
    ws = str(fake_cli)
    (fake_cli / "plan.md").write_text("# plan\n")
    PlannotatorManager().open(ws, str(fake_cli / "plan.md"), gate=True)
    argv = (fake_cli / "state" / "argv.txt").read_text().splitlines()
    assert argv[0] == "annotate" and "--json" in argv and "--gate" in argv


def test_open_review_passes_base(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_DECISION", "{}")
    ws = str(fake_cli)
    (fake_cli / "pkg").mkdir()
    PlannotatorManager().open(ws, str(fake_cli / "pkg"), kind="review", base="main")
    argv = (fake_cli / "state" / "argv.txt").read_text().splitlines()
    assert argv[0] == "review" and "--json" in argv and argv[argv.index("--base") + 1] == "main"


def test_open_reuses_live_session_and_its_capture(fake_cli, monkeypatch):
    """A restart re-attaches to the live session and its existing capture."""
    monkeypatch.setenv("FAKE_DECISION", '{"decision":"approved"}')
    ws = str(fake_cli)
    (fake_cli / "plan.md").write_text("# plan\n")
    first = PlannotatorManager().open(ws, str(fake_cli / "plan.md"))
    second = PlannotatorManager().open(ws, str(fake_cli / "plan.md"))
    assert second["reused"] is True and second["source"] == "spawned"
    assert second["handle"] == first["handle"]
    assert second["result_path"] == first["result_path"]


def test_open_registers_human_started_session(fake_cli):
    """A session the human started is reattachable and reapeable, uncaptured."""
    ws = str(fake_cli)
    (fake_cli / "plan.md").write_text("# plan\n")
    target = str((fake_cli / "plan.md").resolve())
    server = subprocess.Popen(["sleep", "30"])
    try:
        _sessions_json(fake_cli, [{"target": target, "url": "http://localhost:4444/", "port": 4444, "pid": server.pid}])
        out = PlannotatorManager().open(ws, target)
        assert out["result_path"] is None and out["source"] == "discovered" and out["url"] == "http://localhost:4444/"
        entry = json.loads((Path(plannotator_result_dir()) / f"{out['handle']}.registry.json").read_text())
        assert entry["pgid"] == 0 and entry["server_pid"] == server.pid
    finally:
        server.kill()
        server.wait()


def test_open_without_listing_still_captures(fake_cli, monkeypatch):
    """A broken `sessions` listing costs the URL, never the result capture."""
    monkeypatch.setenv("FAKE_DECISION", '{"decision":"approved"}')
    monkeypatch.setattr("tkt._sessionmanagers._sessions_listing", lambda: None)
    monkeypatch.setattr("tkt._sessionmanagers._DISCOVER_BUDGET_S", 0.1)
    ws = str(fake_cli)
    (fake_cli / "plan.md").write_text("# plan\n")
    out = PlannotatorManager().open(ws, str(fake_cli / "plan.md"))
    assert out["url"] is None
    assert Path(out["result_path"]).read_text() == '{"decision":"approved"}'
    assert "Plannotator session ready:" in Path(out["err_path"]).read_text()


@pytest.mark.parametrize(
    "name,keys",
    [
        ("annotate-approved.json", {"decision", "annotationCount", "target"}),
        ("annotate-annotated.json", {"decision", "feedback", "annotationCount", "target"}),
        ("annotate-dismissed.json", {"decision", "target"}),
        ("review-approved.json", {"decision", "message"}),
        ("review-annotated.json", {"decision", "message"}),
    ],
)
def test_decision_fixture_shapes(name, keys):
    """The pinned stdout shapes the guidance and tests describe."""
    path = Path(__file__).parent / "fixtures" / "plannotator" / name
    decision = json.loads(path.read_text())
    assert decision["decision"] in {"approved", "annotated", "dismissed"}
    assert set(decision) == keys


def test_closed_session_capture_is_empty(fake_cli):
    """Closing without submitting leaves a complete, empty capture."""
    empty = Path(__file__).parent / "fixtures" / "plannotator" / "annotate-closed-empty.json"
    assert empty.read_text() == ""
```

Run the gates, then commit:

```
git add tkt/_sessionmanagers.py tests/test_session_managers.py tests/fixtures/plannotator
git commit -m "Capture the Plannotator decision JSON at spawn and key sessions by handle

open() returns a tkt handle, the session URL, and the path the decision JSON
will land at. Attaching to a session started outside tkt registers it for
teardown but has no capture to return; a listing failure costs only the URL,
since the URL also lands in the capture's stderr file. Plannotator session ids
no longer appear anywhere on this interface.
```

---

## Task 3 — One host tool: `plannotator_open`

**File:** `tkt/_session_tools.py`
**Tests:** `tests/test_session_tools.py`, `tests/test_mcp_server.py`

### 3.1 The tool

Delete the `plannotator` action-enum tool (lines ~206-282) and put this in its
place, keeping the `plannotator_mgr = PlannotatorManager()` line that precedes it:

```python
    @mcp.tool()
    def plannotator_open(
        target: str,
        kind: str = "annotate",
        gate: bool = False,
        base: str | None = None,
        description: str | None = None,  # present for human approvals of tool actions
    ) -> str:
        """Open a Plannotator review session on the host, where the human's browser reaches it.

        Hand the human the returned URL **verbatim** and end your turn: feedback
        is not pushed to you.  Their decision arrives as JSON at ``result_path``,
        a file you read once they say the review is in.  Never run the
        ``plannotator`` CLI yourself: a session started inside a sandbox is
        unreachable by design.

        Args:
            target: File or folder to annotate, file or folder to review, or a
                PR URL to review (absolute or workspace-relative). Non-URL
                paths must live inside the workspace or the host plan directory
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
            lines.append(
                f"When the human says the review is in, read `{info['result_path']}`. A missing file "
                "means the session is still open; an empty file means it closed without feedback; "
                "JSON that does not parse yet means the session has not resolved — read it again "
                "later rather than reporting a parse error."
            )
        return "\n".join(lines)
```

`_PLAN_DIR`, `safe_subpath`, `_json_block` and `_git_diff_spec` stay as they are.
`INBOX_ACTION_TOOLS`, `_inbox_call` and `plannotator_inbox` are deleted in Task 4,
which also wires the replacement in; if you land Task 3 alone, keep those intact
and the tree green (they are independent).

### 3.2 Tests

In `tests/test_session_tools.py`: point `test_session_tools_registered` at
`{"brainstorm_server", "plannotator_open"}`; rewrite
`test_plannotator_tool_delegates_and_contains` and
`test_plannotator_open_accepts_plan_dir_target` against `plannotator_open`
(`mcp._tool_manager.get_tool("plannotator_open")`, called with `target=...` only);
and add:

```python
def test_plannotator_open_tells_the_agent_how_to_pull(monkeypatch) -> None:
    """The result text is the pull contract: read the file, do not poll."""

    class FakeMgr:
        def open(self, workspace, target, **kw):
            return {
                "type": "open",
                "handle": "pna-plan-md-1",
                "url": "http://localhost:9/x",
                "result_path": "/state/pna-plan-md-1.out",
                "err_path": "/state/pna-plan-md-1.err",
                "reused": False,
                "source": "spawned",
            }

    from tkt import _session_tools

    monkeypatch.setattr(_session_tools, "PlannotatorManager", lambda: FakeMgr())
    mcp = MCPServer("t")
    build_session_tools(mcp, workspace="/ws")
    out = _call(mcp._tool_manager.get_tool("plannotator_open"), target="spec.md")
    assert "http://localhost:9/x" in out and "/state/pna-plan-md-1.out" in out
    assert "still open" in out and "without feedback" in out and "does not parse" in out


def test_plannotator_open_discovered_session_has_no_capture(monkeypatch) -> None:
    """A human-started session is reported honestly: nothing to read back."""

    class FakeMgr:
        def open(self, workspace, target, **kw):
            return {
                "type": "open",
                "handle": "pna-plan-md-1",
                "url": "http://localhost:9/x",
                "result_path": None,
                "err_path": None,
                "reused": True,
                "source": "discovered",
            }

    from tkt import _session_tools

    monkeypatch.setattr(_session_tools, "PlannotatorManager", lambda: FakeMgr())
    mcp = MCPServer("t")
    build_session_tools(mcp, workspace="/ws")
    out = _call(mcp._tool_manager.get_tool("plannotator_open"), target="spec.md")
    assert "started outside tkt" in out and "null" in out


def test_plannotator_open_without_url_points_at_stderr(monkeypatch) -> None:
    """Listing breakage costs the URL only; the stderr file carries it."""

    class FakeMgr:
        def open(self, workspace, target, **kw):
            return {
                "type": "open",
                "handle": "pna-plan-md-1",
                "url": None,
                "result_path": "/state/pna-plan-md-1.out",
                "err_path": "/state/pna-plan-md-1.err",
                "reused": False,
                "source": "spawned",
            }

    from tkt import _session_tools

    monkeypatch.setattr(_session_tools, "PlannotatorManager", lambda: FakeMgr())
    mcp = MCPServer("t")
    build_session_tools(mcp, workspace="/ws")
    out = _call(mcp._tool_manager.get_tool("plannotator_open"), target="spec.md")
    assert "/state/pna-plan-md-1.err" in out
```

In `tests/test_mcp_server.py` (line ~246) change the Zed-side assertion to
`{"brainstorm_server", "plannotator_open"} <= captured["names"]`.

Commit:

```
git add tkt/_session_tools.py tests/test_session_tools.py tests/test_mcp_server.py
git commit -m "Collapse the Plannotator host tool to a single open action

The action enum, session-id parameter and the result/status/close actions are
gone: the result is a file the agent reads, and teardown is tab-close or
workspace removal.
```

---

## Task 4 — Verbatim Inbox re-expose (`tkt/_inbox_proxy.py`)

**Files:** new `tkt/_inbox_proxy.py`; `tkt/_session_tools.py` (delete
`plannotator_inbox`/`INBOX_ACTION_TOOLS`/`_inbox_call`, add the wiring and the
patch-source resolver); new `tests/test_inbox_proxy.py`, `tests/fake_inbox_mcp.py`;
delete the inbox action-map tests from `tests/test_session_tools.py`.

### 4.1 Why this shape

Upstream `plannotator inbox mcp` publishes seven tools
(`send_message`, `read_thread`, `search`, `resolve_message`, `wait_for_reply`,
`get_guide_brief`, `submit_guide`), each with its own title, description and
input schema. Republishing them from the live `tools/list` means tkt carries no
mapping to maintain (R3). The proxy synthesizes a keyword-only `Any` parameter
per schema property so the SDK's argument model accepts whatever upstream
declares, then overwrites the advertised `parameters` with upstream's schema —
verified against the installed mcp SDK 2.x, which also passes a returned
`CallToolResult` through `convert_result` untouched. Upstream declares no output
schema, so neither do we; its structured content is forwarded in the result
object exactly as upstream sends it.

### 4.2 `tkt/_inbox_proxy.py`

Copy the BSD-3 header from `tkt/_session_tools.py`, then:

```python
"""Re-expose ``plannotator inbox mcp``'s tools as session tools, verbatim."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, ToolAnnotations

__all__ = ("register_inbox_tools",)

_UPSTREAM_COMMAND = "plannotator"
_UPSTREAM_ARGS = ("inbox", "mcp")
_LIST_TIMEOUT_S = 20.0
_CALL_TIMEOUT_S = 75.0  # > the upstream wait_for_reply ~50 s cursor

#: Properties ``submit_guide`` gains, because a sandbox-private path (a
#: restricted run's ``/tmp``) is invisible to the host process that would read
#: it.  Every other published property is upstream's own.
_TKT_EXTENSIONS: dict[str, dict[str, dict[str, Any]]] = {
    "submit_guide": {
        "patch_file": {
            "type": "string",
            "description": (
                "tkt extension: path to a unified-diff file inside the workspace, read on the "
                "host and forwarded as ``patch``."
            ),
            "x-tkt-extension": True,
        },
        "patch_spec": {
            "type": "object",
            "description": (
                "tkt extension: host-side git diff forwarded as ``patch`` — dir (repo inside the "
                "workspace, default its root), base (required ref), head (default HEAD), paths "
                "(optional pathspecs)."
            ),
            "x-tkt-extension": True,
        },
    }
}

#: ``(property name, value) -> unified diff text``; raises ValueError with an
#: agent-facing message when the source cannot be resolved.
PatchResolver = Callable[[str, Any], str]


def register_inbox_tools(
    mcp: MCPServer,
    *,
    workspace: str,
    patch_resolver: PatchResolver | None = None,
) -> int:
    """Publish every upstream Inbox tool under upstream's own name and schema.

    Called once per session-server connection, from the upstream ``tools/list``,
    so the surface follows upstream releases. Each published tool forwards its
    arguments to the upstream tool of the same name and returns upstream's
    content, structured content and error flag unchanged; each call is one
    short-lived ``plannotator inbox mcp`` connection, scoped to ``workspace``
    because upstream derives the Inbox project from the invoking directory.

    An unreachable upstream costs only the Inbox tools: the rest of the session
    server starts. Returns the number of tools published.
    """
    try:
        upstream = _list_upstream_tools(workspace)
    except Exception:
        logging.warning("plannotator inbox listing failed; publishing no inbox tools", exc_info=True)
        return 0
    published = 0
    for tool in upstream:
        if not isinstance(tool.get("name"), str):
            continue
        try:
            if _publish(mcp, tool, workspace, patch_resolver):
                published += 1
        except Exception:
            logging.warning(f"could not publish inbox tool {tool['name']!r}", exc_info=True)
            continue
    return published


def _list_upstream_tools(cwd: str) -> list[dict[str, Any]]:
    """One-shot ``tools/list`` against ``plannotator inbox mcp``."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def _run() -> list[dict[str, Any]]:
        params = StdioServerParameters(command=_UPSTREAM_COMMAND, args=list(_UPSTREAM_ARGS), cwd=cwd)
        async with asyncio.timeout(_LIST_TIMEOUT_S), stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                res = await session.list_tools()
                return [t.model_dump(by_alias=True, mode="json", exclude_none=True) for t in res.tools]

    return asyncio.run(_run())


def _publish(mcp: MCPServer, tool: dict[str, Any], cwd: str, patch_resolver: PatchResolver | None) -> bool:
    """Register one upstream tool on ``mcp``; False when the name was taken."""
    name = str(tool["name"])
    schema = dict(tool.get("inputSchema") or {})
    extra = _TKT_EXTENSIONS.get(name, {}) if patch_resolver is not None else {}
    if extra:
        schema["properties"] = {**(schema.get("properties") or {}), **extra}
    names = [str(key) for key in (schema.get("properties") or {})]

    def forward(**kwargs: Any) -> Any:
        return _forward(name, kwargs, cwd, patch_resolver)

    # The SDK builds the advertised schema and its argument model from the
    # signature, so the parameters are synthesized from upstream's properties
    # (Any accepts whatever upstream actually declares) and the advertised
    # schema is then replaced wholesale.
    forward.__name__ = name
    forward.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [inspect.Parameter(n, inspect.Parameter.KEYWORD_ONLY, annotation=Any, default=None) for n in names]
    )
    forward.__annotations__ = dict.fromkeys(names, Any)
    annotations = tool.get("annotations")
    mcp.add_tool(
        forward,
        name=name,
        title=tool.get("title"),
        description=tool.get("description") or f"Plannotator Inbox: {name}.",
        annotations=ToolAnnotations(**annotations) if isinstance(annotations, dict) else None,
        meta=tool.get("_meta"),
        structured_output=False,
    )
    registered = mcp._tool_manager.get_tool(name)
    # The SDK keeps the pre-existing tool when a name is already registered,
    # so only this call's own registration may have its schema replaced; a
    # colliding listing entry is dropped rather than rewriting tkt's surface.
    if registered is None or registered.fn is not forward:
        logging.warning(f"inbox tool name {name!r} is already registered; not publishing it")
        return False
    registered.parameters = schema
    return True


def _forward(name: str, kwargs: dict[str, Any], cwd: str, patch_resolver: PatchResolver | None) -> Any:
    arguments = {k: v for k, v in kwargs.items() if v is not None}
    if patch_resolver is not None:
        for source in sorted(set(arguments) & {"patch_file", "patch_spec"}):
            if "patch" in arguments:
                return f"plannotator inbox {name}: pass at most one of patch and {source}."
            try:
                patch_text = patch_resolver(source, arguments[source])
            except ValueError as exc:
                return f"plannotator inbox {name}: {exc}"
            arguments.pop(source)
            arguments["patch"] = patch_text
    return asyncio.run(_call(name, arguments, cwd))


async def _call(name: str, arguments: dict[str, Any], cwd: str) -> Any:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=_UPSTREAM_COMMAND, args=list(_UPSTREAM_ARGS), cwd=cwd)
    try:
        async with asyncio.timeout(_CALL_TIMEOUT_S), stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool(name, arguments)
    except FileNotFoundError:
        return "plannotator inbox failed: plannotator binary not found on host."
    except Exception as exc:  # surface any upstream breakage as tool text
        return f"plannotator inbox failed: {exc}."
    return CallToolResult(
        content=res.content,
        structured_content=res.structured_content,
        is_error=res.is_error,
        meta=res.meta,
    )
```

### 4.3 Wiring in `tkt/_session_tools.py`

Delete `INBOX_ACTION_TOOLS`, `_inbox_call` and the `plannotator_inbox` tool. Add
at module level (it is the logic the deleted action map already had, now raising
instead of formatting):

```python
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
```

and at the end of `build_session_tools`, after the tool definitions:

```python
    register_inbox_tools(mcp, workspace=workspace, patch_resolver=partial(_resolve_patch_source, workspace))
```

with `from functools import partial` and
`from ._inbox_proxy import register_inbox_tools` in the imports.

Two consequences worth stating: the listing spawn happens once per session-server
connection (and per Zed MCP connection, via `tkt/mcp_server.py`'s shared builder),
and with no `plannotator` on the host PATH it fails into the logged
degrade-to-zero path — which is why the existing `build_session_tools` unit tests
stay green without a stub. Tests that only exercise the brainstorm/open surface may
`monkeypatch.setattr("tkt._session_tools.register_inbox_tools", lambda *a, **k: 0)`
to keep their run time at zero.

### 4.4 Tests

`tests/fake_inbox_mcp.py` — a real upstream Inbox server built with the installed
mcp SDK, so "verbatim" is checked against actual `tools/list` output rather than a
hand-written expectation. Copy the license header, then:

```python
"""Stand-in for `plannotator inbox mcp`, used by the proxy's tests."""

import os

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

mcp = MCPServer("fake-inbox")


@mcp.tool(title="Send a message to the person", structured_output=False)
def send_message(body: str, subject: str | None = None) -> str:
    """Say something in the Inbox.

    Args:
        body: the markdown message
        subject: one-line thread subject
    """
    return f"sent: {body}"


@mcp.tool(title="Get the method for a guided review")
def get_guide_brief() -> dict[str, object]:
    """Return the brief the author needs, in structured content."""
    return {"method": "one section per file", "example": {"title": "Example"}}


@mcp.tool(title="Send the person a guided review")
def submit_guide(guide: object, patch: str | None = None, snapshot: object = None) -> dict[str, object]:
    """Deliver an authored guide.

    Args:
        guide: the authored guide
        patch: the unified diff it describes
        snapshot: a complete snapshot instead
    """
    return {"guide": guide, "patch": patch, "cwd": os.getcwd()}


@mcp.tool(title="Resolve a thread", structured_output=False)
def resolve_message(message_id: str) -> str:
    """Archive a thread.

    Args:
        message_id: a message in the thread
    """
    raise ToolError(f"no such message: {message_id}")


if __name__ == "__main__":
    mcp.run()
```

`mcp.tool()`'s `structured_output` auto-detection makes the `dict` returns publish
structured content; an unannotated `str` return gets wrapped into a
`{"result": ...}` structured payload, so the two text-returning tools pass
`structured_output=False` to publish content only — which is what the
content-preservation tests assert. Add the fixture (same shape as the
`fake_inbox_cli` fixture it replaces in `tests/test_session_tools.py`) and these
tests in `tests/test_inbox_proxy.py`:

```python
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


def _published(mcp):
    return {t.name: t for t in asyncio.run(mcp.list_tools())}


def _invoke(mcp, name, **arguments):
    """Call a published tool the way the server would."""
    return asyncio.run(mcp._tool_manager.get_tool(name).run(arguments, None))


def test_surface_is_verbatim(inbox_upstream) -> None:
    """Name, title, description and input schema come through unchanged."""
    from tkt._inbox_proxy import register_inbox_tools

    expected = asyncio.run(_direct_list(inbox_upstream))  # tools/list straight from the stub
    mcp = MCPServer("t")
    assert register_inbox_tools(mcp, workspace=str(inbox_upstream)) == len(expected)
    published = _published(mcp)
    for tool in expected:
        got = published[tool["name"]]
        assert got.title == tool["title"]
        assert got.description.strip() == tool["description"].strip()
        assert got.input_schema == tool["inputSchema"]


def test_content_and_structured_content_preserved(inbox_upstream) -> None:
    from tkt._inbox_proxy import register_inbox_tools

    mcp = MCPServer("t")
    register_inbox_tools(mcp, workspace=str(inbox_upstream))
    plain = _invoke(mcp, "send_message", body="hi")
    assert plain.structured_content is None
    assert plain.content[0].text == "sent: hi"
    brief = _invoke(mcp, "get_guide_brief")
    assert brief.structured_content == {"method": "one section per file", "example": {"title": "Example"}}


def test_upstream_error_flag_preserved(inbox_upstream) -> None:
    from tkt._inbox_proxy import register_inbox_tools

    mcp = MCPServer("t")
    register_inbox_tools(mcp, workspace=str(inbox_upstream))
    res = _invoke(mcp, "resolve_message", message_id="nope")
    assert res.is_error is True


def test_forward_strips_unset_kwargs(monkeypatch) -> None:
    """Only the arguments the agent actually passed reach upstream."""
    from tkt import _inbox_proxy

    seen: dict[str, Any] = {}

    async def fake_call(name, arguments, cwd):
        seen.update(name=name, arguments=arguments, cwd=cwd)
        return "ok"

    monkeypatch.setattr(_inbox_proxy, "_call", fake_call)
    _inbox_proxy._forward("send_message", {"body": "hi", "subject": None}, "/ws", None)
    assert seen == {"name": "send_message", "arguments": {"body": "hi"}, "cwd": "/ws"}


def test_scoped_to_the_workspace_cwd(inbox_upstream) -> None:
    """Upstream derives the Inbox project from the invoking directory."""
    from tkt._inbox_proxy import register_inbox_tools

    mcp = MCPServer("t")
    register_inbox_tools(mcp, workspace=str(inbox_upstream))
    res = _invoke(mcp, "submit_guide", guide={"title": "t"}, patch="diff --git a/x b/x\n")
    assert res.structured_content["cwd"] == str(inbox_upstream)


def test_tkt_patch_sources_forwarded_as_patch(inbox_upstream, tmp_path, monkeypatch) -> None:
    """``patch_file`` is read host-side and arrives upstream as ``patch``."""
    from tkt._inbox_proxy import register_inbox_tools
    from tkt._session_tools import _resolve_patch_source
    from functools import partial

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
    from tkt._inbox_proxy import register_inbox_tools

    mcp = MCPServer("t")
    register_inbox_tools(mcp, workspace=str(inbox_upstream))
    assert "patch_file" not in _published(mcp)["submit_guide"].input_schema["properties"]


def test_patch_and_patch_file_conflict(inbox_upstream, tmp_path, monkeypatch) -> None:
    from functools import partial

    from tkt._inbox_proxy import register_inbox_tools
    from tkt._session_tools import _resolve_patch_source

    (tmp_path / "change.diff").write_text("diff\n")
    mcp = MCPServer("t")
    register_inbox_tools(
        mcp, workspace=str(tmp_path), patch_resolver=partial(_resolve_patch_source, str(tmp_path))
    )
    out = _invoke(mcp, "submit_guide", guide={"title": "t"}, patch="x", patch_file="change.diff")
    assert "at most one of patch and patch_file" in str(out)


def test_unreachable_upstream_publishes_nothing(tmp_path, monkeypatch) -> None:
    """A broken upstream costs only the Inbox tools."""
    from tkt._inbox_proxy import register_inbox_tools

    monkeypatch.setattr("tkt._inbox_proxy._LIST_TIMEOUT_S", 5.0)  # keep the failure fast
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "plannotator"
    shim.write_text("#!/usr/bin/env bash\nexit 1\n")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    mcp = MCPServer("t")
    assert register_inbox_tools(mcp, workspace=str(tmp_path)) == 0
    assert _published(mcp) == {}
```

`_direct_list` is a three-line helper in the same file: an `mcp.client.stdio`
`list_tools()` against `python tests/fake_inbox_mcp.py` with
`StdioServerParameters(..., cwd=str(tmp_path))`, returning
`[t.model_dump(by_alias=True, mode="json", exclude_none=True) for t in res.tools]`
— the same call `_list_upstream_tools` makes, which is exactly why the comparison
is meaningful.

The inline `from tkt…` imports above mark what each test needs; hoist them to the
top of the file the way `tests/test_session_tools.py` does, since ruff's isort
rule runs on the test tree.

Delete from `tests/test_session_tools.py`: `INBOX_ACTION_TOOLS`,
`fake_inbox_cli`, `_inbox_tool`/`_call` inbox helpers and every `test_inbox_*`
test (their coverage lives in `tests/test_inbox_proxy.py`). Keep
`test_no_host_danger_tools` with its dangerous-name denylist — the Inbox names now
come from upstream, so the guard asserts "no host execution tools", not an exact
tool list.

Commit:

```
git add tkt/_inbox_proxy.py tkt/_session_tools.py tests/test_inbox_proxy.py tests/fake_inbox_mcp.py tests/test_session_tools.py
git commit -m "Re-expose the Plannotator Inbox upstream tools verbatim

The session server publishes whatever `plannotator inbox mcp` lists, under
upstream's own names, descriptions and schemas, forwarding calls and results
unchanged; the hand-maintained action map is gone. Only `submit_guide` gains
properties — the tkt `patch_file`/`patch_spec` sources, which resolve host-side
because sandbox-private paths are invisible here.
```

---

## Task 5 — Read-only result mount and `TKT_PLANNOTATOR_RESULTS`

**File:** `tkt/sandbox.py`
**Tests:** `tests/test_sandbox.py`

### 5.1 The change

Import at line 47 becomes:

```python
from ._sessionmanagers import plannotator_result_dir, sweep_session_registry
```

In `_build_common_argv`, immediately after the `for path in self._mounts_rw:` loop
(currently lines 966-969), add:

```python
        # The session tools' result directory, visible at its host path: the
        # decision JSON a host-side Plannotator session leaves behind is
        # something the agent reads with its own file tools.  Mounted after the
        # configured mounts so a configured read-write bind of the same path
        # still wins.  plannotator_result_dir() creates the directory, which
        # --ro-bind-try otherwise would skip silently.
        if not network and bridge_llm:
            result_dir = plannotator_result_dir()
            if result_dir not in rw_paths:
                argv += ["--ro-bind-try", result_dir, result_dir]
```

and inside the existing session-env block (currently lines 990-996), directly
after the `TKT_SESSION_SOCKET` line:

```python
            argv += ["--setenv", "TKT_PLANNOTATOR_RESULTS", plannotator_result_dir()]
```

The condition is deliberately the same as the session socket's: the mount exists
where the session tools exist. A `--network` run has no session tools, and gains
no view of other workspaces' review captures.

### 5.2 Tests

```python
def _ro_pairs(argv: list[str]) -> set[tuple[str, str]]:
    return {(argv[i + 1], argv[i + 2]) for i in range(len(argv) - 2) if argv[i] == "--ro-bind-try"}


def _env_of(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


def test_plannotator_results_mounted_read_only(tmp_path, monkeypatch):
    """Restricted runs expose the result dir at its host path, read-only."""
    home = tmp_path / "home"
    (home / ".local" / "state" / "tkt").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    result_dir = str(home / ".local" / "state" / "tkt" / "plannotator")
    net_dir = tmp_path / "net"
    net_dir.mkdir()  # a restricted run validates that the net dir exists
    argv = Sandbox(command=[])._build_common_argv(
        home=str(home), mounts=[], inner="true", network=False, net_dir=str(net_dir)
    )
    assert (result_dir, result_dir) in _ro_pairs(argv)
    assert _env_of(argv, "TKT_PLANNOTATOR_RESULTS") == result_dir
    assert Path(result_dir).is_dir()  # launch creates it, so the mount is never skipped


def test_plannotator_results_absent_for_network_runs(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    result_dir = str(home / ".local" / "state" / "tkt" / "plannotator")
    argv = Sandbox(command=[])._build_common_argv(home=str(home), mounts=[], inner="true", network=True)
    assert (result_dir, result_dir) not in _ro_pairs(argv)
    assert _env_of(argv, "TKT_PLANNOTATOR_RESULTS") is None


def test_plannotator_results_skipped_when_already_read_write(tmp_path, monkeypatch):
    """A configured read-write bind of the same path is not overridden."""
    home = tmp_path / "home"
    (home / ".local" / "state" / "tkt").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    result_dir = str(home / ".local" / "state" / "tkt" / "plannotator")
    argv = Sandbox(command=[], mounts_ro=[result_dir])._build_common_argv(
        home=str(home), mounts=["--bind", result_dir, result_dir], inner="true", network=False,
        net_dir=str(tmp_path / "net"),
    )
    assert (result_dir, result_dir) not in _ro_pairs(argv)
```

Confirm `tests/test_sandbox.py`'s existing sweep test still passes unchanged
(`sweep_session_registry` keeps its name and call site at line ~652). Commit:

```
git add tkt/sandbox.py tests/test_sandbox.py
git commit -m "Mount the Plannotator result directory read-only into restricted runs

The agent reads a session's decision JSON with its own file tools, at the same
path the host sees, named by TKT_PLANNOTATOR_RESULTS.
```

---

## Task 6 — Guidance in one place; install-time skill patches gone

**Files:** `tkt/AGENTS.md.in`, `tkt/install.py`, `AGENTS.md`,
`tests/test_install.py`

### 6.1 `tkt/AGENTS.md.in`

Replace lines 48-74 (the host-session paragraph, from "If you offer a
browser-based visual companion" through "…retry through the path the catalog
gives.") with:

```
If you offer a browser-based visual companion (see the brainstorming skill) or
Plannotator reviews, use the `brainstorm_server` / `plannotator_open` MCP tools:
they run the session servers on the *host*, where the human's browser reaches
them directly. If your tool catalog lists entries as `tools["<server>"].<tool>`
(a code-mode harness), that path is the call form: invoke the tool from code mode
(the `execute` tool), never as a top-level tool name — there, a top-level call to
the bare name (e.g. `plannotator_open`) can never work. From code mode, e.g.
`tools["tkt-session"].plannotator_open({ kind: "review", target: "<dir>",
base: "<ref>" })` opens a review. Hand the human any URL the tool returns
**verbatim** and end your turn: feedback is not pushed to you, and nothing
arrives to wake you.

The tool returns the path the session's decision lands at — `<handle>.out`,
beside `<handle>.registry.json`, under the directory named by
`TKT_PLANNOTATOR_RESULTS` (mounted read-only at that same path). When the human
says the review is in, read that file. A missing file means the session is still
open; an empty file means it closed without feedback; JSON that does not parse
yet means the session has not resolved, so read it again later rather than
reporting a parse error. Never poll for it, and never launch `start-server.sh`
or the `plannotator` CLI yourself: a session started inside the sandbox is
unreachable by design, and
the host tools own session lifecycles — opening the same target again attaches to
the live session, and `ls`/`read` over that directory is how you rediscover a
session after a restart. A session the human started from their own terminal has
no capture for you to read: ask them what they submitted. Closing a review tab
discards an unsent draft, so a session the human walked away from has to be
opened again.

For the Plannotator Inbox — leaving the human a question they can answer later,
or authoring a guided review — the upstream tools are published directly:
`send_message`, `read_thread`, `search`, `resolve_message`, `wait_for_reply`,
`get_guide_brief`, `submit_guide`. Call them through the same code-mode path.
`submit_guide` also accepts tkt's `patch_file` (a diff staged inside the
workspace) and `patch_spec` (a host `git diff base...head`), each forwarded as
`patch`, because paths private to your sandbox are invisible to the host.

If a session tool is absent from your tool catalog in every spelling, stop and
report it instead of improvising. One exception: shortly after the session sat
idle (e.g. while the human worked through a review), a turn's tool catalog may
transiently list the session tools as "no longer available" while the harness
reconnects its MCP connections. If such a revocation cites session tools you
used in an earlier turn, wait one turn and use the tools if they reappear; report
only if the tools stay missing across turns. A failed call to a bare tool name is
not such a revocation: if the catalog still lists the tool (in any spelling),
retry through the path the catalog gives.
```

### 6.2 `tkt/install.py`

Delete `PLANNOTATOR_SKILL_PATCHES` (lines 42-98), `_apply_skill_patches`
(lines 209-220) and `_install_patched_skill` (lines 222-275), and in
`_install_skill_dirs` replace the patch-or-copy branch (lines 305-308) with a
plain `_copy_skill(src, dst, dry_run=dry_run)`. Drop the now-unused `confirm` and
`unattended` parameters from `_install_skill_dirs` and its call site (line ~467);
keep `confirm` on `install_harness` itself — it still guards removals elsewhere —
and drop `unattended` there too, since the only behavior it gated was adopting
patched upstream text; the CLI keeps its `--yes` flag, which is what `confirm`
implements. Delete `import re` if nothing else
in the file uses it, and remove the docstring sentences about adopting patched
upstream text (`install_harness`'s mention of "updated copy back into the repo",
line ~437). `PLANNOTATOR_HOST_ONLY_SKILLS` and its deny behavior stay exactly as
they are.

### 6.3 Repo `AGENTS.md` and the patch-source doc

The `tkt/sandbox.py` bullet (line ~80) says the session `plannotator` tool accepts
plan files as `open` targets: keep the fact, name the tool correctly
(`plannotator_open`). Add to the `harnesses/shared/skills` bullet that the
vendored Plannotator skills install verbatim upstream — tkt's own guidance for
them lives in `tkt/AGENTS.md.in`.

`docs/plannotator-inbox-patch-source.md` documents a feature that still ships —
`patch_file` / `patch_spec` resolved on the host and forwarded as `patch` — so
update its naming to the published surface (`submit_guide`, the `x-tkt-extension`
marker, `_resolve_patch_source` in `tkt/_session_tools.py` injected into
`tkt/_inbox_proxy.py`) rather than deleting it. Its argument about why a large
diff cannot travel as inline tool-argument text stands.

### 6.4 Tests

In `tests/test_install.py`: delete `test_install_applies_tkt_patches`,
`test_install_patched_skill_second_run_is_quiet`,
`test_install_fresh_upstream_install_not_drift`,
`test_install_adopt_writes_upstream_back_to_repo`,
`test_install_declined_drift_overwrites_installed`,
`test_install_unattended_never_adopts`, `test_install_dry_run_adopt_writes_nothing`,
`test_install_warns_on_patch_anchor_miss` (lines 500-~660) — they test the deleted
mechanism. Add the contract that replaces them:

```python
def test_plannotator_skills_install_verbatim(tmp_path):
    """Vendored Plannotator skills are installed byte-for-byte upstream."""
    repo = _make_repo_root(tmp_path)  # the existing helper these deleted tests used
    home = tmp_path / "home"
    install_harness(repo_root=repo, home=str(home))
    for name in ("plannotator", "plannotator-annotate", "plannotator-review"):
        vendored = Path(repo) / "harnesses" / "shared" / "skills" / name / "SKILL.md"
        installed = home / ".agents" / "skills" / name / "SKILL.md"
        assert installed.read_text() == vendored.read_text()
```

Use whichever existing helper in that file builds a repo root with the real
`harnesses/` tree (the deleted `test_install_applies_tkt_patches` has the
pattern); if none does, build it the same way that test did.

Add to the guidance-rendering tests (`tests/test_sandbox.py`, which already
asserts on rendered `AGENTS.md` content for shared vs worktree mode):

```python
def test_agents_md_describes_the_result_contract() -> None:
    """The one place the pull-by-file contract is documented."""
    rendered = render_agents_md(Path("tkt/AGENTS.md.in").read_text(), shared=True)
    assert "TKT_PLANNOTATOR_RESULTS" in rendered
    assert "plannotator_open" in rendered
    assert "does not parse" in rendered
    for gone in ('action: "result"', "action='status'", "draft is restored"):
        assert gone not in rendered
```

(follow the existing call style of the neighbouring rendering tests rather than
that signature). Commit:

```
git add tkt/AGENTS.md.in tkt/install.py AGENTS.md tests/test_install.py tests/test_sandbox.py
git commit -m "Keep Plannotator guidance in AGENTS.md.in and install skills verbatim

The vendored upstream skills are no longer rewritten at install time: the
code-mode call form, the read-the-file result contract and the inbox tool list
are documented once, where tkt owns the text.
```

---

## Task 7 — Full gates and real-Plannotator acceptance

**Run the human-side steps in the human's shell** (the agent sandbox cannot see
`~/.local/bin/plannotator` or `~/.plannotator`).

1. Code gates (agent): `ruff check . && ruff format --check . && mypy tkt/ &&
   python -m pytest`. Everything added above passes; the deleted tests are gone,
   so the count moves by roughly the number of `test_inbox_*` and
   `test_install_*` cases removed. Landed clean at **398 passed** (baseline 392:
   the action-map and skill-patching cases go out, the capture, proxy, mount and
   patch-source guard cases come in).
2. Human, on the host: `git log --oneline -8` and skim each commit's diff — the
   whole rework should read as deletions plus one small manager.
3. Human, in a tkt sandbox on this workspace, restricted run, one session per
   case. Ask the agent to open each, review in the browser, then tell it "the
   review is in" and let it read the file:
   - **gated annotate, request changes** with two comments → `decision:
     "annotated"`, `feedback` carries both comments, `annotationCount` 2;
   - **gated annotate, approve** → `decision: "approved"`, notes kept in
     `feedback` if you type some;
   - **plain annotate, close the tab without submitting** → capture exists and
     is empty;
   - **review with `base`** → `decision` and `message`;
   - **restart case**: open a review, quit the sandbox mid-session, start a new
     sandbox, open the same target → the tool reports `reused: true` with the
     *same* `result_path`, and submitting there still lands in that file;
   - **inbox**: `send_message` a question, answer it in the browser, `wait_for_reply`;
     then `get_guide_brief` + `submit_guide` with `patch_spec` → the guide card
     shows the diff (`patch_file` variant: stage the diff inside the workspace).
4. Human, on the host after the cases: `plannotator sessions --json` shows no
   leftover servers for the workspace; `tkt rm` (or a later restricted launch)
   reaps one you deliberately abandon — check `sessions --json` before and after.
5. If case 3's commented submit differs from
   `tests/fixtures/plannotator/annotate-annotated.json`, replace that file with
   the new capture verbatim and amend the Task 2 commit (`git commit --fixup` +
   autosquash, or amend at review time).
6. Then, per the repo's Git Workflow: offer to squash the branch's rework commits
   into one and rebase onto `main`.

---

## Self-review notes (done against the spec)

- **Coverage**: R1 (Task 2 capture + Task 5 mount, no Plannotator-storage reads
  anywhere in the diff), R2 (only `plannotator_open` and the Inbox proxy reach the
  host; result retrieval is file reads), R3 (Task 4 lists upstream at startup), R4
  (attach-or-start in `_attach`, handle-keyed captures, no upstream dedup assumed),
  R5 (bridge, `session_server.py`, `BrainstormManager` untouched), R6 (Zed's
  `build_session_tools` gets the same surface for free — Task 3's test in
  `tests/test_mcp_server.py` asserts it).
- **§7 cleanup inventory**: every row is a deletion in Tasks 1-4 or 6.
- **§9 pinned facts** are the fixtures and the fake CLI's behavior, not prose.
- **Type consistency**: `open()` returns
  `{type, handle, url, result_path, err_path, reused, source}` everywhere; the
  registry entry is that plus `{workspace, target, kind, gate, base, opened_at,
  out_path, pgid, server_pid}`; the tool text and the guidance use the same keys.

## Decisions made while planning (approved)

1. **`open()` also returns `err_path`.** The "listing unavailable, URL unknown"
   case needs a named place to look, and `<handle>.err` is it.
2. **A human-started session gets a handle and a registry entry** — a minted
   handle (its registry file exists and is readable from the mount) with
   `result_path=None` and `source="discovered"`.
   That entry is what lets `tkt rm` reap an abandoned human-started server.
3. **Inbox results forward upstream's `CallToolResult` untouched** — content
   blocks, structured content, error flag, `_meta`. Upstream declares no output
   schema, so neither do we; structured content arrives exactly as upstream sends
   it today rather than being flattened into a JSON text block as the old
   `plannotator_inbox` did.
4. **One upstream connection per Inbox call** (as today), opened with
   `cwd=workspace` for project scoping — no long-lived upstream session shared
   across agent tool calls.
5. **The result mount follows the session-socket condition** (restricted +
   bridged), so full-network runs neither see nor get `TKT_PLANNOTATOR_RESULTS`.
6. **Retention pruning runs at restricted launches** (30 days) and the old
   `~/.local/state/tkt/sessions/` registry is deleted outright — no migration,
   since entries there are only used to reap already-dead processes.
7. **The guard test asserts a dangerous-name denylist** rather than an exact
   tool list, because inbox names now come from upstream.
