# tkt MCP Server Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the `tkt mcp-server` sandboxed `bash` tool: enforce `timeout_ms` in the warm-holder driver, fix the child-shell environment (export functions, drop `-l`, detach stdin), and document `description`'s real role.

**Architecture:** A long-lived `bwrap` "warm holder" driver reads framed requests on stdin and writes framed results on stdout; each `bash` tool call runs the command in a fresh `bash -c` child of the holder. This plan changes the framing (3 lines in / 5 fields out), wraps the child in coreutils `timeout --signal=KILL` to enforce the deadline, exports the holder's shell functions so children can call `setup`/`conda`, and detaches child stdin so it can't consume the framing pipe.

**Tech Stack:** Python 3.13, `tkt/mcp_server.py` (FastMCP server + `WarmSandbox` + framing helpers), pytest, coreutils `timeout` (present via the sandbox's `--ro-bind / /`).

**Spec:** `docs/superpowers/specs/2026-08-31-tkt-mcp-server-hardening-design.md`

## Global Constraints

- Python 3.13. Do **not** add packaging config. Do **not** add new third-party deps (coreutils `timeout` is on the host, already visible in the sandbox).
- License: BSD-3-Clause. Do **not** change or remove license headers in `.py` files.
- Numpy-style docstrings, doc-length 79, line-length 110 (ruff). Run before committing: `ruff check .`, `ruff format --check .`, `mypy tkt/` (only `tkt/` and `tests/` results matter — the pinned `superpowers/` submodule has unrelated pre-existing ruff/format noise, ignore it).
- Tests use pytest in `tests/`, mirroring `tests/test_mcp_server.py` conventions (inspect built strings/argv, patch `subprocess.Popen`; never run `bwrap`).
- Commit only the files each task changes. Do **not** commit `AGENTS.md`, the spec, or this plan.
- The wire framing change is internal to the server↔holder link (both sides ship in the same commit); no external compat concern.

---

### Task 1: Driver and framing protocol change

**Files:**
- Modify: `tkt/mcp_server.py` — `parse_result_line` (5 fields) and `build_driver_script` (3-line read, function export, `bash -c`, `</dev/null`, timeout wrapper)
- Modify: `tests/test_mcp_server.py` — update `test_parse_result_line`, `test_build_driver_script_runs_setup_once`, add `test_build_driver_script_hardening`
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: existing `encode_field`, `decode_field`, `build_driver_script(setup_lines: list[str]) -> str`.
- Produces:
  - `parse_result_line(line: str) -> dict[str, Any]` returning keys `stdout`, `stderr`, `exit_code` (int), `cwd`, `timed_out` (bool) — 5 fields.
  - `build_driver_script` emits a driver that reads 3 base64 lines per request (`cwd`, `command`, `timeout_ms`), runs `timeout --kill-after=5 "${secs}s" bash -c -- "$cmd" </dev/null` when `tmo > 0` (else plain `bash -c -- "$cmd" </dev/null`), maps `rc==124 || rc==137` → `to=1`, and emits 5 space-separated base64 fields (`stdout`, `stderr`, `exit_code`, `cwd`, `timed_out`).

- [ ] **Step 1: Update `parse_result_line` to parse 5 fields**

In `tkt/mcp_server.py`, replace the current `parse_result_line` (4 fields) with:

```python
def parse_result_line(line: str) -> dict[str, Any]:
    """Parse one driver result line into a dict.

    The driver emits 5 space-separated base64 fields: stdout, stderr,
    exit_code, cwd, timed_out. base64 has no spaces, so splitting on a single
    space is unambiguous.
    """
    parts = line.split(" ")
    if len(parts) != 5:
        raise ValueError(f"Malformed result line: {line!r}")
    stdout, stderr, exit_code, cwd, timed_out = (decode_field(p) for p in parts)
    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": int(exit_code),
        "cwd": cwd,
        "timed_out": bool(int(timed_out)),
    }
```

- [ ] **Step 2: Update `build_driver_script`**

In `tkt/mcp_server.py`, replace the current `build_driver_script` (4-field, `bash -lc`, no timeout) with:

```python
def build_driver_script(setup_lines: list[str]) -> str:
    """Build the warm-holder driver bash script.

    ``setup_lines`` run once at startup (conda activation + EUPS setup), with
    their stdout redirected to stderr so startup diagnostics never leak into
    the framed stdout channel. The EUPS/conda shell functions are exported so
    fresh children can call ``setup``/``conda``. Then a loop reads three base64
    lines per request (cwd, command, timeout_ms), runs the command in a fresh
    ``bash -c`` child (under a coreutils ``timeout`` when a positive timeout is
    given), and emits one result line of 5 space-separated base64 fields
    (stdout, stderr, exit_code, cwd, timed_out).
    """
    setup = "\n".join(setup_lines)
    return (
        "{\n"
        f"{setup}\n"
        "    while IFS= read -r _f; do export -f \"$_f\"; done < <(compgen -A function)\n"
        "} >&2\n"
        "while IFS= read -r cwd_b64 && IFS= read -r cmd_b64 && IFS= read -r tmo_b64; do\n"
        "    cwd=$(printf '%s' \"$cwd_b64\" | base64 -d)\n"
        "    cmd=$(printf '%s' \"$cmd_b64\" | base64 -d)\n"
        "    tmo=$(printf '%s' \"$tmo_b64\" | base64 -d)\n"
        '    cd "$cwd" 2>/dev/null || true\n'
        "    out=$(mktemp)\n"
        "    errf=$(mktemp)\n"
        "    if [ \"$tmo\" -gt 0 ]; then\n"
        "        secs=$(( (tmo + 999) / 1000 ))\n"
        '        timeout --kill-after=5 "${secs}s" bash -c -- "$cmd" </dev/null >"$out" 2>"$errf"\n'
        "    else\n"
        '        bash -c -- "$cmd" </dev/null >"$out" 2>"$errf"\n'
        "    fi\n"
        "    rc=$?\n"
        "    cur=$(pwd)\n"
        "    if [ \"$rc\" -eq 124 ] || [ \"$rc\" -eq 137 ]; then to=1; else to=0; fi\n"
        "    printf '%s %s %s %s %s\\n' \\\n"
        '        "$(base64 -w0 < "$out")" \\\n'
        '        "$(base64 -w0 < "$errf")" \\\n'
        '        "$(printf \'%s\' "$rc" | base64 -w0)" \\\n'
        '        "$(printf \'%s\' "$cur" | base64 -w0)" \\\n'
        '        "$(printf \'%s\' "$to" | base64 -w0)"\n'
        '    rm -f "$out" "$errf"\n'
        "done\n"
    )
```

- [ ] **Step 3: Update `test_parse_result_line` for 5 fields**

In `tests/test_mcp_server.py`, replace `test_parse_result_line` with:

```python
def test_parse_result_line():
    """parse_result_line splits 5 space-separated base64 fields."""
    line = " ".join(encode_field(field) for field in ("out", "err", "3", "/some/cwd", "1"))
    parsed = parse_result_line(line)
    assert parsed == {
        "stdout": "out",
        "stderr": "err",
        "exit_code": 3,
        "cwd": "/some/cwd",
        "timed_out": True,
    }
```

- [ ] **Step 4: Update `test_build_driver_script_runs_setup_once`**

In `tests/test_mcp_server.py`, change the assertion `script.count("bash -lc --") == 1` to `script.count("bash -c --") == 1`:

```python
def test_build_driver_script_runs_setup_once():
    """The driver runs setup once, then one fresh bash child per loop iter."""
    script = build_driver_script(["conda activate env", "setup -r .agent"])
    assert "conda activate env" in script
    assert "setup -r .agent" in script
    assert script.count("bash -c --") == 1
    assert "base64" in script
```

- [ ] **Step 5: Update `_fake_proc` to a 5-field frame**

`parse_result_line` now requires 5 fields, so the shared `_fake_proc` used by the run test must return a 5-field frame to keep that test green. In `tests/test_mcp_server.py`, replace `_fake_proc` with:

```python
def _fake_proc():
    proc = mock.Mock()
    cwd_field = encode_field("/fake/cwd")
    out_field = encode_field("hello out")
    err_field = encode_field("hello err")
    rc_field = encode_field("0")
    to_field = encode_field("1")
    proc.stdout.readline.return_value = (
        f"{out_field} {err_field} {rc_field} {cwd_field} {to_field}\n"
    ).encode()
    proc.stdin = mock.Mock()
    return proc
```

(The existing `test_warm_sandbox_run_frames_command_and_tracks_cwd` still passes with this 5-field `_fake_proc`, since it only asserts `stdout`/`stderr`/`exit_code`/`cwd`. It is fully rewritten to assert the 3-line payload and `timed_out` in Task 2.)

- [ ] **Step 6: Add `test_build_driver_script_hardening`**

Append to `tests/test_mcp_server.py`:

```python
def test_build_driver_script_hardening():
    """Driver exports functions, uses a fresh bash -c child, blocks stdin, and
    applies a coreutils timeout with a timed_out flag."""
    script = build_driver_script(["setup -r .agent"])
    # exported functions so children can call setup/conda
    assert "compgen -A function" in script
    assert "export -f" in script
    # fresh non-login child, stdin detached from the framing pipe
    assert 'bash -c -- "$cmd" </dev/null' in script
    assert "bash -lc" not in script
    # timeout wrapper with SIGKILL escalation and the 124/137 -> timed_out map
    assert 'timeout --kill-after=5 "${secs}s"' in script
    assert 'if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then to=1; else to=0; fi' in script
```

- [ ] **Step 7: Run the driver/framing tests**

Run: `python -m pytest tests/test_mcp_server.py -v`
Expected: PASS — `test_parse_result_line`, `test_build_driver_script_runs_setup_once`, `test_build_driver_script_hardening`, `test_warm_sandbox_run_frames_command_and_tracks_cwd` (with the 5-field `_fake_proc`), and the pre-existing `test_build_driver_script_keeps_setup_off_framed_stdout` (the function-export line lives inside the `{ ... } >&2` block, which still has exactly one `} >&2`).

- [ ] **Step 8: Commit**

```bash
git add tkt/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): enforce timeout in driver, export functions, bash -c child, 5-field frame"
```

---

### Task 2: `WarmSandbox.run` wire-up and docstrings

**Files:**
- Modify: `tkt/mcp_server.py` — `WarmSandbox.run` (write 3 lines, propagate `timed_out`), `BashResult` docstring, `bash` tool docstring in `run_server`
- Modify: `tests/test_mcp_server.py` — `_fake_proc` (5 fields) and `test_warm_sandbox_run_frames_command_and_tracks_cwd`
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: Task 1's `build_driver_script` (3-line read, 5-field emit), `parse_result_line` (5 fields, incl. `timed_out`), existing `encode_field`.
- Produces: `WarmSandbox.run(command: str, *, timeout_ms: int | None = None) -> BashResult` writes three base64 lines (`cwd`, `command`, `timeout_ms`) and returns `BashResult` with `timed_out` from the frame; `BashResult.timed_out` is now meaningful.

- [ ] **Step 1: Update `WarmSandbox.run` to write 3 lines and propagate `timed_out`**

In `tkt/mcp_server.py`, replace the current `WarmSandbox.run` body (2-line write, no `timed_out`) with:

```python
    def run(self, command: str, *, timeout_ms: int | None = None) -> BashResult:
        if self._proc is None:
            self._start()
        assert self._proc is not None and self._proc.stdin is not None
        assert self._proc.stdout is not None
        if timeout_ms is None:
            timeout_ms = self._default_timeout_ms
        if timeout_ms < 0:
            timeout_ms = 0
        # Write cwd, command, and timeout_ms as three base64 lines.
        self._proc.stdin.write(
            encode_field(self._cwd)
            + "\n"
            + encode_field(command)
            + "\n"
            + encode_field(str(timeout_ms))
            + "\n"
        )
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            rc = self._proc.poll()
            if rc is not None:
                raise RuntimeError(f"Warm holder exited unexpectedly (exit code {rc}).")
            raise RuntimeError("Warm holder exited unexpectedly.")
        frame = parse_result_line(line.strip().decode())
        self._cwd = frame["cwd"]
        return BashResult(
            stdout=frame["stdout"],
            stderr=frame["stderr"],
            exit_code=frame["exit_code"],
            timed_out=frame["timed_out"],
        )
```

- [ ] **Step 2: Update the `BashResult` docstring**

In `tkt/mcp_server.py`, change the `BashResult` class docstring body so the `timed_out` line reads:

```python
    ``stdout`` and ``stderr`` are the child's captured output; ``exit_code``
    is its exit status; ``timed_out`` is True when the call was killed for
    exceeding its ``timeout_ms``.
```

(Replace the current "``timed_out`` is reserved for future timeout handling." sentence.)

- [ ] **Step 3: Update the `bash` tool docstring**

In `tkt/mcp_server.py`, replace the current `bash` tool docstring (which says "``timeout_ms`` is accepted but currently not enforced ... Timeout enforcement is deferred.") with:

```python
        """Run a shell command inside the tkt sandbox.

        ``timeout_ms`` defaults to 60s; a request may override it. A command
        that exceeds its timeout is killed and reported with ``timed_out``.

        Args:
            request: The command to run and optional timeout/description.
        """
```

- [ ] **Step 4: Update `test_warm_sandbox_run_frames_command_and_tracks_cwd`**

In `tests/test_mcp_server.py`, replace `test_warm_sandbox_run_frames_command_and_tracks_cwd` with:

```python
def test_warm_sandbox_run_frames_command_and_tracks_cwd(tmp_path):
    """WarmSandbox.run frames cwd+command+timeout and updates cwd/timed_out."""
    from tkt.sandbox import Sandbox

    sandbox = Sandbox(command=["opencode", "acp"])  # real Sandbox; _start asserts type
    sandbox.warm_holder_argv = mock.Mock(return_value=["bwrap", "args"])
    with mock.patch("tkt.mcp_server.subprocess.Popen", return_value=_fake_proc()):
        ws = WarmSandbox(sandbox, repo_dir=str(tmp_path), cwd="/start")
        result = ws.run("echo hi", timeout_ms=500)
    assert isinstance(result, BashResult)
    assert result.stdout == "hello out"
    assert result.stderr == "hello err"
    assert result.exit_code == 0
    assert result.timed_out is True
    assert ws.cwd == "/fake/cwd"
    # the written stdin payload is three base64 lines: cwd, command, timeout_ms
    payload = ws._proc.stdin.write.call_args[0][0].decode()
    fields = payload.splitlines()
    assert decode_field(fields[0]) == "/start"
    assert decode_field(fields[1]) == "echo hi"
    assert decode_field(fields[2]) == "500"
```

- [ ] **Step 5: Run tests + lint + typecheck**

Run:

```bash
python -m pytest tests/test_mcp_server.py tests/test_sandbox.py -v
ruff check .
ruff format --check .
mypy tkt/
```

Expected: all pass. `mypy` must confirm `timeout_ms` narrows to `int` after the `if timeout_ms is None:` branch (`_default_timeout_ms` is typed `int`), so `timeout_ms < 0` and `encode_field(str(timeout_ms))` type-check.

- [ ] **Step 6: Commit**

```bash
git add tkt/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): wire timeout_ms through run, propagate timed_out"
```
