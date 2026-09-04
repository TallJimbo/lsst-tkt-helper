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
  inline code, and explicit links render (so diffs highlight, links click). One
  caveat learned in W1: the **bare-path auto-linker that applies to agent
  messages does NOT apply to tool output** — to get a clickable path in a tool
  result you must emit an explicit link (see W2). So our MCP tools return
  markdown, which keeps the agent's activity legible to the human instead of
  opaque.

### Tool surface

| Tool                                 | Backed by           | Notes                 |
| ------------------------------------ | ------------------- | --------------------- |
| `bash`                               | tkt MCP (sandboxed) | shell commands        |
| `read`                               | tkt MCP (sandboxed) | read files            |
| `ls` / `glob` / `grep`               | tkt MCP (sandboxed) | list / find / search  |
| `write` / `edit`                     | tkt MCP (sandboxed) | sandboxed create/edit |
| `todo_write`                         | tkt MCP (sandboxed) | W2 — returns markdown |
| `skill` / `spawn_agent` / `ask_user` | Zed native          | intrinsic             |

File operations (delete/move/copy/mkdir) are done with `bash` (`rm`/`mv`/`cp`/`mkdir`),
which is already sandboxed and confined; they are not separate MCP tools.

## 2. Active workstreams

Each workstream is tracked at design-decision level; implementation details are
worked out when the workstream is picked up.

### W2 — MCP tools return Markdown

Have every MCP tool return markdown-formatted output so it renders readably in
the agent UI: `todo_write`, `bash`, `read`, `ls`, `glob`, `grep`, and the new
`write`/`edit`. JSON returns do not format well.

What renders in MCP tool output (verified during W1, 2026-09-04):

- **Code fences** render with syntax highlighting (a ` ```diff ` fence shows
  colored +/- lines).
- **Explicit markdown links** `[text](path)` render as clickable links; an
  absolute target resolves to the file, a relative one against the workspace
  root.
- **Backticked code spans** render as monospace. They do **not** auto-link a
  bare path in `write`/`edit` output (though they did linkify absolute paths in
  `bash` output — tool-output renderers are not consistent with each other, nor
  with agent messages).
- **Bare file paths do not auto-link** in tool output at all. This differs from
  agent messages, where Zed auto-links bare and embedded paths.

Rule for W2: whenever a tool returns a path the human should click, emit it as
an explicit markdown link with backticked text for monospace —
`` [`/abs/path`](/abs/path) ``. Never rely on bare-path auto-linking in tool
output. Test each tool's actual rendered card rather than assuming consistency.

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
