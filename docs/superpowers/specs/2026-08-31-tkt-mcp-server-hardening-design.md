# tkt MCP Server Hardening — Design Handover

**Date:** 2026-08-31
**Status:** Approved by human in conversation (Gate 1) before planning.

## Goal

Harden the `tkt mcp-server` sandboxed `bash` tool by (1) actually enforcing the
`timeout_ms` that the current implementation accepts but ignores, (2) fixing the
child-shell environment so fresh children inherit the warm holder's functions,
don't re-source `/etc/profile`, and can't consume the framing pipe, and (3)
documenting the `description` field's real role.

## Context

The server runs a long-lived `bwrap` "warm holder" whose driver bash loop reads
framed commands on stdin and writes framed results on stdout. Each `bash` tool
call runs the command in a **fresh child** shell of the holder. The current
implementation:

- Reads 2 lines per request (`cwd`, `command`) and writes a 4-field result
  frame (`stdout`, `stderr`, `exit_code`, `cwd`).
- Runs the child as `bash -lc -- "$cmd"` (login shell) with **no** timeout
  enforcement, **no** exported shell functions, and **no** stdin detachment.
- `BashResult.timed_out` is permanently `False`; `timeout_ms` is documented but
  not enforced.

## Key decisions (signed off in conversation)

1. **Timeout enforced in the driver (Approach A), not the server.** The server
   can't kill just one command without killing the shared warm holder, so the
   driver must do the killing. Uses coreutils `timeout` (present because the
   sandbox `--ro-bind / /` exposes the host filesystem).
   - `timeout --kill-after=5 "${secs}s"` sends SIGTERM at the deadline and
     escalates to SIGKILL after 5s for a command that ignores it; it signals the
     whole process group, so runaway descendants die too. (Ruling: the original
     `--signal=KILL` choice was changed during implementation because GNU
     `timeout --signal=KILL` re-raises the SIGKILL, so the driver observes
     `rc=137` and the `rc==124` mapping would never fire.)
   - `rc == 124` (killed by TERM at the deadline) **or** `rc == 137` (a stubborn
     command the `--kill-after` escalation KILLed) ⇒ `timed_out = 1`. A command
     legitimately returning 124/137 is reported as timed out — negligible,
     accepted.
   - Partial stdout/stderr captured to temp files is returned on timeout.
   - Timeout is enforced at whole-second granularity (sub-second values round up
     to 1s; never kills early).
2. **Drop `-l` → `bash -c`.** A fresh child that merely inherits the warm
   holder's exported env + functions is the true "warm but stateless" behavior;
   `bash -lc` re-sources `/etc/profile` every call, risking `PATH` reset. Parity
   with `sandbox-run`'s interactive `--login -i` isn't relevant to one-shot
   command runs.
3. **Export all shell functions** (`compgen -A function` loop) after setup so
   children can call `setup`/`conda`. The design previously *claimed* children
   inherit functions via `BASH_FUNC_*` but never exported them.
4. **Detach child stdin (`</dev/null`)** so a stdin-reading command can't
   consume the framing pipe.
5. **Wire framing change (internal).** Server→holder becomes 3 lines (`cwd`,
   `command`, `timeout_ms`; `0` = no timeout). Holder→server becomes 5 fields
   (`stdout`, `stderr`, `exit_code`, `cwd`, `timed_out`). Both sides ship in the
   same commit; no external compat concern.
6. **`description` kept as structured metadata (decision A).** MCP has no
   server-initiated permission RPC; approval is host-side. Zed renders MCP tools
   as `ToolKind::Other`, showing `description` only under the collapsible
   "View Raw Input" disclosure — not in the confirmation title (Zed inlines an
   arg only for single-string-arg tools). Kept because it's a portable, standard
   place for a rationale; documented as not-prominent in Zed.
7. **Default timeout stays `60000ms`** in `WarmSandbox`; no new CLI flag (YAGNI).
8. **`WarmSandbox.run` remains serialized** (single pipe); matches Zed's
   per-project server usage.

## Wire framing (server ↔ warm holder)

- **Server → holder (command):** three base64 lines, one per request:
  `encode(cwd)`, `encode(command)`, `encode(str(timeout_ms))`.
- **Holder → server (result):** one line of 5 space-separated base64 fields:
  `stdout`, `stderr`, `exit_code`, `cwd`, `timed_out` (`0`/`1`).

## Code (verbatim from the agreed design)

### `parse_result_line` (5 fields)

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

### `build_driver_script`

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

### `WarmSandbox.run`

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

### Docstrings

`BashResult.timed_out` becomes:
```python
    ``stdout`` and ``stderr`` are the child's captured output; ``exit_code``
    is its exit status; ``timed_out`` is True when the call was killed for
    exceeding its ``timeout_ms``.
```

`bash` tool docstring becomes:
```python
        """Run a shell command inside the tkt sandbox.

        ``timeout_ms`` defaults to 60s; a request may override it. A command
        that exceeds its timeout is killed and reported with ``timed_out``.

        Args:
            request: The command to run and optional timeout/description.
        """
```

## Testing

- `parse_result_line` parses 5 fields (incl. `timed_out` truthy).
- Driver script assertions: exports functions (`compgen -A function`, `export -f`),
  uses `bash -c -- "$cmd" </dev/null`, has no `bash -lc`, has
  `timeout --signal=KILL "${secs}s"`, maps `rc==124` → `to=1`.
- `WarmSandbox.run` writes a 3-line payload (`cwd`, `command`, `timeout_ms`) and
  propagates `timed_out` from the frame.
- Existing setup-stdout regression test stays green (the function-export line
  lives inside the `{ ... } >&2` block, which still has exactly one `} >&2`).
