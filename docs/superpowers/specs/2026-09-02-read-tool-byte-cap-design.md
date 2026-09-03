# `read` Tool Byte-Level Output Cap — Design Handover

**Date:** 2026-09-02
**Status:** Approved by human in conversation (before implementation).

## Goal

Close E4 in `docs/zed-agent-roadmap.md`: add a byte-level output cap to the tkt
MCP server's `read` tool, mirroring the `bash` truncation fix. `read` truncates
by **line count**, not bytes, so a file with one extremely long line (a multi-GB
single-line file — minified JS, a huge JSONL record, a log with no newlines) lets
`sed` buffer the whole line in memory (OOM risk) and lets a large slice flood the
model context window. Bound what `read` ships and what it buffers, without
breaking its line-based `offset`/`limit` paging for normal files.

## Current behavior

- `build_read_command(path, offset, limit)` emits:
  `sed -n "{start},{end}p" "$f" | base64 -w0` (after a `wc -l` `READ_TOTAL`
  marker on stderr).
- `read_tool` runs that through the warm-holder driver (same channel as `bash`),
  base64-decodes stdout, numbers each line with its absolute line number, and
  appends `... (N more lines)` when lines remain past the slice.
- The driver already hard-caps stdout at `_DRIVER_OUT_CAP = 50_000` bytes via
  `head -c`, but only as a wire bound: when a read exceeds it, `sed` is
  SIGPIPE-killed (`rc 141`) and `read_tool` returns an **error**
  (`read: [output hard-capped...]`) instead of partial content.
- `sed -n` buffers each full input line in memory, so a multi-GB single line OOMs
  the sandbox before the driver's `head -c` ever sees output.

## Decisions (approved in conversation)

1. **`bash` keeps its 5 000 default; 25 000 is the shared hard cap/ceiling.**
   Split into two constants: `_BASH_OUTPUT_CHARS = 5_000` (the model-facing cap
   `bash` already uses today — unchanged behavior) and `_MAX_OUTPUT_CHARS =
25_000` (the hard cap/ceiling that `read`'s scaling scales up to). `bash`
   continues to truncate at 5 000.
2. **`read`'s model-facing cap scales with the requested line count.** Per-call
   cap = `min(_MAX_OUTPUT_CHARS, limit * _CHARS_PER_LINE)` with
   `_CHARS_PER_LINE = 110` (this repo's ruff line-length). So `limit=2000`
   (default) → `25000`; `limit=100` → `11000`. It is applied with the existing
   head+tail `truncate_output` helper and its marker; `ReadResult.truncated`
   becomes true when the byte cap (or the line-based note) cuts the content.
3. **Bound per-line memory in the sandbox command** to fix the OOM: run the file
   through `fold -b -w "$_READ_BYTE_CAP"` before `sed`, so no single line exceeds
   `_READ_BYTE_CAP` bytes. `fold` only splits lines longer than the cap, so normal
   files (all lines below the cap) are byte-identical and `offset`/`limit` paging
   is preserved.
4. **Bound the read command's own output** with `head -c "$_READ_BYTE_CAP"` before
   `base64`, so the command caps its own output and exits cleanly (rc 0) instead
   of exceeding the driver's 50 KB wire cap and returning a hard-cap error. Set
   `_READ_BYTE_CAP = 32_000` so `base64(32_000) ≈ 42.7 KB` stays under the
   driver's `_DRIVER_OUT_CAP = 50_000` with margin.
5. **Reuse the existing `truncate_output`** head+tail helper for the model-facing
   cap (same convention as `bash`); no new truncation logic. `__all__` already
   exports `truncate_output`.
6. **No new tool parameters.** `read(file_path, offset=0, limit=2000)` is
   unchanged; the byte cap is derived from `limit` internally.

## Architecture

Three layers, mirroring the `bash` fix:

- **Sandbox command (`build_read_command`):** pipe the file through
  `fold -b -w CAP` (bounds per-line memory → fixes OOM), then `sed` the
  `offset`/`limit` slice, then `head -c CAP` (bounds output bytes → command exits
  cleanly), then `base64 -w0`.
- **Driver:** unchanged — `_DRIVER_OUT_CAP = 50_000` remains a last-resort wire
  bound; the read command's own `head -c` (32 000 raw bytes → ~42.7 KB base64)
  now keeps it from ever firing.
- **Host (`read_tool`):** after building the numbered content and the line-based
  `... (N more lines)` note, apply `truncate_output(content, _read_char_cap(limit))`
  and set `truncated = line_truncated or byte_truncated`.

### Data flow

1. `read_tool` clamps `offset`/`limit`, builds the command via
   `build_read_command(file_path, offset, limit)`.
2. `warm.run` runs it; `fold` bounds per-line memory, `head -c` bounds output, the
   driver wire cap stays as a backstop.
3. Host base64-decodes stdout, numbers the lines, appends the line-based note.
4. Host applies `truncate_output(numbered, _read_char_cap(limit))`, returns
   `ReadResult(content, truncated=line_truncated or byte_truncated)`.

All logic is host-Python and testable with a mocked `warm.run`, matching the
existing `test_mcp_server.py` style. `build_read_command` string assertions cover
the `fold`/`head -c` shape.

## Concretes (verbatim — authoritative for implementation)

### `tkt/mcp_server.py` changes

Module constants (near the existing ones):

```python
# Model-facing cap for the `bash` tool output, in characters (unchanged default).
_BASH_OUTPUT_CHARS = 5_000

# Shared hard cap / ceiling (chars) for tool output; `read`'s per-call cap
# scales up to this value.
_MAX_OUTPUT_CHARS = 25_000

# Driver-side hard cap (bytes) per stream; SIGPIPE-kills a runaway producer.
_DRIVER_OUT_CAP = 50_000

# Sandbox-side bound for `read` (bytes): `fold` wraps over-long lines at this
# width and `head -c` caps the shipped slice, so a huge single-line file can
# neither OOM `sed` nor exceed the driver's wire cap (base64(CAP) < _DRIVER_OUT_CAP).
_READ_BYTE_CAP = 32_000

# Per-line scale factor for `read`'s model-facing cap (this repo's ruff
# line-length). cap = min(_MAX_OUTPUT_CHARS, limit * _CHARS_PER_LINE).
_CHARS_PER_LINE = 110
```

New pure helper (place near `truncate_output`):

```python
def read_char_cap(limit: int) -> int:
    """Model-facing char cap for a ``read`` of ``limit`` lines.

    Scales with the requested line count (about one ruff-formatted line per
    ``_CHARS_PER_LINE`` chars) and is hard-capped at ``_MAX_OUTPUT_CHARS``.
    """
    return min(_MAX_OUTPUT_CHARS, limit * _CHARS_PER_LINE)
```

`build_read_command` pipeline changes:

```sh
fold -b -w "$READ_CAP" "$f" | sed -n "{start},{end}p" | head -c "$READ_CAP" | base64 -w0
```

(`READ_CAP` is `_READ_BYTE_CAP`, embedded into the generated command string.)

`read_tool` tail changes (after the numbered content and line-based note are
built):

```python
    # Model-facing byte cap: truncate head+tail, flagging if cut.
    content, byte_truncated = truncate_output(numbered, read_char_cap(limit))
    return ReadResult(content=content, truncated=line_truncated or byte_truncated)
```

## Key decisions log

1. **`bash` stays at 5 000; 25 000 is the shared ceiling** — `_BASH_OUTPUT_CHARS
= 5_000` (bash's unchanged default) and `_MAX_OUTPUT_CHARS = 25_000` (the hard
   cap/ceiling). `bash` behavior is unchanged; 5 000 was too small for `read`.
2. **`read`'s cap scales with `limit`** via `min(_MAX_OUTPUT_CHARS, limit * 110)`
   — an agent reading 100 lines gets a ~11 000-char budget, reading the default
   2000 gets the full 25 000.
3. **`fold -b -w` bounds per-line memory** — fixes the `sed` OOM without breaking
   normal-file paging (only over-long lines are split).
4. **`head -c` inside the read command, sized below the driver wire cap** — the
   command caps its own output and exits cleanly (rc 0), eliminating the current
   "error instead of partial content" behavior when a read exceeds 50 KB.
5. **Reuse `truncate_output`** — identical head+tail-with-marker convention as
   `bash`; `truncated` reflects both the line-based note and the byte cap.
6. **No MCP schema change** — `read`'s signature is unchanged; `ReadResult` fields
   are unchanged (only `truncated` may now be set by the byte cap too).

## Open items / assumptions

- Assumes GNU coreutils `fold` (with `-b`) is available inside the sandbox. It is
  part of coreutils, which the driver already relies on (`head`, `sed`, `base64`,
  `wc`, `timeout`, `mktemp`).
- `fold -b -w CAP` wraps by bytes, keeping the byte-based cap exact even for
  multi-byte UTF-8.
- No harness/config/machine-side changes; this is purely a server-side change to
  `tkt/mcp_server.py` and its tests.
