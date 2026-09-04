# Zed Native Agent Roadmap

**Status:** Active — forward-looking baseline for the Zed-native agent harness.

**Purpose:** A human-readable roadmap for the Zed agent setup: the shape of the
agent's tool surface, the active workstreams, and the deferred items. It is
organized around where the harness is going, not a log of what has already
shipped.

**Updates:** When a task is completed, _remove_ it, but update the design
rationale and/or standing constraints in-place as appropriate. Do not try to
maintain old designs or plans that have been obsoleted.

## 1. Context & design rationale

`tkt` runs LLM agents from the Zed editor via a custom agent profile plus the
tkt MCP server (`tkt mcp-server`). The MCP server is a sandboxed surface
(`bwrap`-based, per-project, warm-but-stateless) that the agent uses for its
read/execute/write work, while Zed-native tools are used for harness-intrinsic
operations that don't touch files.

The design rationale that holds:

- **The sandbox is the security boundary** for the agent's read/execute/write
  surface. Reads and writes that matter are sandboxed; `$HOME` is blocked and
  write locations are determined by the sandbox mount model, not by
  `tool_permissions` regexes (which are global, match on raw relative path
  strings, and cannot be scoped per project).
- **Per-project write scoping comes from the sandbox mount model.** In a tkt
  workspace the workspace root is mounted read-only with `.agent/` writable
  (writes are confined to `.agent/**` plus git metadata); in a single repo the
  whole repo is mounted writable.
- **A small, familiar tool suite**, modeled on Claude Code for names and
  argument shapes. Zed-native tools remain where the UI is the point (`skill`,
  `spawn_agent`, `ask_user`, `fetch`, `diagnostics`, `search_web`).
- **MCP tool returns are rendered as markdown in Zed's agent UI**: code fences,
  inline code, and explicit links render (so diffs highlight, links click). Every
  MCP tool returns markdown, so the agent's activity is legible to the human
  instead of opaque JSON. One hard rule from the W1 caveat: the **bare-path
  auto-linker that applies to agent messages does NOT apply to tool output** —
  to get a clickable path in a tool result you must emit an explicit link with
  backticked text, `` [`path`](path) ``. Never rely on bare-path auto-linking.
- **Tool-output formatting conventions** (W2): `bash`, `read`, `ls`, `glob`, and
  `grep` return code-fenced blocks (monospace, and clear about what is stdout vs.
  stderr vs. status); `read` links the target file path up top, as `write`/`edit`
  do for the files they touch; `todo_write` returns a markdown checklist. Nonzero
  exit codes, timeouts, and truncation are surfaced as plain-text notes. No
  path in a tool result is assumed to be clickable; only explicit links are.

### Tool surface

| Tool                                 | Backed by           | Notes                 |
| ------------------------------------ | ------------------- | --------------------- |
| `bash`                               | tkt MCP (sandboxed) | shell commands        |
| `read`                               | tkt MCP (sandboxed) | read files            |
| `ls` / `glob` / `grep`               | tkt MCP (sandboxed) | list / find / search  |
| `write` / `edit`                     | tkt MCP (sandboxed) | sandboxed create/edit |
| `todo_write`                         | tkt MCP (sandboxed) | returns a checklist |
| `skill` / `spawn_agent` / `ask_user` | Zed native          | intrinsic             |

File operations (delete/move/copy/mkdir) are done with `bash` (`rm`/`mv`/`cp`/`mkdir`),
which is already sandboxed and confined; they are not separate MCP tools.

## 2. Active workstreams

Each workstream is tracked at design-decision level; implementation details are
worked out when the workstream is picked up.

### W3 — Cross-session-state decision point (CWD + todo store)

Both the tracked cwd and the in-memory todo store are **per-project**, shared
across every session that uses a project's MCP server process.

- Decision point: **session-isolate** them (requires a session key that flows
  through tool calls), or **drop them** in favor of root-anchored calls and
  per-session state.
- Not urgent — simultaneous sessions in a project are rare — but worth deciding
  deliberately before the surface grows further.

### W4 — `ask_user` usage guidance

Add additional instructions for effective `ask_user` usage (primary agent) in
the harness/skills.

### W5 — GitHub MCP permissions

Identify which GitHub MCP tools are read-only vs. read-write, and define agent
profiles that use them.

## 3. Backlog / deferred

A. **WebFetch security** — explore why Zed sandboxes `fetch`/`terminal` and
decide whether/how to provide a sandboxed `webfetch`. Web access is disabled
in the meantime.

B. **Compaction research** — compare how OpenCode and Zed compact conversation
history; improve Zed's compaction if possible. Research/design first.

C. **Todo persistence to a visible file** — a future enhancement where the todo
list lives in a file the human can keep open, giving a persistent status
view rather than per-call markdown cards.

D. **`test_pull.py` `forkpty()` deprecation warning** — maintenance; the suite
emits `DeprecationWarning: this process is multi-threaded, use of forkpty()
may lead to deadlocks` from `pty.fork()` in
`test_diverged_rebase_with_tty_editor_succeeds`. Pre-existing and harmless
today, but worth addressing.

E. **Language-aware `read` fences** — `read` fences its output as `text` (no
syntax highlighting), like `bash`/`ls`/`glob`/`grep`. Possible enhancement:
detect the language from the file extension (e.g. `python` for `.py`) so reads
colorize the way `edit` diffs do. Deferred; the uniform `text` fence was a
deliberate choice to avoid mis-highlighting arbitrary output, and the value of
colored reads is unproven.

## 4. Standing constraints

- **OpenCode coexists** throughout testing; skill/AGENTS changes must not break
  the existing OpenCode workflow.
- **Sandbox mount model** — workspace: root read-only + `.agent` writable;
  single repo: whole repo writable. This is the source of per-project write
  scoping and should be preserved, and is automatic as long as the existing
  sandbox set-up code is used.
- **`ask_user` is for the primary agent.** Subagents also have the tool (it
  cannot be blocked — subagents inherit the parent's profile), but it interrupts
  their flow and renders poorly, so the skills discourage it and instruct
  subagents to surface uncertainty in their report instead.
