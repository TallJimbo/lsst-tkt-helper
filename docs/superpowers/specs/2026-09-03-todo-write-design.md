# R2 Batch 3 — `todo_write` MCP Tool — Design Handover

**Date:** 2026-09-03
**Status:** Approved by human in conversation (before implementation).
**Implements:** batch 3 (the `TodoWrite` tool) of phase R2 in
`docs/zed-agent-roadmap.md`.

## Goal

Add a `todo_write` MCP tool to the tkt MCP server — a Claude-Code-style task
scratchpad — and fold it into the Zed harness (skills mapping + roadmap
check-off), so the Zed native agent can track its task list through the sandboxed
MCP server rather than falling back to an ad-hoc Markdown checklist. This is the
third R2 MCP tool batch, following `read` (batch 1) and `grep`/`glob`/`ls`
(batch 2). OpenCode is untouched throughout.

## Architecture

`todo_write` is unlike the existing MCP tools (`bash`, `read`, `ls`, `glob`,
`grep`): those are **stateless** — each call builds a small command, runs it
through the warm holder, and returns a result. A todo list is **stateful** — it
must persist across calls within an agent session. Rather than running through
the sandbox, the list is held **host-side, in memory, on the MCP server
process**. Each `run_server` instance owns one `TodoStore`; state is per-session
and lost if the server process restarts, which is acceptable for a scratchpad
(identical to Claude Code).

Sandboxing is deliberately **not** used: `todo_write` is model bookkeeping with
no file access, so the `$HOME`-read-blocking rationale that motivates sandboxing
`read`/`grep`/`glob`/`ls` does not apply.

### Behavior / API semantics

Claude Code-style **whole-list replace** (explicit human preference): the model
passes the _full_ desired list on every call; the server replaces its stored list
and returns it. No separate add/update/complete/delete/list tools — a read is
built into the return value. This is idempotent and stateless from the model's
perspective: each call describes the complete intended end-state.

Each item mirrors Claude Code's `TodoWrite` shape:

- `content: str` — the task description
- `status: str = "pending"` — one of `pending`, `in_progress`, `completed`,
  `cancelled`
- `activeForm: str | None = None` — the present-participle verb phrase
  (e.g. `Building`, `Testing`) shown while an item is in progress

### Tool name

Exposed MCP tool name is **`todo_write`** (snake_case, explicit human preference;
consistent with MCP tool-naming convention and the lowercase `bash`/`read`/
`grep`/`glob`/`ls` names). FastMCP derives the tool name from the function name,
so the function is named `todo_write` (no `name=` override needed).

## Concretes (verbatim — authoritative for implementation)

### `tkt/mcp_server.py` additions

`__all__` additions:

```python
    "TodoItem",
    "TodoStore",
    "TodoWriteResult",
```

New models + store (after `GrepResult`, before `encode_field`):

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

The MCP tool is registered inside `run_server`, after `grep`, capturing a
per-instance `TodoStore` in the closure (same pattern as `warm`):

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

### `superpowers/skills/using-superpowers/references/zed-tools.md`

Add a mapping row (this file lives inside the `superpowers` submodule; see Open
items). Place the row after `List a directory`:

```markdown
| Create/update the task todo list | `todo_write` |
```

And replace the note (line 33):

```markdown
- Task tracking ("create a todo", "mark complete") maps to the `todo_write`
  tool — a whole-list-replace scratchpad held in the tkt MCP server's memory
  for the session.
```

### `docs/zed-agent-roadmap.md`

Mark batch 3 in section 6 R2 as done:

```markdown
3. `TodoWrite`. **DONE, 2026-09-03**
```

The target-suite table (section 4) already lists `TodoWrite` backed by tkt MCP
and needs no change; design decision #4 ("Add `TodoWrite` as an MCP tool") is
already recorded.

### Explicitly NOT changed

- **`harnesses/zed/rules.md`** — the roadmap (section 6 R2) already states
  `TodoWrite` adds no system-prompt override: the Zed system prompt never names
  a todo tool unconditionally. No change motivated.
- **`README.md`** — its "Other" block explicitly describes the _sandboxed_
  read/execute surface (`bash`, `read`, `grep`, `glob`, `ls`); `todo_write` is
  not sandboxed, so folding it in there would be inaccurate. The roadmap and
  `zed-tools.md` are the documentation of record for the new tool.
- **OpenCode** — untouched (coexistence, roadmap Goal 4).

## Key decisions log

1. **`todo_write` is host-side and in-memory, not sandboxed.** A todo list is
   bookkeeping, not file access; the `$HOME`-blocking rationale doesn't apply.
   State lives on the MCP server process (per `run_server` instance), is
   per-session, and is lost on process restart — acceptable for a scratchpad.
2. **Claude Code-style whole-list replace** (explicit human preference): the
   model passes the full desired list each call; no incremental add/update/
   delete/list tools. Idempotent and stateless from the model's view.
3. **Exposed name `todo_write`** (explicit human preference): snake_case,
   consistent with MCP naming and the existing lowercase tool names, not the
   roadmap's Claude-Code-parity label `TodoWrite`.
4. **State owned by a trivial `TodoStore` class** instantiated per
   `run_server`, captured in the MCP-tool closure (same pattern as `warm`),
   making the pure logic directly unit-testable with a fresh instance.
5. **No `harnesses/zed/rules.md` change** — roadmap already established
   `TodoWrite` adds no system-prompt override.
6. **No `README.md` change** — its tool list names the _sandboxed_ surface;
   `todo_write` is not sandboxed, so adding it there would mislead.
7. **Machine-side profile enable is human-applied** — the Zed agent profile must
   allowlist the `todo_write` MCP tool; it lives on the human's machine and is
   **not** in this repo (delivered as a paste-ready note in the plan).
8. **OpenCode untouched** — coexistence maintained (roadmap Goal 4).

## Open items / assumptions

- **`zed-tools.md` lives in the `superpowers` submodule.** Editing it means
  committing inside the submodule, then bumping the submodule pointer in the
  main repo (convention from `a2e1e84 Update superpowers submodule for Zed tool
mapping`). The implementing agent must do both, or flag it if the submodule is
  pinned/read-only for this work.
- **Machine-side profile** (enable `todo_write` in the Zed agent profile). The
  implementing agent must NOT edit it; a paste-ready note is delivered in the
  plan's final chat summary for the human.
- **Persistence / session isolation explicitly deferred.** This MVP is in-memory,
  per-project: no persistence across server restarts and no isolation between
  concurrent sessions in one project (which share the per-project server process
  and its tracked CWD). Tracked as roadmap item E6; triaged into its own cycle.
