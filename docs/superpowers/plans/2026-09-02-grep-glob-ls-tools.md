# R2 Batch 2 — Sandboxed `grep`, `glob`, `ls` Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add sandboxed `grep`, `glob`, and `ls` MCP tools to the tkt MCP server and fold them into the Zed harness (skills mapping + `zed-explorer` wording), so the Zed native agent searches and lists through the sandbox rather than Zed's native, unsandboxed `grep`/`find_path`/`list_directory`. This is batch 2 of R2, following the `read` tool.

**Architecture:** Each tool builds a small command (`build_ls_command` / `build_glob_command` / `build_grep_command`) and runs it through the existing warm holder (`WarmSandbox.run`), the same channel `bash` and `read` use — that keeps it sandboxed because the MCP server process is host-side. Host Python maps the resulting `BashResult` to a distinct per-tool result type (`LSResult`/`GlobResult`/`GrepResult`, each `content: str` + `truncated: bool`), normalizes grep rc 1 to empty content, and caps with `truncate_output(..., _MAX_OUTPUT_CHARS)`. `$HOME` stays blocked, `~/.agents/skills` stays read-only; no new mounts. Native tools are left in place in the repo (disabling happens in the human's profile, delivered as a paste-ready snippet).

**Tech Stack:** Python 3.13, click, pydantic, pytest. No new third-party deps.

**Spec:** `docs/superpowers/specs/2026-09-02-grep-glob-ls-tools-design.md`

## Global Constraints

- Python 3.13; deps are `click`, `GitPython`, `pyyaml`, `json5` — no new third-party dependencies.
- Every `.py` file carries the BSD-3-Clause header exactly as in existing modules (preserve it; do not alter).
- Must pass before each commit and at the end: `ruff check .` and `ruff format --check .` and `mypy tkt/`.
- `tkt` is not pip-distributed; do not add packaging config.
- OpenCode workflow must keep working throughout (coexistence).
- `superpowers/skills/using-superpowers/references/zed-tools.md` lives inside the `superpowers` **submodule**. Editing it requires committing in the submodule and bumping the submodule pointer in the main repo (`git add superpowers`), per the `a2e1e84` convention. Do not touch other files in the submodule.
- Do **not** edit `harnesses/zed/rules.md` — the "Tool changes" note there is not motivated for this batch (the only system-prompt prose naming `grep`/`find_path` is gated on `available_tools 'grep'`; no prose names `list_directory`/`glob`; verified in the Zed source).
- Native `grep`/`find_path`/`list_directory` are **not** disabled in the repo; disabling them and the system-prompt override are machine-side (human-applied), delivered as a snippet in the final Gate-2 summary.

---

### Task 1: Add `ls`, `glob`, `grep` tools to the MCP server

**Files:**

- Modify: `tkt/mcp_server.py`
- Test: `tests/test_mcp_server.py`

**Interfaces:**

- Consumes: existing `WarmSandbox.run`, `BashResult`, `truncate_output`,
  `_MAX_OUTPUT_CHARS`, `shlex`, `base64`.
- Produces: `LSResult`, `GlobResult`, `GrepResult` models; `build_ls_command`,
  `build_glob_command`, `build_grep_command`; `ls_tool`, `glob_tool`,
  `grep_tool`; and the `ls`/`glob`/`grep` MCP tools registered in `run_server`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_mcp_server.py`, add `build_ls_command`, `build_glob_command`,
`build_grep_command`, `ls_tool`, `glob_tool`, `grep_tool`, and the three result
types to the existing `from tkt.mcp_server import (...)` block, then append:

```python
def test_build_ls_command_quotes_path_and_lists():
    """build_ls_command lists with -laF and quotes the path."""
    cmd = build_ls_command("/a b")
    assert "ls -laF --" in cmd
    assert "'/a b'" in cmd


def test_build_glob_command_globstar_nullglob_and_quotes():
    """build_glob_command sets globstar/nullglob and quotes path+pattern."""
    cmd = build_glob_command("**/*.py", "src")
    assert 'cd "src"' in cmd or "cd 'src'" in cmd
    assert "shopt -s globstar nullglob" in cmd
    assert "for f in $pattern" in cmd
    assert "[ -e \"$f\" ]" in cmd
    assert "**/*.py" in cmd
    # pattern is assigned to a quoted var (injection-safe), not inlined
    assert "'**/*.py'" in cmd


def test_build_grep_command_defaults_content():
    """build_grep_command content mode has -rEIH and --exclude-dir=.git."""
    cmd = build_grep_command("foo", "src")
    assert "grep -r -E -I -H" in cmd
    assert "--exclude-dir=.git" in cmd
    assert "-e foo" in cmd
    assert " -- src" in cmd


def test_build_grep_command_output_modes_and_flags():
    """output_mode/ignore_case/line_number map to grep flags; glob to --include."""
    cmd = build_grep_command("x", "src", glob="*.py", output_mode="files", ignore_case=True)
    assert "-l" in cmd and "-i" in cmd
    assert "--include='*.py'" in cmd
    cmd = build_grep_command("x", "src", output_mode="matches", line_number=True)
    assert "-o" in cmd and "-n" in cmd


def test_ls_tool_success():
    """ls_tool returns stdout as content, not truncated."""
    warm = mock.Mock()
    warm.run.return_value = BashResult(stdout="a\nb\n", stderr="", exit_code=0)
    res = ls_tool(warm, path=".")
    assert res.content == "a\nb\n"
    assert res.truncated is False


def test_ls_tool_error():
    """ls_tool surfaces stderr on nonzero exit."""
    warm = mock.Mock()
    warm.run.return_value = BashResult(stdout="", stderr="ls: cannot access nope\n", exit_code=2)
    res = ls_tool(warm, path="nope")
    assert res.content.startswith("ls: ")
    assert "cannot access" in res.content
    assert res.truncated is False


def test_glob_tool_success():
    """glob_tool returns one path per line."""
    warm = mock.Mock()
    warm.run.return_value = BashResult(stdout="a.py\nb.py\n", stderr="", exit_code=0)
    res = glob_tool(warm, pattern="*.py", path=".")
    assert res.content == "a.py\nb.py\n"
    assert res.truncated is False


def test_glob_tool_no_match_is_empty_success():
    """A nullglob no-match yields empty content with rc 0, not an error."""
    warm = mock.Mock()
    warm.run.return_value = BashResult(stdout="", stderr="", exit_code=0)
    res = glob_tool(warm, pattern="*.zzz", path=".")
    assert res.content == ""
    assert res.truncated is False


def test_grep_tool_content():
    """grep_tool content mode returns the match lines."""
    warm = mock.Mock()
    warm.run.return_value = BashResult(stdout="src/a.py:3: foo\n", stderr="", exit_code=0)
    res = grep_tool(warm, pattern="foo", path=".")
    assert res.content == "src/a.py:3: foo\n"
    assert res.truncated is False


def test_grep_tool_no_matches_normalized():
    """grep rc 1 (no matches) becomes empty content, not an error."""
    warm = mock.Mock()
    warm.run.return_value = BashResult(stdout="", stderr="", exit_code=1)
    res = grep_tool(warm, pattern="none", path=".")
    assert res.content == ""
    assert res.truncated is False


def test_grep_tool_error():
    """grep rc >1 surfaces stderr as an error."""
    warm = mock.Mock()
    warm.run.return_value = BashResult(stdout="", stderr="grep: bad\n", exit_code=2)
    res = grep_tool(warm, pattern="x", path=".")
    assert res.content.startswith("grep: ")
    assert res.truncated is False


def test_ls_tool_truncation():
    """ls_tool caps oversized content and sets truncated."""
    warm = mock.Mock()
    warm.run.return_value = BashResult(stdout="A" * 100000 + "\n", stderr="", exit_code=0)
    res = ls_tool(warm, path=".")
    assert res.truncated is True
    assert "chars truncated" in res.content
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_mcp_server.py -k "ls or glob or grep" -v`
Expected: FAIL — `LSResult` / `build_ls_command` etc. don't exist (ImportError).

- [ ] **Step 3: Write the minimal implementation**

In `tkt/mcp_server.py`:

1. Add to `__all__`: `LSResult`, `GlobResult`, `GrepResult`, `build_ls_command`,
   `build_glob_command`, `build_grep_command`, `ls_tool`, `glob_tool`,
   `grep_tool`.
2. Add the three `BaseModel` result classes after `ReadResult` (verbatim from the
   spec, including docstrings).
3. Add `build_ls_command`, `build_glob_command`, `build_grep_command` after
   `build_read_command` (verbatim from the spec).
4. Add `ls_tool`, `glob_tool`, `grep_tool` after `read_tool` (verbatim from the
   spec).
5. Register `ls`, `glob`, `grep` as `@mcp.tool()` inside `run_server` after
   `read` (verbatim from the spec, including docstrings).

Note: the MCP tool function `glob` is fine alongside the module-level
`build_glob_command`/`glob_tool` helpers — there is no `glob` name collision at
module scope.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_mcp_server.py -k "ls or glob or grep" -v` then
`pytest tests/test_mcp_server.py -v`
Expected: PASS (existing + new).

- [ ] **Step 5: Run lint/type checks**

Run: `ruff check tkt/mcp_server.py tests/test_mcp_server.py && ruff format --check tkt/mcp_server.py tests/test_mcp_server.py && mypy tkt/mcp_server.py`
Expected: clean. Fix docstring line-length gotchas per AGENTS.md if ruff flags them.

- [ ] **Step 6: Commit**

```bash
git add tkt/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): add sandboxed ls, glob, grep tools"
```

---

### Task 2: Update the Zed harness tool mapping and explorer wording

**Files:**

- Modify: `superpowers/skills/using-superpowers/references/zed-tools.md`
  (inside the `superpowers` submodule — see constraints)
- Modify: `harnesses/zed/skills/zed-explorer/SKILL.md`

**Interfaces:**

- Consumes: the `ls`/`glob`/`grep` tool names from Task 1.
- Produces: skills reflect that search/find/list go through the sandboxed tools.

- [ ] **Step 1: Update `zed-tools.md` (submodule)**

Change the three table rows:

```markdown
| Search file contents                                         | `grep`             |
| Find files by name                                           | `find_path`        |
| List a directory                                             | `list_directory`   |
```

to:

```markdown
| Search file contents                                         | `grep`             |
| Find files by name                                           | `glob`             |
| List a directory                                             | `ls`               |
```

(Leave the top prose line and other rows unchanged.)

- [ ] **Step 2: Update `zed-explorer/SKILL.md`**

Change:

```markdown
- Find files with `find_path` (glob patterns); search contents with `grep`;
  read with the `read` tool; list with `list_directory`; run shell commands with
```

to:

```markdown
- Find files with `glob` (glob patterns); search contents with `grep`;
  read with the `read` tool; list with `ls`; run shell commands with
```

- [ ] **Step 3: Commit the submodule change + bump the pointer**

The `zed-tools.md` edit lives in the `superpowers` submodule:

```bash
cd superpowers
git add skills/using-superpowers/references/zed-tools.md
git commit -m "Map search/find/list to sandboxed grep, glob, ls tools"
cd ..
git add superpowers
```

Then commit the `zed-explorer` change (which is in the main repo):

```bash
git add harnesses/zed/skills/zed-explorer/SKILL.md
git commit -m "docs(zed): map search/find/list to sandboxed tools"
```

(The submodule pointer bump above is included in this main-repo commit; if the
submodule is pinned/read-only for this work, stop and flag it instead.)

---

### Task 3: Update README and roadmap

**Files:**

- Modify: `README.md`
- Modify: `docs/zed-agent-roadmap.md`

**Interfaces:**

- Produces: docs reflect the extended read/execute tool surface and the batch
  completion.

- [ ] **Step 1: Update `README.md`**

Change:

```markdown
**Other** — `mcp-server` runs the MCP stdio server that exposes the sandboxed
`bash` tool, and `fix-openspec` rewrites OpenSpec skill files for OpenCode's
harness.
```

to:

```markdown
**Other** — `mcp-server` runs the MCP stdio server that exposes the sandboxed
`bash`, `read`, `grep`, `glob`, and `ls` tools, and `fix-openspec` rewrites
OpenSpec skill files for OpenCode's harness.
```

- [ ] **Step 2: Update `docs/zed-agent-roadmap.md`**

In section 6 R2, mark batch 2 done:

```markdown
2. `Grep`, `Glob`, `LS`.
```

to:

```markdown
2. `Grep`, `Glob`, `LS`. **DONE, 2026-09-02**
```

- [ ] **Step 3: Verify**

Run: `grep -rn "find_path\|list_directory" harnesses/zed/ README.md`
Expected: no remaining native-tool references in the Zed harness or README
(zed-explorer/zed-tools updated in Task 2).

- [ ] **Step 4: Commit**

```bash
git add README.md docs/zed-agent-roadmap.md
git commit -m "docs: note ls/glob/grep MCP tools and mark R2 batch 2 done"
```

---

### Task 4: Deliver the machine-side profile + override snippet (no repo edit)

**Files:**

- None modified in the repo. The output is a chat deliverable (the Gate-2 summary).

**Interfaces:**

- None. Produces the paste-ready snippet the human applies on their machine
  (Zed profile + system-prompt override), since those files are not in this repo.

- [ ] **Step 1: Confirm the snippet (in the Gate-2 summary)**

The implementing agent must include, in the final Gate-2 chat summary, a
paste-ready snippet like the following for the human to apply on their machine:

```text
# Zed agent profile (agent.profiles):
# Disable the native `grep`, `find_path`, and `list_directory` tools (mirror how
# `terminal` is disabled), and keep `grep`, `glob`, `ls` available. The MCP
# grep/glob/ls are provided by tkt.

# System-prompt override (add/adjust these lines):
# Search file contents with the sandboxed `grep` tool; find files by name with
# the sandboxed `glob` tool; list directories with the sandboxed `ls` tool.
# (The native grep/find_path guidance in the system prompt is gated on the
#  `grep` tool being enabled; disabling it removes that guidance.)
```

The implementer must adapt the exact profile syntax to the human's existing
profile (which already maps `terminal` -> `bash` and disables `terminal`); the
above is a guide, not a literal drop-in, and the human applies it by hand.

- [ ] **Step 2: No commit** (nothing to commit — this is a chat deliverable).

---

## Self-Review

**Spec coverage:**

- Sandboxed `ls`/`glob`/`grep` tools in `mcp_server.py` → Task 1.
- Distinct result types `LSResult`/`GlobResult`/`GrepResult` + command builders +
  tool helpers → Task 1 (tested).
- grep rc-1 normalization, error surfacing, output capping → Task 1 (tested).
- `zed-tools.md` + `zed-explorer` wording → Task 2 (incl. submodule pointer bump).
- README + roadmap check-off → Task 3.
- Machine-side profile/override template → Task 4.
- No `harnesses/zed/rules.md` change (verified unmotivated) → honored throughout.

**Placeholders:** All steps carry concrete code/commands; no "implement later".

**Type consistency:** `ls_tool(warm, *, path=".") -> LSResult`,
`glob_tool(warm, *, pattern, path=".") -> GlobResult`,
`grep_tool(warm, *, pattern, path=".", glob=None, output_mode="content",
ignore_case=False, line_number=False) -> GrepResult` are defined in Task 1
Step 3 and used consistently by the `@mcp.tool` wrappers; each result type has
`content: str` and `truncated: bool`.
