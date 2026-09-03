# `read` Tool Byte-Level Output Cap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a byte-level output cap to the sandboxed `read` MCP tool — bounding per-line memory in the sandbox command (fixing a `sed` OOM on huge single-line files) and bounding what the model receives — with `read`'s cap scaling with the requested line count up to a 25 000-char ceiling, while `bash` keeps its existing 5 000 default.

**Architecture:** Three layers mirroring the existing `bash` truncation fix. (1) The sandbox command (`build_read_command`) pipes the file through `fold -b -w CAP` (bounds per-line memory), `sed` (the `offset`/`limit` slice), `head -c CAP` (bounds output bytes so the command exits cleanly), then `base64 -w0`. (2) The driver's `_DRIVER_OUT_CAP = 50_000` wire cap stays as a last-resort backstop. (3) `read_tool` applies the existing head+tail `truncate_output` helper with a per-call cap `min(_MAX_OUTPUT_CHARS, limit * _CHARS_PER_LINE)` and sets `truncated` when cut.

**Tech Stack:** Python 3.13, pydantic, Click's `FastMCP` server, GNU coreutils (`fold`, `sed`, `head`, `base64`, `wc`) inside a `bwrap` sandbox; pytest.

**Spec:** `docs/superpowers/specs/2026-09-02-read-tool-byte-cap-design.md`

## Global Constraints

- `_BASH_OUTPUT_CHARS = 5_000` — model-facing cap for the `bash` tool, unchanged default (behavior identical to today).
- `_MAX_OUTPUT_CHARS = 25_000` — shared hard cap / ceiling; `read`'s per-call cap scales up to this.
- `_DRIVER_OUT_CAP = 50_000` — unchanged; driver-side wire cap per stream.
- `_READ_BYTE_CAP = 32_000` — sandbox-side `fold` width and `head -c` bound (raw bytes); chosen so `base64(32_000) ≈ 42.7 KB < _DRIVER_OUT_CAP`.
- `_CHARS_PER_LINE = 110` — `read`'s per-line scale factor (this repo's ruff line-length).
- `read`'s model cap is `read_char_cap(limit) = min(_MAX_OUTPUT_CHARS, limit * _CHARS_PER_LINE)`.
- `bash` keeps using `_BASH_OUTPUT_CHARS` (5 000); its behavior does not change.
- Reuse the existing `truncate_output(text, max_chars) -> (text, truncated)` helper; do not add new truncation logic.
- No new MCP tool parameters; `read(file_path, offset=0, limit=2000)` signature is unchanged. `ReadResult` fields are unchanged.
- All `.py` files keep the BSD license header (already present — do not remove).
- Preserve the existing `build_read_command` shell framing (`f=`, the `[ ! -f ]` guard, `READ_TOTAL` on stderr, trailing `printf "\n"`).
- Run `ruff check .`, `ruff format --check .`, `mypy tkt/` and `python -m pytest` before finishing.

---

### Task 1: Add `read_char_cap` and split the output-cap constants

**Files:**

- Modify: `tkt/mcp_server.py:52-57` (module constants)
- Modify: `tkt/mcp_server.py` (add `read_char_cap` helper near `truncate_output`, after `decode_field`)
- Test: `tests/test_mcp_server.py`

**Interfaces:**

- Consumes: nothing new (existing `truncate_output`).
- Produces: module constants `_BASH_OUTPUT_CHARS = 5_000`, `_MAX_OUTPUT_CHARS = 25_000`, `_READ_BYTE_CAP = 32_000`, `_CHARS_PER_LINE = 110`, and function `read_char_cap(limit: int) -> int`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_mcp_server.py` (import `read_char_cap` and `_MAX_OUTPUT_CHARS` from `tkt.mcp_server` at the top with the other imports):

```python
def test_read_char_cap_scales_with_limit():
    """Cap grows with requested lines, hard-capped at _MAX_OUTPUT_CHARS."""
    assert read_char_cap(1) == 110
    assert read_char_cap(100) == 11000
    assert read_char_cap(2000) == 25000
    assert read_char_cap(10**6) == 25000
    assert read_char_cap(2000) == _MAX_OUTPUT_CHARS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mcp_server.py::test_read_char_cap_scales_with_limit -v`
Expected: FAIL with `ImportError` / `NameError` (function not defined).

- [ ] **Step 3: Write the constants and helper**

In `tkt/mcp_server.py`, split the model-cap constant into a `bash` default and a
shared ceiling, and add the two new constants next to them (lines 52-57):

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

Then update the `bash` MCP tool (line 434) so it keeps using the 5 000 default
(instead of `_MAX_OUTPUT_CHARS`):

```python
        return _cap_result(warm.run(command, timeout_ms=timeout_ms), _BASH_OUTPUT_CHARS)
```

Also update the docstrings that referenced `_MAX_OUTPUT_CHARS` as the `bash`
cap so they point at `_BASH_OUTPUT_CHARS`: the `BashResult` class docstring
(lines ~64 and ~67) and the `bash` MCP tool docstring (line ~423). Each
`_MAX_OUTPUT_CHARS` there becomes `_BASH_OUTPUT_CHARS`.

Add the helper right after `decode_field` (before `truncate_output`):

```python
def read_char_cap(limit: int) -> int:
    """Model-facing char cap for a ``read`` of ``limit`` lines.

    Scales with the requested line count (about one ruff-formatted line per
    ``_CHARS_PER_LINE`` chars) and is hard-capped at ``_MAX_OUTPUT_CHARS``.
    """
    return min(_MAX_OUTPUT_CHARS, limit * _CHARS_PER_LINE)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mcp_server.py::test_read_char_cap_scales_with_limit -v`
Expected: PASS.

- [ ] **Step 5: Run the existing test suite (confirms the constant split didn't break `bash` caps)**

Run: `python -m pytest tests/test_mcp_server.py -q`
Expected: PASS (existing `truncate_output`/`_cap_result` tests pass explicit caps like `5000`, and the `bash` tool still truncates at `_BASH_OUTPUT_CHARS = 5000`).

- [ ] **Step 6: Commit**

```bash
git add tkt/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): add read_char_cap and split output caps (bash 5k, ceiling 25k)"
```

---

### Task 2: Bound line length and output bytes in `build_read_command`

**Files:**

- Modify: `tkt/mcp_server.py:155-177` (`build_read_command`)
- Test: `tests/test_mcp_server.py`

**Interfaces:**

- Consumes: `_READ_BYTE_CAP` (from Task 1).
- Produces: updated `build_read_command(path: str, offset: int, limit: int) -> str` that pipes through `fold -b -w CAP` and `head -c CAP`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_mcp_server.py`:

```python
def test_build_read_command_bounds_line_length_and_output():
    """fold bounds per-line memory and head -c bounds output before base64."""
    from tkt.mcp_server import _READ_BYTE_CAP

    cmd = build_read_command("/a b/c.txt", offset=0, limit=2000)
    assert f'fold -b -w "{_READ_BYTE_CAP}"' in cmd
    assert f'head -c "{_READ_BYTE_CAP}"' in cmd
    assert '| base64 -w0' in cmd
    # The fold must appear before sed in the pipeline.
    assert cmd.index("fold -b -w") < cmd.index("sed -n")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mcp_server.py::test_build_read_command_bounds_line_length_and_output -v`
Expected: FAIL (no `fold`/`head -c` in the command).

- [ ] **Step 3: Write the implementation**

Change the pipeline line in `build_read_command` from:

```python
        f'sed -n "{start},{end}p" "$f" | base64 -w0\n'
```

to:

```python
        f'fold -b -w "{_READ_BYTE_CAP}" "$f" | '
        f'sed -n "{start},{end}p" | '
        f'head -c "{_READ_BYTE_CAP}" | base64 -w0\n'
```

Update the docstring's description of the emitted pipeline to mention `fold -b -w`
(bounds per-line memory) and `head -c` (bounds output bytes).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mcp_server.py::test_build_read_command_bounds_line_length_and_output tests/test_mcp_server.py::test_build_read_command_quotes_path_and_slices tests/test_mcp_server.py::test_build_read_command_respects_offset -v`
Expected: PASS (the existing quote/slice/offset assertions still hold).

- [ ] **Step 5: Commit**

```bash
git add tkt/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): bound read line length and output bytes in sandbox command"
```

---

### Task 3: Apply the byte cap in `read_tool`

**Files:**

- Modify: `tkt/mcp_server.py:186-217` (`read_tool`)
- Test: `tests/test_mcp_server.py`

**Interfaces:**

- Consumes: `read_char_cap` and `truncate_output` (from Task 1).
- Produces: `read_tool(warm, *, file_path, offset=0, limit=2000) -> ReadResult` where `ReadResult.truncated` is `line_truncated or byte_truncated`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_mcp_server.py`:

```python
def test_read_tool_byte_cap_truncates_long_single_line():
    """A single line longer than the per-call cap is head+tail truncated."""
    from tkt.mcp_server import read_tool

    sl = ("x" * 50000 + "\n").encode()
    warm = mock.Mock()
    warm.run.return_value = BashResult(
        stdout=base64.b64encode(sl).decode(), stderr="READ_TOTAL 1\n", exit_code=0
    )
    res = read_tool(warm, file_path="/tmp/x.txt", limit=100)  # cap = 11000
    assert res.truncated is True
    assert "... chars truncated ..." in res.content
    assert len(res.content) < 12000


def test_read_tool_byte_cap_not_truncated_when_within_budget():
    """A read within the char cap is not byte-truncated."""
    from tkt.mcp_server import read_tool

    sl = b"hello\n"
    warm = mock.Mock()
    warm.run.return_value = BashResult(
        stdout=base64.b64encode(sl).decode(), stderr="READ_TOTAL 1\n", exit_code=0
    )
    res = read_tool(warm, file_path="/tmp/x.txt", limit=1)
    assert res.truncated is False
    assert res.content == "1\thello"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mcp_server.py::test_read_tool_byte_cap_truncates_long_single_line -v`
Expected: FAIL (content is not truncated today; `truncated` is False since the single line fits `limit`).

- [ ] **Step 3: Write the implementation**

In `read_tool`, replace the tail of the function (from `truncated = more > 0` through the `return`) with:

```python
    line_truncated = more > 0
    if line_truncated and offset == 0:
        numbered += f"\n... ({more} more lines)"
    # Model-facing byte cap: truncate head+tail, flagging if cut.
    content, byte_truncated = truncate_output(numbered, read_char_cap(limit))
    return ReadResult(content=content, truncated=line_truncated or byte_truncated)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mcp_server.py::test_read_tool_byte_cap_truncates_long_single_line tests/test_mcp_server.py::test_read_tool_byte_cap_not_truncated_when_within_budget -v`
Expected: PASS.

- [ ] **Step 5: Run the full read-tool test set**

Run: `python -m pytest tests/test_mcp_server.py -q`
Expected: PASS (all existing read-tool and bash-cap tests still pass).

- [ ] **Step 6: Commit**

```bash
git add tkt/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): cap read tool output bytes via truncate_output"
```

---

### Task 4: Lint, type-check, and full test run

**Files:**

- None (verification only).

- [ ] **Step 1: Ruff check**

Run: `ruff check .`
Expected: no errors.

- [ ] **Step 2: Ruff format check**

Run: `ruff format --check .`
Expected: no diffs (if it reports a diff on a single-line docstring, shorten that docstring's single line or move the last word to the second line per AGENTS.md, then re-run).

- [ ] **Step 3: mypy**

Run: `mypy tkt/`
Expected: no errors.

- [ ] **Step 4: Full test suite**

Run: `python -m pytest`
Expected: PASS.

- [ ] **Step 5: Commit any format/lint fixes (if the prior steps produced diffs)**

```bash
git add -u
git commit -m "style: satisfy ruff/mypy after read byte-cap change"
```

(If Task 4's Steps 1-4 all pass with no edits, there is nothing to commit.)
