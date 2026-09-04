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
- **MCP tool returns are rendered as markdown in Zed's agent UI** (Zed renders
  MCP text content through the same markdown engine as agent messages). So our
  MCP tools return markdown, which keeps the agent's activity legible to the
  human instead of opaque.

### Tool surface

| Tool                                 | Backed by               | Notes                        |
| ------------------------------------ | ----------------------- | ---------------------------- |
| `bash`                               | tkt MCP (sandboxed)     | shell commands               |
| `read`                               | tkt MCP (sandboxed)     | read files                   |
| `ls` / `glob` / `grep`               | tkt MCP (sandboxed)     | list / find / search         |
| `write_file` / `edit_file`           | **tkt MCP (sandboxed)** | W1 — moving into the sandbox |
| `todo_write`                         | tkt MCP (sandboxed)     | W2 — returns markdown        |
| `skill` / `spawn_agent` / `ask_user` | Zed native              | intrinsic                    |

File operations (delete/move/copy/mkdir) are done with `bash` (`rm`/`mv`/`cp`/`mkdir`),
which is already sandboxed and confined; they are not separate MCP tools.

## 2. Active workstreams

Each workstream is tracked at design-decision level; implementation details are
worked out when the workstream is picked up.

### W1 — Sandboxed MCP write/edit (project-level cwd)

Move the write/edit surface into the sandbox as MCP tools.

- Add MCP write and read tools, to be modeled on **Claude Code** (names and
  argument shapes TBD), not on Zed's native tools.
- Run in the sandbox, sharing the existing **project-level tracked cwd** (not
  session-isolated; multiple simultaneous sessions in a project are rare).
- Writes are confined by the mount model: `.agent/**` in a workspace, the whole
  repo in single-repo mode. This also fixes writes to git-ignored scratch under
  `<pkg>/.superpowers/`, which native tools refused.
- Each call returns a **markdown diff summary** so the human can review the
  change in the tool card.
- Update the `zed-tools.md` mapping to point write/edit at the MCP tools.
- Disable the native `write_file` and `edit_file` tools in the agent profile
  (human does this).

### W2 — MCP tools return Markdown

Have every MCP tool return markdown-formatted output so it renders readably in
the agent UI: `todo_write`, `bash`, `read`, `ls`, `glob`, `grep`, and the new
read/edit. JSON returns do not format well.

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
