# R2 Batch 1 — Sandboxed `read` Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a sandboxed `read` MCP tool to the tkt MCP server and fold it into the Zed harness (skills mapping + `zed-explorer` wording), so the Zed native agent reads files through the sandbox rather than Zed's native, unsandboxed `read_file`. Also drop the `tkt:` tool-name prefix nomenclature (e.g. `tkt:bash` -> `bash`) per the naming decision.

**Architecture:** `read` runs through the existing warm holder (`WarmSandbox.run`), the same channel `bash` uses — that keeps it sandboxed because the MCP server process is host-side. The tool builds a coreutils command (path via `shlex.quote`; `sed` slice; `READ_TOTAL` marker on stderr; base64 stdout) and post-processes the `BashResult` in host Python (decode, number lines, append truncation note). `~/.agents/skills` is already mounted read-only from R1, so sandboxed reads of skill files work; no new mounts. Native `read_file` is left in place in the repo (the disabling happens in the human's profile, delivered as a paste-ready snippet).

**Tech Stack:** Python 3.13, click, pydantic, pytest. No new third-party deps.

**Spec:** `docs/superpowers/specs/2026-09-01-zed-read-tool-design.md`

## Global Constraints

- Python 3.13; deps are `click`, `GitPython`, `pyyaml`, `json5` — no new third-party dependencies.
- Every `.py` file carries the BSD-3-Clause header exactly as in existing modules (preserve it; do not alter).
- Must pass before each commit and at the end: `ruff check .` and `ruff format --check .` and `mypy tkt/`.
- `tkt` is not pip-distributed; do not add packaging config.
- OpenCode workflow must keep working throughout (coexistence).
- Only the four `tkt:bash` references in `docs/zed-agent-roadmap.md` (lines 43, 47, 106, 107) change for the naming normalization; the target-suite table in section 4 already uses bare `bash`/`read` names and is left as-is.
- The native `read_file` tool is **not** disabled in the repo; disabling it and the system-prompt override are machine-side (human-applied), delivered as a snippet in the final Gate-2 summary.
- Do not touch `superpowers/` (submodule) — but `superpowers/skills/using-superpowers/references/zed-tools.md` IS edited by this plan (it is a tracked file in this repo, or if it lives only in the submodule, note that the install symlinks it; see Task 4).

---

### Task 1: Add the `read` tool to the MCP server

**Files:**

- Modify: `tkt/mcp_server.py`
- Test: `tests/test_mcp_server.py`

**Interfaces:**

- Consumes: existing `WarmSandbox.run`, `BashResult`, `base64`, `re` (to be imported).
- Produces: `ReadResult` model, `build_read_command(path, offset, limit) -> str`, `_parse_read_total(stderr) -> int | None`, and the `read` MCP tool registered in `run_server`.

- [ ] **Step 1: Write the failing tests**

Append the license header already present. In `tests/test_mcp_server.py`:

1. Add `import base64` to the top-of-file imports (the file already imports
   `from unittest import mock`).
2. Add these tests:

```python
from tkt.mcp_server import (
    BashResult,
    build_read_command,
    read_tool,
)


def test_build_read_command_quotes_path_and_slices():
    """build_read_command quotes the path and selects the [offset+1, offset+limit] slice."""
    cmd = build_read_command("/a b/c.txt", offset=0, limit=2000)
    assert "'/a b/c.txt'" in cmd  # shlex.quote wraps in single quotes
    assert 'sed -n "1,2000p"' in cmd
    assert '"$f"' in cmd
    assert "wc -l" in cmd
    assert "base64 -w0" in cmd
```

```python
def test_build_read_command_respects_offset():
    """offset shifts the 1-based sed range and does not renumber."""
    cmd = build_read_command("/tmp/x.txt", offset=5, limit=3)
    assert 'sed -n "6,8p"' in cmd
```

```python
def test_read_tool_numbers_lines_and_no_truncation():
    """A full slice is numbered with absolute line numbers and not truncated."""
    from tkt.mcp_server import read_tool

    sl = b"a\nbb\nccc\n"
    warm = mock.Mock()
    warm.run.return_value = BashResult(
        stdout=base64.b64encode(sl).decode(), stderr="READ_TOTAL 3\n", exit_code=0
    )
    res = read_tool(warm, file_path="/tmp/x.txt")
    assert res.content == "1\ta\n2\tbb\n3\tccc"
    assert res.truncated is False


def test_read_tool_truncation_note():
    """A partial slice appends a '... (N more lines)' note and truncated=True."""
    from tkt.mcp_server import read_tool

    sl = b"l1\nl2\nl3\nl4\nl5\n"
    warm = mock.Mock()
    warm.run.return_value = BashResult(
        stdout=base64.b64encode(sl).decode(), stderr="READ_TOTAL 5\n", exit_code=0
    )
    res = read_tool(warm, file_path="/tmp/x.txt", offset=0, limit=2)
    assert res.content == "1\tl1\n2\tl2\n... (3 more lines)"
    assert res.truncated is True


def test_read_tool_offset_numbers_from_absolute():
    """offset skips leading lines but numbers from the true line number."""
    from tkt.mcp_server import read_tool

    sl = b"l3\nl4\n"
    warm = mock.Mock()
    warm.run.return_value = BashResult(
        stdout=base64.b64encode(sl).decode(), stderr="READ_TOTAL 5\n", exit_code=0
    )
    res = read_tool(warm, file_path="/tmp/x.txt", offset=2, limit=2)
    assert res.content == "3\tl3\n4\tl4"
    assert res.truncated is True  # 2 returned + 2 offset = 4 < 5


def test_read_tool_missing_file_error():
    """A nonzero exit propagates a read: ... error in content."""
    from tkt.mcp_server import read_tool

    warm = mock.Mock()
    warm.run.return_value = BashResult(
        stdout="", stderr="read: no such file or not a regular file: /nope\n", exit_code=1
    )
    res = read_tool(warm, file_path="/nope")
    assert res.content.startswith("read: ")
    assert "no such file" in res.content
    assert res.truncated is False


def test_read_tool_binary_file():
    """A slice that is not UTF-8 yields a binary-file message instead of a crash."""
    from tkt.mcp_server import read_tool

    raw = b"\xff\xfe\x00binary"
    warm = mock.Mock()
    warm.run.return_value = BashResult(
        stdout=base64.b64encode(raw).decode(), stderr="READ_TOTAL 1\n", exit_code=0
    )
    res = read_tool(warm, file_path="/tmp/blob")
    assert "binary" in res.content
    assert res.truncated is False


def test_read_tool_clamps_offset_and_limit():
    """offset clamps to >=0, limit to >=1."""
    from tkt.mcp_server import read_tool

    warm = mock.Mock()
    warm.run.return_value = BashResult(
        stdout=base64.b64encode(b"x\n").decode(), stderr="READ_TOTAL 1\n", exit_code=0
    )
    res = read_tool(warm, file_path="/tmp/x.txt", offset=-5, limit=0)
    assert res.content == "1\tx"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_mcp_server.py -k read -v`
Expected: FAIL — `ReadResult` / `build_read_command` don't exist (ImportError), tests error.

- [ ] **Step 3: Write the minimal implementation**

In `tkt/mcp_server.py`:

Add to imports (top of file, after existing stdlib imports):

```python
import base64
import os
import re
import shlex
import subprocess
from typing import Any
```

Add `ReadResult` model after `BashResult`:

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

Add to `__all__`:

```python
    "ReadResult",
    "build_read_command",
```

Add helper functions after `parse_result_line`:

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


def read_tool(warm: WarmSandbox, *, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
    """Run one sandboxed ``read`` against ``warm`` and return a :class:`ReadResult`."""
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

Register the tool inside `run_server`, after `bash`:

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
        return read_tool(warm, file_path=file_path, offset=offset, limit=limit)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_mcp_server.py -k read -v` and `pytest tests/test_mcp_server.py -v`
Expected: PASS (existing tests + new).

- [ ] **Step 5: Run lint/type checks**

Run: `ruff check tkt/mcp_server.py tests/test_mcp_server.py && ruff format --check tkt/mcp_server.py tests/test_mcp_server.py && mypy tkt/mcp_server.py`
Expected: clean. Fix docstring line-length gotchas per AGENTS.md if ruff flags them.

- [ ] **Step 6: Commit**

```bash
git add tkt/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): add sandboxed read tool"
```

---

### Task 2: Update the Zed harness tool mapping and explorer wording

**Files:**

- Modify: `superpowers/skills/using-superpowers/references/zed-tools.md`
- Modify: `harnesses/zed/skills/zed-explorer/SKILL.md`

**Interfaces:**

- Consumes: the `read` tool names from Task 1.
- Produces: skills reflect that file reads go through the sandboxed `read` tool.

- [ ] **Step 1: Update `zed-tools.md`**

Change the table row:

```markdown
| Read a file | `read_file` |
```

to:

```markdown
| Read a file | `read` |
```

(Leave the top prose line and other rows unchanged.)

- [ ] **Step 2: Update `zed-explorer/SKILL.md`**

Change:

```markdown
read with `read_file`; list with `list_directory`; run shell commands with
```

to:

```markdown
read with the `read` tool; list with `list_directory`; run shell commands with
```

- [ ] **Step 3: Verify no other Zed-relevant `read_file` references remain**

Run: `grep -rn "read_file" harnesses/ superpowers/skills/ | grep -v /\.git/ | grep -vi "opencode\|codex\|antigravity\|pi-tools\|gemini\|hermes"`
Expected: no matches in Zed-relevant skills (OpenCode/superpowers shared text may keep `read` without `_file`).

- [ ] **Step 4: Commit**

```bash
git add superpowers/skills/using-superpowers/references/zed-tools.md harnesses/zed/skills/zed-explorer/SKILL.md
git commit -m "docs(zed): map Read a file to the sandboxed read tool"
```

---

### Task 3: Normalize the `tkt:` tool-name prefix in the roadmap

**Files:**

- Modify: `docs/zed-agent-roadmap.md`

**Interfaces:**

- Produces: roadmap uses bare tool names (`bash`, `read`) per the naming decision.

- [ ] **Step 1: Replace the four `tkt:bash` references with `bash`**

- Line 43: `maps `terminal`->`tkt:bash`` → `maps `terminal` -> `bash``
- Line 47: `only gaps are `terminal`->`tkt:bash`in`zed-tools.md`→`only gaps are `terminal` -> `bash` in `zed-tools.md``
- Line 106: `Update `superpowers/skills/using-superpowers/references/zed-tools.md`: `terminal`->\n     `tkt:bash`;` → `: `terminal`->`bash`;`
- Line 107: `already maps `terminal`->`tkt:bash`.` → `already maps `terminal`->`bash`.`

Optionally mark batch 1 (the `Read` item in section 6 R2) as in progress — this is
cosmetic and may be skipped; the plan's substantive change is the naming removal.

- [ ] **Step 2: Verify**

Run: `grep -rn "tkt:\`" docs/zed-agent-roadmap.md`Expected: no remaining`tkt:`-prefixed tool references.

- [ ] **Step 3: Commit**

```bash
git add docs/zed-agent-roadmap.md
git commit -m "docs(roadmap): drop tkt: tool-name prefix"
```

---

### Task 4: Deliver the machine-side profile + override snippet (no repo edit)

**Files:**

- None modified in the repo. The output is a chat deliverable (the Gate-2 summary).

**Interfaces:**

- None. This task produces the paste-ready snippet the human applies on their
  machine (Zed profile + system-prompt override), since those files are not in
  this repo.

- [ ] **Step 1: Confirm the snippet (in the Gate-2 summary)**

The implementing agent must include, in the final Gate-2 chat summary, a
paste-ready snippet like the following for the human to apply on their machine:

```text
# Zed agent profile (agent.profiles):
# Disable the native `read_file` tool (mirror how `terminal` is disabled),
# and keep `read` available. The MCP `read` is provided by tkt.

# System-prompt override (add/adjust these lines):
# Skill instructions reference `read_file`; run those reads with the
# sandboxed `read` tool instead.
```

The implementer must adapt the exact profile syntax to the human's existing
profile (which already maps `terminal` -> `bash` and disables `terminal`); the
above is a guide, not a literal drop-in, and the human applies it by hand.

- [ ] **Step 2: No commit** (nothing to commit — this is a chat deliverable).

---

## Self-Review

**Spec coverage:**

- Sandboxed `read` tool in `mcp_server.py` → Task 1.
- `ReadResult` + `build_read_command` + `read_tool` → Task 1.
- Base64 transport / binary / truncation handling → Task 1 (tested).
- `zed-tools.md` + `zed-explorer` wording → Task 2.
- Drop `tkt:` name prefix (incl. roadmap normalization) → Task 3.
- Machine-side profile/override template → Task 4.

**Placeholders:** All steps carry concrete code/commands; no "implement later".

**Type consistency:** `read_tool(warm, *, file_path, offset=0, limit=2000) -> ReadResult`
is defined in Task 1 Step 3 and used consistently; `ReadResult` has `content` and
`truncated` fields; `build_read_command(path, offset, limit) -> str` and
`_parse_read_total(stderr) -> int | None` match their uses.
