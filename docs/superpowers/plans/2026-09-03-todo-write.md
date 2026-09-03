# R2 Batch 3 — `todo_write` MCP Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `todo_write` MCP tool to the tkt MCP server — a Claude-Code-style whole-list-replace task scratchpad held in-memory on the server process — and fold it into the Zed harness (`zed-tools.md` mapping + roadmap check-off), so the Zed native agent tracks its task list through the MCP server instead of an ad-hoc Markdown checklist. This is batch 3 of R2, following `read` and `grep`/`glob`/`ls`.

**Architecture:** Unlike the existing stateless MCP tools (`bash`/`read`/`ls`/`glob`/`grep`), a todo list is stateful. The list is held **host-side in-memory on the MCP server process** — each `run_server` instance owns one `TodoStore`, captured in the `@mcp.tool` closure (same pattern as `warm`). `todo_write(todos)` replaces the stored list wholesale (Claude Code semantics: the model passes the full desired list every call) and returns it as a `TodoWriteResult`. Not sandboxed: it's model bookkeeping with no file access, so the `$HOME`-blocking rationale doesn't apply. State is per-session and lost on process restart, which is acceptable for a scratchpad. Exposed tool name is `todo_write` (snake_case).

**Tech Stack:** Python 3.13, click, pydantic, pytest. No new third-party deps.

**Spec:** `docs/superpowers/specs/2026-09-03-todo-write-design.md`

## Global Constraints

- Python 3.13; deps are `click`, `GitPython`, `pyyaml`, `json5` — no new third-party dependencies.
- Every `.py` file carries the BSD-3-Clause header exactly as in existing modules (preserve it; do not alter).
- Must pass before each commit and at the end: `ruff check .` and `ruff format --check .` and `mypy tkt/`.
- `tkt` is not pip-distributed; do not add packaging config.
- OpenCode workflow must keep working throughout (coexistence).
- `superpowers/skills/using-superpowers/references/zed-tools.md` lives inside the `superpowers` **submodule**. Editing it requires committing in the submodule and bumping the submodule pointer in the main repo (`git add superpowers`), per the `a2e1e84` convention. Do not touch other files in the submodule.
- Do **not** edit `harnesses/zed/rules.md` (the roadmap already established `TodoWrite` adds no system-prompt override) and do **not** edit `README.md` (its tool list names the *sandboxed* surface; `todo_write` is not sandboxed).
- The Zed agent profile (allowing the `todo_write` MCP tool) is machine-side (human-applied), **not** in this repo; delivered as a note in the final Gate-2 summary.

---

### Task 1: Add `TodoItem`, `TodoWriteResult`, `TodoStore`, and the `todo_write` MCP tool

**Files:**

- Modify: `tkt/mcp_server.py`
- Test: `tests/test_mcp_server.py`

**Interfaces:**

- Consumes: `BaseModel` (already imported). No sandbox/warm usage.
- Produces: `TodoItem`, `TodoWriteResult`, `TodoStore` (with `.replace(todos) -> TodoWriteResult`); the `todo_write` MCP tool registered in `run_server`; all three names added to `__all__`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_mcp_server.py`, add `TodoItem`, `TodoStore`, `TodoWriteResult` to the existing `from tkt.mcp_server import (...)` block, then append:

```python
def test_todo_item_defaults_status_pending():
    """TodoItem defaults to status='pending' and no activeForm."""
    item = TodoItem(content="Do the thing")
    assert item.status == "pending"
    assert item.activeForm is None


def test_todo_item_fields_passthrough():
    """content/status/activeForm round-trip through TodoItem."""
    item = TodoItem(content="Build", status="in_progress", activeForm="Building")
    assert item.content == "Build"
    assert item.status == "in_progress"
    assert item.activeForm == "Building"


def test_todo_store_replace_returns_list():
    """replace() stores the list and returns it in a TodoWriteResult."""
    store = TodoStore()
    result = store.replace([TodoItem(content="a"), TodoItem(content="b")])
    assert isinstance(result, TodoWriteResult)
    assert [t.content for t in result.todos] == ["a", "b"]


def test_todo_store_replace_overwrites_previous():
    """A later replace() fully replaces the prior list."""
    store = TodoStore()
    store.replace([TodoItem(content="a"), TodoItem(content="b")])
    result = store.replace([TodoItem(content="c")])
    assert [t.content for t in result.todos] == ["c"]


def test_todo_store_empty_clears():
    """Replacing with an empty list clears the stored list."""
    store = TodoStore()
    store.replace([TodoItem(content="a")])
    result = store.replace([])
    assert result.todos == []


def test_todo_store_preserves_statuses_and_active_form():
    """status/activeForm are preserved through replace()."""
    store = TodoStore()
    result = store.replace(
        [
            TodoItem(content="done", status="completed"),
            TodoItem(content="doing", status="in_progress", activeForm="Building"),
        ]
    )
    assert result.todos[0].status == "completed"
    assert result.todos[1].status == "in_progress"
    assert result.todos[1].activeForm == "Building"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_mcp_server.py -k "todo" -v`
Expected: FAIL — `TodoItem` / `TodoStore` / `TodoWriteResult` don't exist (ImportError).

- [ ] **Step 3: Write the minimal implementation**

In `tkt/mcp_server.py`:

1. Add to `__all__`:

```python
    "TodoItem",
    "TodoStore",
    "TodoWriteResult",
```

2. Add the models + store after `GrepResult` (before `encode_field`), verbatim from the spec:

```python
class TodoItem(BaseModel):
    """One entry in the agent's todo list.

    ``status`` is one of ``pending``, ``in_progress``, ``completed``, or
    ``cancelled``; ``activeForm`` is the present-participle verb phrase
    (e.g. ``Building``, ``Testing``) shown while the item is in progress.
    """

    content: str
    status: str = "pending"
    activeForm: str | None = None


class TodoWriteResult(BaseModel):
    """The todo list after a ``todo_write`` call.

    ``todos`` is the full current list; the caller replaces it wholesale on
    each call, so this is both the result and the read of the current state.
    """

    todos: list[TodoItem]


class TodoStore:
    """In-memory scratchpad holding the agent's current todo list.

    Each ``run_server`` instance owns one ``TodoStore``. ``todo_write``
    replaces the list wholesale (Claude Code-style) and returns it, so the
    store is a trivial holder. State is host-side (not sandboxed — this is
    model bookkeeping with no file access) and is lost if the server process
    restarts, which is acceptable for a scratchpad.
    """

    def __init__(self) -> None:
        self._todos: list[TodoItem] = []

    def replace(self, todos: list[TodoItem]) -> TodoWriteResult:
        """Replace the stored list with ``todos`` and return it."""
        self._todos = list(todos)
        return TodoWriteResult(todos=list(self._todos))
```

3. Register the tool inside `run_server`, after `grep` (verbatim from the spec),
   capturing a per-instance `TodoStore`:

```python
    todo_store = TodoStore()

    @mcp.tool()
    def todo_write(
        todos: list[TodoItem],
        description: str | None = None,  # present for human approvals of tool actions
    ) -> TodoWriteResult:
        """Replace the agent's todo list and return the full new list.

        The caller passes the entire desired list on every call (Claude
        Code-style); the stored list is replaced wholesale, so this is
        idempotent and stateless from the model's perspective. The list is
        held in memory for the life of this server process (not sandboxed —
        it is model bookkeeping with no file access) and is lost if the
        process restarts. ``description`` is a per-call rationale for the
        human; it does not change behavior.

        Args:
            todos: The full desired todo list. Each item has ``content``,
                ``status`` (``pending``, ``in_progress``, ``completed``, or
                ``cancelled``; default ``pending``), and an optional
                ``activeForm`` verb phrase.
            description: Optional human-readable rationale for this call.
        """
        return todo_store.replace(todos)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_mcp_server.py -k "todo" -v` then
`pytest tests/test_mcp_server.py -v`
Expected: PASS (existing + new).

- [ ] **Step 5: Run lint/type checks**

Run: `ruff check tkt/mcp_server.py tests/test_mcp_server.py && ruff format --check tkt/mcp_server.py tests/test_mcp_server.py && mypy tkt/mcp_server.py`
Expected: clean. Fix docstring line-length gotchas per AGENTS.md if ruff flags them.

- [ ] **Step 6: Commit**

```bash
git add tkt/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): add todo_write task-tracking tool"
```

---

### Task 2: Update the Zed harness tool mapping and mark the roadmap batch done

**Files:**

- Modify: `superpowers/skills/using-superpowers/references/zed-tools.md`
  (inside the `superpowers` submodule — see constraints)
- Modify: `docs/zed-agent-roadmap.md`

**Interfaces:**

- Consumes: the `todo_write` tool name from Task 1.
- Produces: skills map task tracking to `todo_write`; roadmap marks batch 3 done.

- [ ] **Step 1: Update `zed-tools.md` (submodule)**

Add a table row after the `List a directory` row:

```markdown
| List a directory                                             | `ls`               |
| Create/update the task todo list                            | `todo_write`       |
```

(Keep `List a directory` as-is; add the new row directly beneath it.)

Then replace the note (currently line 33):

```markdown
- Task tracking ("create a todo", "mark complete") maps to the `todo_write`
  tool — a whole-list-replace scratchpad held in the tkt MCP server's memory
  for the session.
```

- [ ] **Step 2: Update `docs/zed-agent-roadmap.md`**

In section 6 R2, mark batch 3 done:

```markdown
3. `TodoWrite`.
```

to:

```markdown
3. `TodoWrite`. **DONE, 2026-09-03**
```

(The section-4 tool-suite table already lists `TodoWrite` backed by tkt MCP and
needs no change; design decision #4 in section 7 already records it.)

- [ ] **Step 3: Commit the submodule change + bump the pointer**

The `zed-tools.md` edit lives in the `superpowers` submodule:

```bash
cd superpowers
git add skills/using-superpowers/references/zed-tools.md
git commit -m "Map task tracking to the todo_write MCP tool"
cd ..
git add superpowers
```

Then commit the roadmap change (in the main repo):

```bash
git add docs/zed-agent-roadmap.md
git commit -m "docs: mark R2 batch 3 (TodoWrite) done"
```

(The submodule pointer bump above is included in this main-repo commit; if the
submodule is pinned/read-only for this work, stop and flag it instead.)

- [ ] **Step 4: Verify no stray references**

Run: `grep -rn "Markdown checklist" superpowers/skills/using-superpowers/references/zed-tools.md`
Expected: no output (the note was replaced).

---

### Task 3: Deliver the machine-side profile note (no repo edit)

**Files:**

- None modified in the repo. The output is a chat deliverable (the Gate-2 summary).

**Interfaces:**

- None. Produces the note the human applies on their machine (Zed agent profile
  allowlisting the `todo_write` MCP tool), since that profile is not in this repo.

- [ ] **Step 1: Confirm the note (in the Gate-2 summary)**

The implementing agent must include, in the final Gate-2 chat summary, a
paste-ready note like the following for the human to apply on their machine:

```text
# Zed agent profile (agent.profiles):
# Allow the `todo_write` MCP tool provided by tkt so the agent can track its
# task list through the MCP server. (Mirror how the other tkt MCP tools —
# read/grep/glob/ls — are allowed in the profile.)
```

The implementer must adapt the exact profile syntax to the human's existing
profile (which already allows the other tkt MCP tools); the above is a guide,
not a literal drop-in, and the human applies it by hand.

- [ ] **Step 2: No commit** (nothing to commit — this is a chat deliverable).

---

## Self-Review

**Spec coverage:**

- `TodoItem` / `TodoWriteResult` / `TodoStore` + `todo_write` MCP tool in
  `mcp_server.py` → Task 1 (tested).
- Whole-list-replace semantics (replace overwrites, empty clears, status/
  activeForm passthrough, `pending` default) → Task 1 (tested).
- `zed-tools.md` row + note → Task 2 (incl. submodule pointer bump).
- Roadmap batch 3 check-off → Task 2.
- Machine-side profile note → Task 3.
- No `harnesses/zed/rules.md` change and no `README.md` change (both unmotivated
  per spec decisions 5 and 6) → honored throughout.

**Placeholders:** All steps carry concrete code/commands; no "implement later".

**Type consistency:** `TodoStore.replace(todos: list[TodoItem]) -> TodoWriteResult`
is defined in Task 1 Step 3 and used consistently by the `@mcp.tool` wrapper
(`todo_store.replace(todos)`) and by the Task 1 tests. `TodoItem(content,
status="pending", activeForm=None)` field names match across the model, the
tests, and the tool docstring.
