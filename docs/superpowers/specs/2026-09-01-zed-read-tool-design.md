# R2 Batch 1 — Sandboxed `Read` Tool — Design Handover

**Date:** 2026-09-01
**Status:** Approved by human in conversation (before implementation).
**Implements:** batch 1 (the `Read` tool) of phase R2 in `docs/zed-agent-roadmap.md`.

## Goal

Add a sandboxed `read` MCP tool to the tkt MCP server — the Claude-Code-shaped read
tool — and fold it into the Zed harness (skills mapping + `zed-explorer` wording) so
the Zed native agent reads files through the sandbox (blocking `$HOME`, honoring
workspace mounts) rather than Zed's native, unsandboxed `read_file`. This resolves
the R2 "Read tension" (the `~/.agents/skills`/`$HOME` conflict) and is the first of
the R2 MCP tool batches. OpenCode is untouched throughout.

## Architecture

The MCP server process runs **outside** the sandbox (host-side), so a `read` tool
implemented by opening files in the server would read `$HOME` unscoped. To stay
sandboxed, `read` executes a small coreutils command through the **existing warm
holder** (`WarmSandbox.run` — the same channel `bash` uses): it gets the sandbox's
mount model automatically (workspace read-only + `.agent`/git writable, `$HOME`
tmpfs-blocked, `~/.agents/skills` and configured ro/rw mounts), returns using the
existing base64 framing, and needs no new sandbox infrastructure.

The `~/.agents/skills` tension is already resolved by **R1**: `~/.agents/skills` is
in the sandbox's read-only mount list (`local.json` → `Sandbox._mounts_ro`), so the
sandboxed `read` can read skill reference files at the symlinked paths the skills
expect — even though the rest of `$HOME` is a blocked tmpfs. No new mount work is
needed; the Read batch only confirms reads work and replaces the misleading native
`read_file` guidance in the system-prompt override.

### Tool shape and naming

- MCP tool name is **`read`** (no `tkt:` prefix). The `tkt:`/`MCPNS:` nomenclature
  applied to other tools is dropped as a naming convention; existing occurrences in
  `docs/zed-agent-roadmap.md` (`tkt:bash`) are normalized to plain `bash`.
- Signature: `read(file_path, offset=0, limit=2000)`.
- Semantics (Claude Code baseline, slimmed): read a slice of a text file, numbered
  by absolute line number, with a "… N more lines" tail note when lines remain
  beyond the slice. `stop_sequence` is omitted (YAGNI — paging via `offset`/`limit`
  covers it).

### Data flow

1. Host builds a sandbox command from `path`/`offset`/`limit` (path passed safely via
   `shlex.quote`; `offset`/`limit` validated and clamped in Python).
2. The command checks the path is a regular file, reports the total line count on
   stderr as a `READ_TOTAL <n>` marker, then `sed`-selects the slice `[offset+1,
   offset+limit]` (1-based) and emits it **base64-encoded** on stdout.
   Base64 keeps the byte stream lossless through the UTF-8 decode in
   `parse_result_line`, so reading a binary file degrades to a "binary" message
   instead of crashing the framing.
3. Host decodes the base64 back to bytes, decodes UTF-8 (else "binary" message),
   numbers the slice with absolute line numbers, and appends a "… N more lines"
   note when `offset + len(lines) < total`.

All formatting/logic after the sandbox lives in host Python, so it is testable in
pure Python with a mocked `Popen` — exactly like the existing framing tests in
`tests/test_mcp_server.py`.

## Concretes (verbatim — authoritative for implementation)

### `tkt/mcp_server.py` additions

```python
__all__ = (
    "BashResult",
    "ReadResult",
    "WarmSandbox",
    "build_driver_script",
    "build_read_command",
    "decode_field",
    "encode_field",
    "parse_result_line",
    "run_server",
)
```

New model:

```python
class ReadResult(BaseModel):
    """The outcome of one sandboxed ``read`` call.

    ``content`` is the line-numbered slice of a text file. When lines remain
    past the slice, ``content`` ends with a ``... (N more lines)`` note and
    ``truncated`` is True.
    """

    content: str
    truncated: bool
```

Command builder (module-level, after `parse_result_line`):

```python
_READ_TOTAL_RE = re.compile(r"READ_TOTAL (\d+)")


def build_read_command(path: str, offset: int, limit: int) -> str:
    """Build the sandbox command that reads a slice of ``path``.

    Reads lines ``[offset+1, offset+limit]`` (1-based, via ``sed``), emitting the
    raw slice base64-encoded on stdout so the byte stream round-trips losslessly
    through the UTF-8 decode in :func:`parse_result_line` (a binary file degrades
    to a host-side "binary" message instead of crashing the framing). The total
    line count is reported on stderr as a ``READ_TOTAL <n>`` marker so the host can
    compute the truncation note. ``path`` is embedded via ``shlex.quote``.
    """
    quoted = shlex.quote(path)
    start = offset + 1
    end = offset + limit
    return (
        f"f={quoted}\n"
        'if [ ! -f "$f" ]; then printf "read: no such file or not a regular file: %s\\n" "$f" >&2; exit 1; fi\n'
        'printf "READ_TOTAL %s\\n" "$(wc -l < "$f")" >&2\n'
        f'sed -n "{start},{end}p" "$f" | base64 -w0\n'
        'printf "\\n"\n'
    )


def _parse_read_total(stderr: str) -> int | None:
    """Return the ``READ_TOTAL`` count parsed from ``stderr``, or None."""
    m = _READ_TOTAL_RE.search(stderr)
    return int(m.group(1)) if m else None
```

The MCP tool is registered inside `run_server`, alongside `bash`:

```python
    @mcp.tool()
    def read(
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
        description: str | None = None,  # present for human approvals of tool actions
    ) -> ReadResult:
        """Read a file (or a slice of it) inside the tkt sandbox.

        The sandbox blocks ``$HOME`` (so credentials are never exposed) but mounts
        the workspace and the read-only ``~/.agents/skills`` directory, so skill
        reference files are readable. ``offset`` is the number of lines to skip
        (default 0); ``limit`` is the maximum number of lines to read (default
        2000). When more lines remain past the slice, ``content`` ends with a
        ``... (N more lines)`` note and ``truncated`` is True. ``description`` is a
        per-call rationale for the human; it does not change behavior.

        Args:
            file_path: The file to read (absolute, or relative to the sandbox cwd).
            offset: Number of lines to skip from the start.
            limit: Maximum number of lines to read.
            description: Optional human-readable rationale for this call.
        """
        offset = max(0, offset)
        limit = max(1, limit)
        result = warm.run(build_read_command(file_path, offset, limit))
        if result.exit_code != 0:
            err = (result.stderr or result.stdout or "").strip()
            return ReadResult(content=f"read: {err}", truncated=False)
        total = _parse_read_total(result.stderr)
        raw = base64.b64decode(result.stdout.strip())
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return ReadResult(
                content="read: file appears to be binary (did not decode as UTF-8)",
                truncated=False,
            )
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        numbered = "\n".join(f"{offset + i + 1}\t{line}" for i, line in enumerate(lines))
        returned = offset + len(lines)
        if total is None:
            total = returned
        more = total - returned
        truncated = more > 0
        if truncated:
            numbered += f"\n... ({more} more lines)"
        return ReadResult(content=numbered, truncated=truncated)
```

`re` is imported at the top of the module.

### `superpowers/skills/using-superpowers/references/zed-tools.md`

- Row "Read a file": `read_file` → `read` (the sandboxed tkt tool).
- Row "Run a shell command": `bash` (unchanged; no `tkt:` prefix).

### `harnesses/zed/skills/zed-explorer/SKILL.md`

Line ~14: `read with read_file` → `read with the \`read\` tool`. The rest of that
sentence (find_path/grep/list_directory/bash) is unchanged for this batch — those
tools are still native.

### `docs/zed-agent-roadmap.md` (nomenclature normalization)

The four `tkt:bash` references (lines 43, 47, 106, 107) become plain `bash`, and the
mapping is written as `terminal -> bash` / `MCP bash`, dropping the `tkt:` prefix
for tool names. The target-suite table (section 4) that says "Backed by tkt MCP
(sandboxed)" is left as-is other than the naming cleanup described here.

## Key decisions log

1. **`read` runs through the warm holder, not host file IO** — that is what keeps it
   sandboxed (the server process is host-side and would otherwise read `$HOME`).
2. **Drop the `tkt:`/`MCPNS:` tool-name prefix** — tools are called by their bare
   name (`read`, `bash`). Normalize existing roadmap occurrences.
3. **No new sandbox mounts** — `~/.agents/skills` is already read-only from R1; the
   Read batch only replaces the misleading native-read guidance in the override.
4. **Base64 transport** — keeps reads byte-lossless through the UTF-8 framing and
   turns binary files into a clean "binary" message instead of a crash.
5. **Host-side formatting** (numbering + truncation) in Python — unit-testable with a
   mocked `Popen`, matching the existing framing-test style.
6. **Clamp `offset`/`limit`** (offset >= 0, limit >= 1) and validate `path` inside the
   sandbox; no new error surface.
7. **OpenCode untouched** — coexistence maintained (roadmap Goal 4).

## Open items / assumptions (machine-side, verified by human)

- The Zed agent profile (disabling native `read_file`, keeping `terminal` disabled
  with `bash`) and the system-prompt override live on the human's machine and are
  **not** in this repo. The implementing agent must NOT edit them; a paste-ready
  template is delivered in the plan's final chat summary for the human.
- Confirm empirically that a sandboxed `read` of `~/.agents/skills/<name>/SKILL.md`
  resolves (relies on R1's read-only mount).
