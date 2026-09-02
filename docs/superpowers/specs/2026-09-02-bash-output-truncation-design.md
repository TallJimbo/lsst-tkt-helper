# Bash MCP Tool Output Truncation — Design Handover

**Date:** 2026-09-02
**Status:** Approved by human in conversation (before implementation).

## Goal

Guard the tkt MCP server's `bash` tool against context blowouts and against
OOM / server-hangs caused by a command (e.g. `cat` on a multi-GB file) shipping
its entire output up to the model. Cap what the model receives at 5000 chars per
stream (stdout and stderr independently), keep head+tail with a marker, and bound

- kill runaway output at the source (inside the sandbox driver) so Python never
  holds or receives a huge blob.

This mirrors how Claude Code's `Bash` tool behaves: a per-tool output cap
(`maxOutput`, default 30_000 chars) with the _middle_ of the output elided and an
ellipsis indicator. We follow the same head+tail-with-marker convention (an
approved decision from brainstorming).

## Decisions (approved in conversation)

1. **Cap is 5000 chars per stream** (stdout and stderr each), applied independently.
2. **No unlimited output** — this is a hard guard. No `max_output_chars` tool
   argument, no "0 = unlimited". A fixed module constant.
3. **Keep head + tail, with a marker in the middle** (e.g.
   `... [N chars truncated] ...`), mirroring Claude Code's ellipsis elision.
4. **Hybrid early-termination:** bounded head+tail when the command completes
   within a generous window; but **hard-kill the command** if it keeps producing
   far more than the budget (the driver caps at 10x the 5000 budget = 50_000 bytes
   per stream via `head -c`, which SIGPIPE-kills the producer). This avoids waiting
   for — and transferring — a multi-GB blob, the current OOM/hang point.
5. **Signal truncation** to the model with a boolean `truncated` field on the
   result, and a hard-cap marker on stderr when the command was killed for
   over-producing.

## Architecture

Two layers, mirroring how Claude Code separates the per-tool config cap from the
model-visible truncation:

- **Host layer (`tkt/mcp_server.py`):** a pure `truncate_output(text, max_chars)`
  helper that keeps head+tail within `max_chars` with a dropped-count marker and
  returns `(text, truncated)`. The `bash` MCP tool applies it to both `stdout` and
  `stderr` (5000 each), sets `BashResult.truncated` if either stream was cut. This
  is the context-blowout guard and is unit-testable in pure Python.
- **Driver layer (`build_driver_script`):** replace the unbounded
  `bash -c -- "$cmd" >"$out" 2>"$errf"` with a hard-capped pipe so a multi-GB
  `cat` can never ship its whole contents as one base64 line (the current
  OOM/hang). `head -c "$OC"` on each stream stops reading after the cap and closes
  the pipe, SIGPIPE-killing the producer (the hybrid "hard-kill"); temp files stay
  on disk (already the case), and only ≤`OC` bytes ever reach Python. A `wait`
  syncs the stderr process substitution before the result is framed, and when the
  command was killed for over-producing (`rc == 141`, SIGPIPE) a marker is
  appended to stderr so the host surfaces it.

On a runaway kill the command yields head-only (no tail) plus the hard-cap marker
— the unavoidable cost of killing before the end, and the chosen hybrid behavior.

### Data flow

1. Host builds `truncate_output`; the `bash` tool calls `warm.run(command)`
   exactly as today.
2. The warm-holder driver runs the command with stdout piped through
   `head -c "$OC"` into `"$out"` and stderr through a `head -c "$OC"` process
   substitution into `"$errf"` (both `OC = 50_000`). If a stream exceeds `OC`, the
   pipe closes and the producer is SIGPIPE-killed (`rc == 141`); a marker is
   appended to `"$errf"`.
3. The driver frames the existing 5 base64 fields (stdout, stderr, exit_code,
   cwd, timed_out) — unchanged framing; `rc=${PIPESTATUS[0]}` preserves the
   command's own exit code so the `124/137 -> timed_out` mapping still works.
4. `warm.run` returns a `BashResult`; the `bash` tool runs both `stdout` and
   `stderr` through `truncate_output(_, 5000)`, sets `truncated` if either was
   cut, and returns the model-facing `BashResult`.

All `truncate_output` logic lives in host Python and is testable with a mocked
`Popen`, matching the existing framing-test style. The driver's bounded
cap/kill behavior is additionally validated by a real subprocess test that runs
`build_driver_script([])` output under host `bash` (no bwrap) and exercises the
framing protocol directly.

## Concretes (verbatim — authoritative for implementation)

### `tkt/mcp_server.py` changes

New module constants (place near the other module-level constants):

```python
# Model-facing cap for a single `bash` stream (stdout or stderr), in characters.
_MAX_OUTPUT_CHARS = 5_000

# Driver-side hard cap (bytes) per stream; SIGPIPE-kills a runaway producer.
_DRIVER_OUT_CAP = 50_000
```

`__all__` gains `"truncate_output"`.

`BashResult` gains a `truncated: bool = False` field (backed by the model):

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

New pure helper (place after `decode_field` / before `parse_result_line`):

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
```

The truncation wiring is factored into a pure, testable helper `_cap_result` (so
it can be unit-tested without invoking the MCP closure), and the `bash` MCP tool
(inside `run_server`) calls it:

```python
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

Only `truncate_output` is added to `__all__`; `_cap_result` is module-private and not exported.

### `build_driver_script` — bounded transfer + hard-cap kill

The loop-body capture changes from:

```sh
    out=$(mktemp)
    errf=$(mktemp)
    if [ "$tmo" -gt 0 ]; then
        secs=$(( (tmo + 999) / 1000 ))
        timeout --kill-after=5 "${secs}s" bash -c -- "$cmd" </dev/null >"$out" 2>"$errf"
    else
        bash -c -- "$cmd" </dev/null >"$out" 2>"$errf"
    fi
    rc=$?
```

to:

```sh
    out=$(mktemp)
    errf=$(mktemp)
    if [ "$tmo" -gt 0 ]; then
        secs=$(( (tmo + 999) / 1000 ))
        timeout --kill-after=5 "${secs}s" bash -c -- "$cmd" </dev/null 2> >(head -c "$OC" >"$errf") | head -c "$OC" >"$out"
    else
        bash -c -- "$cmd" </dev/null 2> >(head -c "$OC" >"$errf") | head -c "$OC" >"$out"
    fi
    rc=${PIPESTATUS[0]}
    wait
    if [ "$rc" -eq 141 ]; then
        printf '\n[output hard-capped: command produced more than %s bytes and was killed]\n' "$OC" >>"$errf"
    fi
```

- The `| head -c "$OC" >"$out"` bounds stdout and, when the cap is reached, closes
  the pipe → the producer gets SIGPIPE and dies (`rc == 141`).
- The `2> >(head -c "$OC" >"$errf")` process substitution bounds stderr the same
  way; the bare `wait` after the pipeline syncs that background `head` so the
  `"$errf"` file is fully written before it is framed (avoids a read-before-flush
  race).
- `rc=${PIPESTATUS[0]}` captures the _command's_ exit status (the first pipeline
  element), not `head`'s, so the existing `124/137 -> timed_out` mapping and the
  new `141` check both read the right value.
- `"$OC"` is set once near the top of the generated script via
  `f'OC="{_DRIVER_OUT_CAP}"\n'` (inserted after the setup block redirect closes,
  before the `while` loop).

## Key decisions log

1. **Split into host + driver layers** — host `truncate_output` is the simple,
   testable context guard (mirrors Claude Code's `maxOutput`); the driver bounds
   transfer and kills runaway output at the source so Python never holds a huge
   blob (fixes the OOM/hang).
2. **Fixed caps, no tool argument** — `_MAX_OUTPUT_CHARS = 5000` (per stream),
   `_DRIVER_OUT_CAP = 50000` (10x). No unlimited path (approved: "no unlimited,
   even as an option"). No new tool parameters, so no MCP schema change beyond the
   new `truncated` field on the result.
3. **Head+tail with dropped-count marker** — matches Claude Code's ellipsis
   elision; the marker reports the exact dropped char count each stream.
4. **`head -c` pipe for the hard kill** — closing the pipe SIGPIPE-kills a
   runaway producer (the chosen hybrid), keeps temp files on disk (no memory
   blowup), and bounds what reaches Python. `PIPESTATUS[0]` preserves the command's
   real exit code; bare `wait` syncs the stderr process substitution.
5. **`rc == 141` (SIGPIPE) marks the hard cap** — the driver appends a
   stderr marker the host surfaces, so the model knows a large amount was dropped
   and the command was killed.
6. **Built-in `read` is untouched** — it already has its own line-based
   `offset`/`limit` truncation and a separate `ReadResult`; nothing changes.
7. **Real subprocess driver test (no bwrap)** — runs `build_driver_script([])`
   output under host `bash` and drives the framing protocol directly to prove the
   cap bounds bytes, kills the producer, and preserves the command's `rc`.
   Supersedes string-only driver assertions.

## Open items / assumptions (machine-side, verified by human)

- None. The truncation is purely a server-side change to `tkt/mcp_server.py` and
  its tests; there is no Zed/OpenCode harness wiring, no config file, no
  machine-side profile change. The `bash` tool's existing MCP name and signature
  `bash(command, timeout_ms=None, description=None)` are unchanged; only the
  returned `BashResult` gains a `truncated` field.
- Assumes GNU coreutils `head -c`, `timeout`, `base64 -w0`, and `mktemp` are
  available inside the sandbox (they already are — the current driver uses
  `timeout`, `base64 -w0`, `mktemp`, `sed`, `wc`).
