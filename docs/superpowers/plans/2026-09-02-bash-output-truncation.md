# Bash MCP Tool Output Truncation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guard the tkt MCP server's `bash` tool against context blowouts and against OOM / server-hangs by capping each output stream at 5000 chars (head+tail with a marker, `truncated` flag) and hard-capping runaway output in the sandbox driver at 50000 bytes per stream (SIGPIPE-killing the producer), mirroring how Claude Code's `Bash` tool caps output.

**Architecture:** Two layers in `tkt/mcp_server.py`. A pure host helper `truncate_output(text, max_chars)` keeps head+tail within the cap with a dropped-count marker; the `bash` tool applies it to both `stdout` and `stderr` via a testable `_cap_result` wrapper and sets `BashResult.truncated`. Separately, `build_driver_script` replaces the unbounded `> "$out" 2> "$errf"` capture with `head -c "$OC"` pipes on both streams (`OC = 50000`), which bound transfer and SIGPIPE-kill a runaway producer; `rc=${PIPESTATUS[0]}` preserves the command's real exit code and a bare `wait` syncs the stderr process substitution before framing.

**Tech Stack:** Python 3.13, pydantic, pytest, host `bash` (for the driver integration test). No new third-party deps.

**Spec:** `docs/superpowers/specs/2026-09-02-bash-output-truncation-design.md`

## Global Constraints

- Python 3.13; deps are `click`, `GitPython`, `pyyaml`, `json5` — no new third-party dependencies.
- Every `.py` file carries the BSD-3-Clause header exactly as in existing modules (preserve it; do not alter).
- Must pass before each commit and at the end: `ruff check .` and `ruff format --check .` and `mypy tkt/`.
- `tkt` is not pip-distributed; do not add packaging config.
- Caps are fixed: `_MAX_OUTPUT_CHARS = 5000` (per stream), `_DRIVER_OUT_CAP = 50000` (per stream). No tool argument; no "unlimited" path. Do NOT add `max_output_chars` to the `bash` tool signature.
- Do not change the existing fields or fixed 5-field framing of the driver result line (`stdout stderr exit_code cwd timed_out`) or `parse_result_line`.
- The built-in `read` tool is untouched (it has its own line-based `offset`/`limit` truncation).
- Every step that shows code must be transcribed; test expectations must match the exact marker text `... [N chars truncated] ...` and driver marker text `[output hard-capped: command produced more than N bytes and was killed]`.

---

### Task 1: Host-layer truncation (`truncate_output`, `BashResult.truncated`, `_cap_result`, `bash` wiring)

**Files:**

- Modify: `tkt/mcp_server.py`
- Test: `tests/test_mcp_server.py`

**Interfaces:**

- Consumes: existing `BashResult`, `warm.run`, `run_server`.
- Produces: module constants `_MAX_OUTPUT_CHARS = 5000`, helper `truncate_output(text, max_chars) -> tuple[str, bool]`, private `_cap_result(result, max_chars) -> BashResult`, `BashResult.truncated: bool = False`, `bash` tool wired through `_cap_result`. `__all__` gains `"truncate_output"`.

- [ ] **Step 1: Write the failing tests**

Append the following to the top-of-file imports in `tests/test_mcp_server.py` (the file already imports `from tkt.mcp_server import (...)`):

```python
    _cap_result,
    truncate_output,
```

Add these tests at the end of `tests/test_mcp_server.py`:

```python
def test_truncate_output_passthrough_when_within_cap():
    """Text within max_chars is returned unchanged with truncated=False."""
    text = "a" * 100
    out, truncated = truncate_output(text, 5000)
    assert out == text
    assert truncated is False


def test_truncate_output_at_cap_not_truncated():
    """Text exactly at max_chars is not truncated."""
    text = "a" * 5000
    out, truncated = truncate_output(text, 5000)
    assert out == text
    assert truncated is False


def test_truncate_output_head_and_tail_with_marker():
    """Oversized text keeps half head, half tail, and an exact dropped-count marker."""
    text = "A" * 7000
    out, truncated = truncate_output(text, 5000)
    assert truncated is True
    assert out == "A" * 2500 + "\n... [2000 chars truncated] ...\n" + "A" * 2500


def test_cap_result_truncates_and_sets_flag():
    """_cap_result cuts an oversized stream and sets truncated when either is cut."""
    result = BashResult(stdout="A" * 7000, stderr="ok", exit_code=0)
    capped = _cap_result(result, 5000)
    assert capped.truncated is True
    assert len(capped.stdout) < 7000
    assert "truncated]" in capped.stdout
    assert capped.stderr == "ok"
    assert capped.exit_code == 0
    assert capped.timed_out is False


def test_cap_result_no_truncation_preserves_fields():
    """A within-cap result passes through unchanged, truncated=False."""
    result = BashResult(stdout="hi", stderr="err", exit_code=3, timed_out=True)
    capped = _cap_result(result, 5000)
    assert capped.stdout == "hi"
    assert capped.stderr == "err"
    assert capped.exit_code == 3
    assert capped.timed_out is True
    assert capped.truncated is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_mcp_server.py -k "truncate_output or cap_result" -v`
Expected: FAIL — `truncate_output` / `_cap_result` not defined (ImportError), tests error.

- [ ] **Step 3: Write the minimal implementation**

In `tkt/mcp_server.py`:

1. Add the two module constants right after `_DEFAULT_VC_PORT = 8081` (line ~71):

```python
# Model-facing cap for a single `bash` stream (stdout or stderr), in characters.
_MAX_OUTPUT_CHARS = 5_000

# Driver-side hard cap (bytes) per stream; SIGPIPE-kills a runaway producer.
_DRIVER_OUT_CAP = 50_000
```

2. Add `"truncate_output"` to `__all__` (alphabetical, it currently ends with `"run_server",` — insert before it):

```python
    "run_server",
    "truncate_output",
```

3. Add `truncated: bool = False` to `BashResult` and update its docstring:

```python
class BashResult(BaseModel):
    """The outcome of one sandboxed ``bash`` call.

    ``stdout`` and ``stderr`` are the child's captured output (truncated to
    ``_MAX_OUTPUT_CHARS`` for the model); ``exit_code`` is its exit status;
    ``timed_out`` is True when the call was killed for exceeding its
    ``timeout_ms``; ``truncated`` is True when either stream was cut to
    ``_MAX_OUTPUT_CHARS``.
    """

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    truncated: bool = False
```

4. Add `truncate_output` and `_cap_result` right after `decode_field` (before `parse_result_line`):

```python
def truncate_output(text: str, max_chars: int) -> tuple[str, bool]:
    """Keep head+tail of ``text`` within ``max_chars``, returning ``(text, truncated)``.

    When ``len(text) <= max_chars`` the input is returned unchanged with
    ``truncated=False``. When it must be cut, roughly half the budget is kept as
    the head and half as the tail, joined by a marker reporting exactly how many
    characters were dropped. 0 or negative ``max_chars`` keeps marker-only output.
    """
    if len(text) <= max_chars:
        return text, False
    n_head = max_chars // 2
    n_tail = max_chars - n_head
    head = text[:n_head]
    tail = text[-n_tail:] if n_tail else ""
    dropped = len(text) - len(head) - len(tail)
    marker = f"\n... [{dropped} chars truncated] ...\n"
    return head + marker + tail, True


def _cap_result(result: BashResult, max_chars: int) -> BashResult:
    """Apply ``truncate_output`` to both streams of ``result``, setting ``truncated``."""
    stdout, s_trunc = truncate_output(result.stdout, max_chars)
    stderr, e_trunc = truncate_output(result.stderr, max_chars)
    return BashResult(
        stdout=stdout,
        stderr=stderr,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        truncated=s_trunc or e_trunc,
    )
```

5. Rewire the `bash` MCP tool (inside `run_server`) to route through `_cap_result`:

```python
    @mcp.tool()
    def bash(
        command: str,
        timeout_ms: int | None = None,
        description: str | None = None,  # present for human approvals of tool actions
    ) -> BashResult:
        """Run a shell command inside the tkt sandbox.

        ``timeout_ms`` defaults to 60s; a request may override it. A command that
        exceeds its timeout is killed and reported with ``timed_out``. The command
        is also hard-capped in the sandbox at ``_DRIVER_OUT_CAP`` bytes per stream
        (a runaway producer is killed); the model sees stdout/stderr truncated to
        ``_MAX_OUTPUT_CHARS`` chars each (head+tail, ``truncated`` set if cut).
        ``description`` is a per-call rationale for the human (e.g. shown when a
        host requests tool confirmation); it is not used to change behavior.

        Args:
            command: The shell command to run.
            timeout_ms: Kill the command after this many milliseconds (0 means
                no timeout).
            description: Optional human-readable rationale for this call.
        """
        return _cap_result(warm.run(command, timeout_ms=timeout_ms), _MAX_OUTPUT_CHARS)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_mcp_server.py -v`
Expected: PASS (existing tests + the new host-layer tests).

- [ ] **Step 5: Run lint/type checks**

Run: `ruff check tkt/mcp_server.py tests/test_mcp_server.py && ruff format --check tkt/mcp_server.py tests/test_mcp_server.py && mypy tkt/mcp_server.py`
Expected: clean. Fix docstring line-length gotchas per AGENTS.md if ruff flags them.

- [ ] **Step 6: Commit**

```bash
git add tkt/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): cap bash tool output at 5000 chars/stream"
```

---

### Task 2: Driver-side bounded transfer + hard-cap kill (`build_driver_script`)

**Files:**

- Modify: `tkt/mcp_server.py` (the `build_driver_script` function)
- Test: `tests/test_mcp_server.py`

**Interfaces:**

- Consumes: `_DRIVER_OUT_CAP` (added in Task 1), existing `build_driver_script(setup_lines) -> str`, `parse_result_line`, `encode_field`.
- Produces: `build_driver_script` emits a script that caps each stream at `_DRIVER_OUT_CAP` via `head -c`, SIGPIPE-kills a runaway producer, preserves the command's real exit code via `PIPESTATUS[0]`, syncs the stderr process substitution, and appends a hard-cap marker to stderr when `rc == 141`. The 5-field framing and `parse_result_line` are unchanged.

- [ ] **Step 1: Write the failing tests**

Append the following import additions to `tests/test_mcp_server.py` top-of-file `from tkt.mcp_server import (...)` block:

```python
    encode_field,
    parse_result_line,
    truncate_output,
```

(Add `encode_field` and `parse_result_line` if not present. `truncate_output` was added in Task 1.) Also add `import subprocess as sp` and `import pytest` at the top if not already present.

Add a driver-execution helper and tests at the end of `tests/test_mcp_server.py`:

```python
def _run_driver(tmp_path, command, timeout_ms="0"):
    """Run build_driver_script([]) output under host bash; return parsed frame."""
    script = tmp_path / "driver.sh"
    script.write_text(build_driver_script([]), encoding="utf-8")
    proc = sp.Popen(
        ["bash", str(script)],
        stdin=sp.PIPE,
        stdout=sp.PIPE,
        stderr=sp.PIPE,
        text=True,
    )
    payload = f"{encode_field(str(tmp_path))}\n{encode_field(command)}\n{encode_field(timeout_ms)}\n"
    out, err = proc.communicate(payload)
    assert proc.returncode == 0, err
    # result line may begin with a space when stdout is empty; only strip trailing newline.
    return parse_result_line(out.rstrip("\n"))


def test_driver_hard_caps_oversized_stdout_and_kills_producer(tmp_path):
    """A multi-MB stdout stream is capped at _DRIVER_OUT_CAP and the producer is killed."""
    frame = _run_driver(tmp_path, "seq 1 1000000")
    assert len(frame["stdout"]) <= 50_000
    assert "output hard-capped" in frame["stderr"]
    assert frame["exit_code"] != 0  # killed by SIGPIPE when head closed the pipe


def test_driver_preserves_small_output_and_rc(tmp_path):
    """Within-cap output passes through unchanged and the command's rc is preserved."""
    frame = _run_driver(tmp_path, "printf 'hi'")
    assert frame["stdout"] == "hi"
    assert frame["stderr"] == ""
    assert frame["exit_code"] == 0

    frame = _run_driver(tmp_path, "exit 7")
    assert frame["stdout"] == ""
    assert frame["exit_code"] == 7
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_mcp_server.py -k "driver" -v`
Expected: FAIL — `seq 1 1000000` currently produces the whole ~6.9 MB into `frame["stdout"]`, so `assert len(frame["stdout"]) <= 50_000` fails (and no `output hard-capped` marker appears).

- [ ] **Step 3: Write the minimal implementation**

Replace the entire `build_driver_script` function in `tkt/mcp_server.py` (currently lines ~176-221) with:

```python
def build_driver_script(setup_lines: list[str]) -> str:
    """Build the warm-holder driver bash script.

    ``setup_lines`` run once at startup (conda activation + EUPS setup), with
    their stdout redirected to stderr so startup diagnostics never leak into the
    framed stdout channel. The EUPS/conda shell functions are exported so fresh
    children can call ``setup``/``conda``. Then a loop reads three base64 lines
    per request (cwd, command, timeout_ms), runs the command in a fresh ``bash -c``
    child (under ``timeout --kill-after=5`` when a positive timeout is given:
    default TERM at the deadline, escalating to KILL), and emits one result line
    of 5 space-separated base64 fields (stdout, stderr, exit_code, cwd, timed_out).
    ``timed_out`` maps from ``rc==124 || rc==137``. Both streams are hard-capped at
    ``_DRIVER_OUT_CAP`` bytes via ``head -c``: a producer that exceeds the cap is
    SIGPIPE-killed (``rc==141``), a marker is appended to stderr, and only the
    capped bytes ever reach the frame. ``rc=${PIPESTATUS[0]}`` preserves the
    command's own exit code; the bare ``wait`` syncs the stderr process
    substitution before framing. The ``timeout_ms`` value is enforced at
    whole-second granularity (sub-second values round up to 1s).
    """
    setup = "\n".join(setup_lines)
    return (
        "{\n"
        f"{setup}\n"
        '    while IFS= read -r _f; do export -f "$_f"; done < <(compgen -A function)\n'
        "} >&2\n"
        f'OC="{_DRIVER_OUT_CAP}"\n'
        "while IFS= read -r cwd_b64 && IFS= read -r cmd_b64 && IFS= read -r tmo_b64; do\n"
        "    cwd=$(printf '%s' \"$cwd_b64\" | base64 -d)\n"
        "    cmd=$(printf '%s' \"$cmd_b64\" | base64 -d)\n"
        "    tmo=$(printf '%s' \"$tmo_b64\" | base64 -d)\n"
        '    cd "$cwd" 2>/dev/null || true\n'
        "    out=$(mktemp)\n"
        "    errf=$(mktemp)\n"
        '    if [ "$tmo" -gt 0 ]; then\n'
        "        secs=$(( (tmo + 999) / 1000 ))\n"
        '        timeout --kill-after=5 "${secs}s" bash -c -- "$cmd" </dev/null 2> >(head -c "$OC" >"$errf") | head -c "$OC" >"$out"\n'
        "    else\n"
        '        bash -c -- "$cmd" </dev/null 2> >(head -c "$OC" >"$errf") | head -c "$OC" >"$out"\n'
        "    fi\n"
        "    rc=${PIPESTATUS[0]}\n"
        "    wait\n"
        '    if [ "$rc" -eq 141 ]; then\n'
        '        printf "\\n[output hard-capped: command produced more than %s bytes and was killed]\\n" "$OC" >>"$errf"\n'
        "    fi\n"
        "    cur=$(pwd)\n"
        '    if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then to=1; else to=0; fi\n'
        '    printf "%s %s %s %s %s\\n" \\\n'
        '        "$(base64 -w0 < "$out")" \\\n'
        '        "$(base64 -w0 < "$errf")" \\\n'
        '        "$(printf \'%s\' "$rc" | base64 -w0)" \\\n'
        '        "$(printf \'%s\' "$cur" | base64 -w0)" \\\n'
        '        "$(printf \'%s\' "$to" | base64 -w0)"\n'
        '    rm -f "$out" "$errf"\n'
        "done\n"
    )
```

- [ ] **Step 4: Verify the existing string-based driver tests still hold**

Run: `pytest tests/test_mcp_server.py -k "build_driver_script or driver" -v`
Expected: PASS. Confirm `test_build_driver_script_hardening` still finds `'bash -c -- "$cmd" </dev/null'`, `'timeout --kill-after=5 "${secs}s"'`, and the `124 || 137` mapping; `test_build_driver_script_keeps_setup_off_framed_stdout` still passes (the pre-loop setup block is unchanged) — the new `OC="50000"` line is emitted _after_ the `} >&2` redirect closes.

- [ ] **Step 5: Run the full test file**

Run: `pytest tests/test_mcp_server.py -v`
Expected: PASS (all existing + new tests).

- [ ] **Step 6: Run lint/type checks**

Run: `ruff check tkt/mcp_server.py tests/test_mcp_server.py && ruff format --check tkt/mcp_server.py tests/test_mcp_server.py && mypy tkt/mcp_server.py`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add tkt/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): hard-cap bash driver output and kill runaway producers"
```

---

### Task 3: Full-suite verification

**Files:**

- None to modify; this is the whole-repo check before finishing.

**Interfaces:**

- None. Runs the repo's full checks to confirm nothing else regressed (in particular the `read` tool unaffected, and all of `tests/` passes).

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 2: Run the repo lint/format/type gate**

Run: `ruff check . && ruff format --check . && mypy tkt/`
Expected: clean. Fix any new violations (likely none — the changes are confined to `tkt/mcp_server.py` and its tests).

- [ ] **Step 3: Confirm no unrelated files changed**

Run: `git status --short`
Expected: only `tkt/mcp_server.py`, `tests/test_mcp_server.py`, and the two docs files (`docs/superpowers/specs/2026-09-02-bash-output-truncation-design.md`, `docs/superpowers/plans/2026-09-02-bash-output-truncation.md`) changed/added.

- [ ] **Step 4: Commit if any verification-only changes were made**

```bash
git add -A
git commit -m "chore: verify full suite after bash output truncation"  # only if there are changes
```

(If `git status` was already clean after Task 2's commit, skip this commit.)

---

## Self-Review

**Spec coverage:**

- Host `truncate_output` (5000/stream, head+tail, dropped-count marker) → Task 1.
- `BashResult.truncated` field → Task 1.
- `_cap_result` wiring + `bash` docstring → Task 1.
- Driver `head -c "$OC"` bounded transfer + SIGPIPE kill + `PIPESTATUS[0]` + `wait` sync + hard-cap marker → Task 2.
- `_DRIVER_OUT_CAP = 50000` (10x) and `_MAX_OUTPUT_CHARS = 5000` fixed, no unlimited → Tasks 1 & 2.
- Real subprocess driver test (no bwrap) → Task 2.
- Untouched: `read` tool, 5-field framing, `parse_result_line` → enforced by task scoping.

**Placeholders:** All steps carry concrete code/commands; no "implement later" or "handle edge cases".

**Type consistency:** `truncate_output(text: str, max_chars: int) -> tuple[str, bool]` (Task 1) is called by `_cap_result(BashResult, int) -> BashResult`; `BashResult.truncated: bool` defaults `False`; `warm.run` still returns a 4-field `BashResult` (truncated defaults) and is consumed unchanged; `_cap_result` is defined in Task 1 Step 3 and used in the same task's `bash` tool and referenced in tests; `encode_field`/`parse_result_line` in Task 2's `_run_driver` match their existing signatures. `_DRIVER_OUT_CAP` (Task 1 constant) is used in Task 2's `build_driver_script`. The driver marker text `[output hard-capped: ...]` (Task 2 impl) matches Task 2's test `"output hard-capped" in frame["stderr"]`.
