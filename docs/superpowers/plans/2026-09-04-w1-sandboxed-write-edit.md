# W1 Sandboxed MCP Write/Edit Tools — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add sandboxed `Write` and `Edit` MCP tools to the tkt MCP server, replacing the native unsandboxed Zed `write_file`/`edit_file`, with markdown confirmations/diffs for review.

**Architecture:** The write/edit logic lives in a new `tkt/mcp_files.py` module invoked _inside_ the sandbox via `python -m tkt.mcp_files <op> ...` (content rides as base64 argv, decoded by the module). Each call runs through the existing warm holder (`WarmSandbox.run`), so writes are confined by the sandbox mount model and share the tracked cwd. The MCP tools `Write`/`Edit` in `mcp_server.py` build the `python -m` command and return the module's markdown output. The Zed harness mapping is updated and the native `write_file`/`edit_file` are disabled (human-applied).

**Tech Stack:** Python 3.13, `click`, `mcp` (FastMCP), `pydantic`, `difflib` (stdlib). Tests with `pytest`.

**Spec:** `docs/superpowers/specs/2026-09-04-w1-sandboxed-write-edit-design.md`

## Global Constraints

- Line length 110, doc length 79, numpy docstrings — enforce via `ruff check .`, `ruff format --check .` (see `pyproject.toml`).
- mypy clean: `mypy tkt/`.
- All `.py` files (including tests) carry the BSD-3 license header (copy from an existing file).
- BSD-3 license; no packaging config; `tkt` is not distributed via pip.
- `__all__` must be sorted (ruff `RUF022`).
- OpenCode workflow must not be broken.
- Agent profile (disabling native `write_file`/`edit_file`) is **human-applied** and NOT edited in this repo.
- `python -m tkt.mcp_files` must be runnable inside the sandbox (needs `python` + `tkt` importable there — inherited host env; see spec Open items).

---

### Task 1: Write-side of `tkt/mcp_files.py`

**Files:**

- Create: `tkt/mcp_files.py`
- Test: `tests/test_mcp_files.py`

**Interfaces:**

- Produces: `MAX_CONTENT_BYTES` (int), `DIFF_LINE_BUDGET` (int), `DIFF_CHAR_CAP` (int), `MCPFilesError(Exception)`, and `write_op(target: str, content: bytes) -> str` — used by Task 2 (`edit_op`), Task 3 (`main`), and Task 4 (host size guard imports `MAX_CONTENT_BYTES`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp_files.py` (with the BSD-3 license header from `tests/test_mcp_server.py`) containing:

```python
from __future__ import annotations

import pytest

from tkt.mcp_files import MCPFilesError, write_op


def test_write_creates_file_with_parents(tmp_path):
    target = tmp_path / "a" / "b" / "f.txt"
    msg = write_op(str(target), b"hello\n")
    assert msg == f"Wrote {target.resolve()}"
    assert target.read_bytes() == b"hello\n"


def test_write_overwrites_existing(tmp_path):
    target = tmp_path / "f.txt"
    target.write_bytes(b"old")
    write_op(str(target), b"new")
    assert target.read_bytes() == b"new"


def test_write_preserves_arbitrary_bytes(tmp_path):
    target = tmp_path / "bin"
    blob = b"\x00\xff\x10\x00\n"
    write_op(str(target), blob)
    assert target.read_bytes() == blob


def test_write_resolves_relative_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    msg = write_op("rel.txt", b"x")
    assert msg == f"Wrote {tmp_path / 'rel.txt'}"
    assert (tmp_path / "rel.txt").read_bytes() == b"x"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mcp_files.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tkt.mcp_files'`

- [ ] **Step 3: Write the module (write side)**

Create `tkt/mcp_files.py` (license header copied from `tkt/mcp_server.py`) with:

```python
from __future__ import annotations

import difflib
import os

__all__ = (
    "DIFF_CHAR_CAP",
    "DIFF_LINE_BUDGET",
    "MCPFilesError",
    "MAX_CONTENT_BYTES",
    "write_op",
)

# Number of unified-diff lines beyond which an `Edit` shows a one-line stats
# confirmation instead of the diff itself.
DIFF_LINE_BUDGET = 100

# Model-facing char cap for a shown `Edit` diff (head+tail truncation).
DIFF_CHAR_CAP = 25_000

# Raw content byte budget for content riding in the `bash -c` argv. Content is
# base64-encoded into a single argv element, so the binding limit is Linux's
# per-argument cap (MAX_ARG_STRLEN, 128 KiB) minus base64's 4/3 inflation and
# command/path overhead. 90_000 leaves clear margin (95 KiB is the empirical
# edge).
MAX_CONTENT_BYTES = 90_000


class MCPFilesError(Exception):
    """A graceful, user-facing failure in a sandbox file operation."""


def _cap_text(text: str, max_chars: int) -> str:
    """Keep head+tail of ``text`` within ``max_chars``, with a dropped marker."""
    if len(text) <= max_chars:
        return text
    n_head = max_chars // 2
    n_tail = max_chars - n_head
    dropped = len(text) - max_chars
    return text[:n_head] + f"\n... [{dropped} chars truncated] ...\n" + text[-n_tail:]


def _compute_diff(before: str, after: str, path: str) -> tuple[list[str], int, int]:
    """Return the unified diff of before->after plus added/removed counts."""
    diff = list(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=path,
            tofile=path,
        )
    )
    added = sum(
        1 for line in diff if line.startswith("+") and not line.startswith("+++")
    )
    removed = sum(
        1 for line in diff if line.startswith("-") and not line.startswith("---")
    )
    return diff, added, removed


def write_op(target: str, content: bytes) -> str:
    """Create or overwrite ``target`` (resolved against cwd) with ``content``.

    Auto-creates missing parent directories. Returns the markdown confirmation
    for the tool card; raises :class:`MCPFilesError` on failure.
    """
    path = os.path.abspath(target)
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)
    except OSError as e:
        raise MCPFilesError(f"Write failed: {e}") from e
    return f"Wrote {path}"
```

(The module also contains `edit_op`, `_dispatch`, and `main`, added in Tasks 2–3; the file compiles with just `write_op` here.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mcp_files.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Lint**

Run: `ruff check tkt/mcp_files.py tests/test_mcp_files.py && ruff format --check tkt/mcp_files.py tests/test_mcp_files.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add tkt/mcp_files.py tests/test_mcp_files.py
git commit -m "feat(mcp): add write_op to sandbox file module"
```

---

### Task 2: Edit-side of `tkt/mcp_files.py`

**Files:**

- Modify: `tkt/mcp_files.py`
- Test: `tests/test_mcp_files.py`

**Interfaces:**

- Consumes: `write_op`'s helpers `MCPFilesError`, `_compute_diff`, `_cap_text`, `DIFF_LINE_BUDGET`, `DIFF_CHAR_CAP` (from Task 1).
- Produces: `edit_op(target: str, old: str, new: str, replace_all: bool = False) -> str` — used by Task 3 (`main`).

- [ ] **Step 1: Write the failing tests**

Append the following tests to `tests/test_mcp_files.py`, and add `edit_op` to
its top import block (change `from tkt.mcp_files import MCPFilesError, write_op`
to `from tkt.mcp_files import MCPFilesError, edit_op, write_op`):

````python
def test_edit_replaces_first_occurrence(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("aaa bbb")
    msg = edit_op(str(target), "aaa", "XXX")
    assert "Edited" in msg
    assert target.read_text() == "XXX bbb"


def test_edit_replace_all_replaces_every_occurrence(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("aaa aaa aaa")
    msg = edit_op(str(target), "aaa", "X", replace_all=True)
    assert "Edited" in msg
    assert target.read_text() == "X X X"


def test_edit_pattern_not_found(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("abc")
    with pytest.raises(MCPFilesError, match="pattern not found"):
        edit_op(str(target), "zzz", "y")


def test_edit_multiple_matches_requires_replace_all(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("x x x")
    with pytest.raises(MCPFilesError, match="matches 3 times"):
        edit_op(str(target), "x", "y")


def test_edit_non_utf8_rejected(tmp_path):
    target = tmp_path / "f.bin"
    target.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(MCPFilesError, match="not valid UTF-8"):
        edit_op(str(target), "x", "y")


def test_edit_small_diff_shown(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("one\ntwo\n")
    msg = edit_op(str(target), "two", "TWO")
    assert "```diff" in msg


def test_edit_large_diff_uses_stats(tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("x\n" * 200)
    msg = edit_op(str(target), "x", "y", replace_all=True)
    assert "```diff" not in msg
    assert "200 replacements" in msg
    assert "+200/-200" in msg
````

(Add `edit_op` to the existing `from tkt.mcp_files import ...` line, or keep a second import — either is fine.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mcp_files.py -q`
Expected: FAIL with `ImportError` / `NameError` (`edit_op` undefined)

- [ ] **Step 3: Write the implementation**

Append `edit_op` to `tkt/mcp_files.py`, and add `"edit_op"` to `__all__`
(keep it sorted for ruff RUF022):

````python
def edit_op(target: str, old: str, new: str, replace_all: bool = False) -> str:
    """Replace ``old`` with ``new`` in ``target`` and return a markdown summary.

    Returns the per-call snapshot diff when it fits the line budget, else a
    one-line stats confirmation. Raises :class:`MCPFilesError` on failure.
    """
    path = os.path.abspath(target)
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise MCPFilesError(f"Edit failed: {e}") from e
    try:
        before = data.decode("utf-8")
    except UnicodeDecodeError:
        raise MCPFilesError(
            f"Edit failed: file is not valid UTF-8 text at {path}"
        ) from None
    count = before.count(old)
    if count == 0:
        raise MCPFilesError(f"Edit failed: pattern not found at {path}")
    if count > 1 and not replace_all:
        raise MCPFilesError(
            f"Edit failed: pattern matches {count} times at {path} (use replace_all=True)"
        )
    after = before.replace(old, new) if replace_all else before.replace(old, new, 1)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(after)
    except OSError as e:
        raise MCPFilesError(f"Edit failed: {e}") from e
    replacements = count if replace_all else 1
    diff, added, removed = _compute_diff(before, after, path)
    if len(diff) <= DIFF_LINE_BUDGET:
        body = "".join(diff).rstrip("\n")
        return f"Edited {path}:\n```diff\n{_cap_text(body, DIFF_CHAR_CAP)}\n```"
    return f"Edited {path}: {replacements} replacements, +{added}/-{removed} lines"
````

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mcp_files.py -q`
Expected: PASS (all tests, Tasks 1+2)

- [ ] **Step 5: Lint + mypy**

Run: `ruff check tkt/mcp_files.py tests/test_mcp_files.py && ruff format --check tkt/mcp_files.py tests/test_mcp_files.py && mypy tkt/`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add tkt/mcp_files.py tests/test_mcp_files.py
git commit -m "feat(mcp): add edit_op to sandbox file module"
```

---

### Task 3: `main()` entry point and dispatch

**Files:**

- Modify: `tkt/mcp_files.py`
- Test: `tests/test_mcp_files.py`

**Interfaces:**

- Consumes: `write_op`, `edit_op`, `MCPFilesError`.
- Produces: `main(argv: Sequence[str] | None = None) -> int` — the in-sandbox entry point invoked as `python -m tkt.mcp_files <op> ...`. Task 4 relies on its CLI contract (`write <target> <content_b64>` and `edit <target> <old_b64> <new_b64> <replace_all>`), printing the markdown message to stdout and exiting 0 on success / 1 on failure.

- [ ] **Step 1: Write the failing tests**

Append the following tests to `tests/test_mcp_files.py`, and extend its top
import block: add `import base64` to the stdlib imports and `main` to the
`from tkt.mcp_files import ...` line (run `ruff check --fix` afterward to sort
imports):

```python
def test_main_write_success(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc = main(["write", "out.txt", base64.b64encode(b"hi").decode("ascii")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Wrote" in out
    assert (tmp_path / "out.txt").read_bytes() == b"hi"


def test_main_edit_error_returns_1(capsys, tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("abc")
    rc = main(
        [
            "edit",
            str(target),
            base64.b64encode(b"zzz").decode("ascii"),
            base64.b64encode(b"y").decode("ascii"),
            "0",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "pattern not found" in out


def test_main_unknown_op_returns_1(capsys):
    assert main(["nope"]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mcp_files.py -q`
Expected: FAIL with `NameError: name 'main' is not defined`

- [ ] **Step 3: Write the implementation**

Append to `tkt/mcp_files.py`. First extend the top import block with the
stdlib imports this entry point uses (keep them sorted; ruff will flag if not):

```python
import base64
import sys
from typing import Sequence
```

Also add `"main"` to `__all__` (keep it sorted for ruff RUF022). Then append the
dispatch and entry point:

```python
def _dispatch(argv: Sequence[str]) -> str:
    if not argv:
        raise MCPFilesError("mcp_files: missing operation (write|edit)")
    op = argv[0]
    if op == "write":
        if len(argv) < 3:
            raise MCPFilesError("mcp_files: write requires <target> <content_b64>")
        return write_op(argv[1], base64.b64decode(argv[2]))
    if op == "edit":
        if len(argv) < 5:
            raise MCPFilesError(
                "mcp_files: edit requires <target> <old_b64> <new_b64> <replace_all>"
            )
        old = base64.b64decode(argv[2]).decode("utf-8")
        new = base64.b64decode(argv[3]).decode("utf-8")
        return edit_op(argv[1], old, new, argv[4] == "1")
    raise MCPFilesError(f"mcp_files: unknown operation {op!r}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run one sandbox-side file operation; print the markdown result.

    Exits 0 on success and 1 on a graceful failure. Unexpected errors are
    caught and reported gracefully rather than as a traceback, so the MCP
    surface never shows one.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        message = _dispatch(args)
    except MCPFilesError as e:
        print(e)
        return 1
    except Exception as e:  # pragma: no cover - defensive; see _dispatch
        print(f"mcp_files failed: {e}")
        return 1
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mcp_files.py -q`
Expected: PASS (all tests)

- [ ] **Step 5: Lint + mypy**

Run: `ruff check tkt/mcp_files.py tests/test_mcp_files.py && ruff format --check tkt/mcp_files.py tests/test_mcp_files.py && mypy tkt/`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add tkt/mcp_files.py tests/test_mcp_files.py
git commit -m "feat(mcp): add main entry point to sandbox file module"
```

---

### Task 4: Host-side MCP tools `Write`/`Edit` in `mcp_server.py`

**Files:**

- Modify: `tkt/mcp_server.py`
- Test: `tests/test_mcp_server.py`

**Interfaces:**

- Consumes: `WarmSandbox` (existing), `MAX_CONTENT_BYTES` from `tkt.mcp_files`.
- Produces: `build_write_command(file_path, content) -> str`, `build_edit_command(file_path, old_string, new_string, replace_all) -> str`, `write_tool(warm, *, file_path, content) -> str`, `edit_tool(warm, *, file_path, old_string, new_string, replace_all=False) -> str`, and the `Write`/`Edit` MCP tool registrations inside `run_server`. Return type is a plain `str` (markdown), not a wrapper model.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mcp_server.py` (after the existing `grep_tool` tests; update the `from tkt.mcp_server import (...)` block at the top to add `build_write_command`, `build_edit_command`, `write_tool`, `edit_tool`, and add `from tkt.mcp_files import MAX_CONTENT_BYTES`):

```python
def test_build_write_command_quotes_path_and_b64_content():
    cmd = build_write_command("/a b/f.py", "x y\n")
    assert "python -m tkt.mcp_files write" in cmd
    assert "'/a b/f.py'" in cmd
    assert base64.b64encode(b"x y\n").decode("ascii") in cmd


def test_build_edit_command_flags_replace_all():
    cmd = build_edit_command("f.py", "old", "new", True)
    assert "python -m tkt.mcp_files edit" in cmd
    assert base64.b64encode(b"old").decode("ascii") in cmd
    assert base64.b64encode(b"new").decode("ascii") in cmd
    assert cmd.endswith(" 1")


def test_write_tool_success():
    warm = mock.Mock()
    warm.run.return_value = BashResult(stdout="Wrote /x\n", stderr="", exit_code=0)
    res = write_tool(warm, file_path="f.py", content="hi")
    assert res == "Wrote /x"


def test_write_tool_surfaces_module_error():
    warm = mock.Mock()
    warm.run.return_value = BashResult(stdout="Write failed: boom\n", stderr="", exit_code=1)
    res = write_tool(warm, file_path="f.py", content="hi")
    assert "Write failed" in res


def test_write_tool_stderr_fallback_on_empty_stdout():
    warm = mock.Mock()
    warm.run.return_value = BashResult(stdout="", stderr="boom", exit_code=1)
    res = write_tool(warm, file_path="f.py", content="hi")
    assert "boom" in res


def test_write_tool_too_large_does_not_run():
    warm = mock.Mock()
    res = write_tool(warm, file_path="f.py", content="x" * (MAX_CONTENT_BYTES + 1))
    assert "content too large" in res
    warm.run.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_mcp_server.py -q`
Expected: FAIL with `ImportError` / `NameError` (new names undefined)

- [ ] **Step 3: Write the implementation**

In `tkt/mcp_server.py`:

1. Add to the imports:

```python
from .mcp_files import MAX_CONTENT_BYTES
```

2. Add `"build_edit_command"`, `"build_write_command"`, `"edit_tool"`, `"write_tool"` to `__all__` (keep it sorted; place them in the correct alphabetical positions).

3. Add these module-level helpers after `grep_tool`:

```python
def build_write_command(file_path: str, content: str) -> str:
    """Build the sandbox command that writes ``content`` to ``file_path``.

    ``content`` rides as base64 (shell-safe, arbitrary bytes) and is decoded by
    ``tkt.mcp_files`` inside the sandbox; ``file_path`` is embedded via
    ``shlex.quote`` and resolved against the tracked cwd by the module.
    """
    content_b64 = base64.b64encode(content.encode()).decode("ascii")
    return f"python -m tkt.mcp_files write {shlex.quote(file_path)} {content_b64}"


def build_edit_command(file_path: str, old_string: str, new_string: str, replace_all: bool) -> str:
    """Build the sandbox command that edits ``file_path``.

    ``old_string``/``new_string`` ride as base64; ``replace_all`` is ``1``/``0``.
    """
    old_b64 = base64.b64encode(old_string.encode()).decode("ascii")
    new_b64 = base64.b64encode(new_string.encode()).decode("ascii")
    flag = "1" if replace_all else "0"
    return f"python -m tkt.mcp_files edit {shlex.quote(file_path)} {old_b64} {new_b64} {flag}"


def _run_files_op(warm: WarmSandbox, *, command: str) -> str:
    """Run a ``python -m tkt.mcp_files`` command and return its message.

    The module formats both success and failure as markdown on stdout; exit 0
    means success, nonzero a graceful error (or, on a crash, the stderr tail).
    """
    result = warm.run(command)
    body = result.stdout.strip()
    if result.exit_code != 0 and not body:
        body = result.stderr.strip()
    if result.exit_code != 0 and not body:
        body = f"mcp_files exited {result.exit_code}"
    return body


def write_tool(warm: WarmSandbox, *, file_path: str, content: str) -> str:
    """Run one sandboxed ``Write`` against ``warm`` and return markdown."""
    nbytes = len(content.encode("utf-8"))
    if nbytes > MAX_CONTENT_BYTES:
        return f"Write failed: content too large ({nbytes} bytes, max {MAX_CONTENT_BYTES})"
    return _run_files_op(warm, command=build_write_command(file_path, content))


def edit_tool(
    warm: WarmSandbox,
    *,
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """Run one sandboxed ``Edit`` against ``warm`` and return markdown."""
    for name, value in (("old_string", old_string), ("new_string", new_string)):
        nbytes = len(value.encode("utf-8"))
        if nbytes > MAX_CONTENT_BYTES:
            return f"Edit failed: {name} too large ({nbytes} bytes, max {MAX_CONTENT_BYTES})"
    return _run_files_op(warm, command=build_edit_command(file_path, old_string, new_string, replace_all))
```

4. Inside `run_server`, after the `grep` tool and before `todo_store = TodoStore()`, register the two tools:

```python
    @mcp.tool()
    def Write(
        file_path: str,
        content: str,
        description: str | None = None,  # present for human approvals of tool actions
    ) -> str:
        """Create or overwrite a file inside the tkt sandbox.

        Writes are confined by the sandbox mount model (``.agent/**`` in a
        workspace, the whole repo in single-repo mode). Missing parent
        directories are created; ``content`` may contain arbitrary bytes.
        Returns a path-only confirmation (clickable). ``description`` is a
        per-call rationale for the human; it does not change behavior.

        Args:
            file_path: Path to create or overwrite (absolute, or relative to
                the sandbox cwd).
            content: The file content to write.
            description: Optional human-readable rationale for this call.
        """
        return write_tool(warm, file_path=file_path, content=content)

    @mcp.tool()
    def Edit(
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        description: str | None = None,  # present for human approvals of tool actions
    ) -> str:
        """Edit a file inside the tkt sandbox.

        Replaces ``old_string`` with ``new_string`` (once, or every occurrence
        when ``replace_all`` is true) and returns a per-call snapshot diff, or a
        stats confirmation when the diff exceeds the line budget. Confined by
        the sandbox mount model. ``description`` is a per-call rationale for
        the human; it does not change behavior.

        Args:
            file_path: Path to edit (absolute, or relative to the sandbox cwd).
            old_string: The exact text to find.
            new_string: The replacement text.
            replace_all: Replace every occurrence instead of just the first.
            description: Optional human-readable rationale for this call.
        """
        return edit_tool(
            warm,
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_mcp_server.py -q`
Expected: PASS (new tests + existing)

- [ ] **Step 5: Lint + mypy**

Run: `ruff check tkt/ tests/test_mcp_server.py && ruff format --check tkt/ tests/test_mcp_server.py && mypy tkt/`
Expected: clean

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add tkt/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): add sandboxed Write and Edit MCP tools"
```

---

### Task 5: Update `zed-tools.md` mapping

**Files:**

- Modify: `superpowers/skills/using-superpowers/references/zed-tools.md` (inside the `superpowers` submodule)

**Interfaces:** None (documentation only).

- [ ] **Step 1: Make the edits**

In `superpowers/skills/using-superpowers/references/zed-tools.md`, change the two rows:

```markdown
| Create a file | `write_file` |
| Edit a file | `edit_file` |
```

to:

```markdown
| Create a file | `Write` |
| Edit a file | `Edit` |
```

- [ ] **Step 2: Commit inside the submodule, then bump the pointer**

```bash
git -C superpowers add skills/using-superpowers/references/zed-tools.md
git -C superpowers commit -m "Point create/edit tool mapping at sandboxed MCP Write/Edit"
git add superpowers
git commit -m "Update superpowers submodule for Zed tool mapping (Write/Edit)"
```

- [ ] **Step 3: Verify the pointer**

Run: `git -C superpowers log --oneline -1 && git submodule status`
Expected: submodule pointer advanced to the new commit.

---

### Task 6: Update roadmap and README

**Files:**

- Modify: `docs/zed-agent-roadmap.md`
- Modify: `README.md`

**Interfaces:** None (documentation only).

- [ ] **Step 1: Update the roadmap tool-surface table**

In `docs/zed-agent-roadmap.md` section 1, change:

```markdown
| `write_file` / `edit_file` | **tkt MCP (sandboxed)** | W1 — moving into the sandbox |
```

to:

```markdown
| `Write` / `Edit` | tkt MCP (sandboxed) | sandboxed create/edit |
```

- [ ] **Step 2: Remove the W1 workstream block**

In section 2, delete the entire `### W1 — Sandboxed MCP write/edit (project-level cwd)` subsection (its bullets are now implemented).

- [ ] **Step 3: Update the W2 reference**

In the W2 block, change "and the new read/edit" to "and the new `Write`/`Edit`" for accuracy.

- [ ] **Step 4: Update README tool list**

In `README.md`, change:

```markdown
**Other** — `mcp-server` runs the MCP stdio server that exposes the sandboxed
`bash`, `read`, `grep`, `glob`, and `ls` tools, and `fix-openspec` rewrites
OpenSpec skill files for OpenCode's harness.
```

to:

```markdown
**Other** — `mcp-server` runs the MCP stdio server that exposes the sandboxed
`bash`, `read`, `grep`, `glob`, `ls`, `Write`, and `Edit` tools, and
`fix-openspec` rewrites OpenSpec skill files for OpenCode's harness.
```

- [ ] **Step 5: Verify formatting**

Run: `ruff check docs/zed-agent-roadmap.md README.md 2>/dev/null || true`
Expected: no action needed (markdown not linted); just confirm the files read cleanly.

- [ ] **Step 6: Commit**

```bash
git add docs/zed-agent-roadmap.md README.md
git commit -m "docs: mark W1 done, update tool surface and README"
```

---

## Open items / human-applied after implementation

- **Agent profile:** disable native `write_file`/`edit_file` in the Zed agent profile and enable MCP `Write`/`Edit`. Paste-ready snippet for the human:

```
# Zed settings agent.profiles (the profile used by the sp-* agents)
"Write": { "disabled": false },   # via MCP (tkt)
"Edit":  { "disabled": false },
"write_file": { "disabled": true },
"edit_file":  { "disabled": true },
```

- **Live-testing pass:** revisit the 100-line diff budget and the 25 000-char cap; confirm `python -m tkt.mcp_files` runs inside the real sandbox (needs `python` + `tkt` importable there).
