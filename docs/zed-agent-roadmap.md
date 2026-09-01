# Zed Native Agent Roadmap

**Status:** Approved by human (design session, 2026-08-31).
**Purpose:** A human-readable roadmap guiding the migration of the working harness from
OpenCode to Zed's native agent. Each phase below is expected to run as its own
design -> plan -> build cycle, except where noted.

## 1. Context & motivation

`tkt` currently runs LLM agents via **OpenCode** inside a `bwrap` sandbox (`tkt
sandbox-run`), using custom `sp-*` agent definitions and the `superpowers` skills.
The motivation to switch to **Zed's native agent** is a better UI for combining
agentic work with the human's own work than either the OpenCode TUI or running
OpenCode in Zed over ACP provides.

The tkt MCP server (`tkt/mcp_server.py`, `tkt mcp-server`) already exists: a
FastMCP stdio server exposing a sandboxed `bash` tool backed by a warm-but-stateless
`bwrap` holder. The goal is to grow this into the agent's default read/execute
surface while keeping Zed's UI-integrated native tools for editing.

## 2. Goals

1. **A small, coherent, familiar tool suite**, not a 1:1 port of Zed's ~24 built-in
   tools (plus MCP tools, ~59 offered). **Claude Code** is the baseline for tool
   *shape and naming*; OpenCode is second-best; Zed is worst on familiarity.
   Fewer, familiar tools is itself a goal.
2. **Sandboxed default for the read/execute surface** (where `$HOME` read-blocking
   and bulletproof rules matter), and **Zed native tools kept only where the UI is
   the point** (`write_file`/`edit_file` for clickable diffs). We deliberately do
   not rely on `tool_permissions` regexes for *reads* — they cannot block reads of
   `$HOME` at all; sandboxing can.
3. **Move prompts out of OpenCode agent definitions into skills**, with clear,
   documented rules for what content belongs in each place (skills vs. `AGENTS.md`
   vs. OpenCode profiles vs. the Zed override).
4. **OpenCode coexists** throughout testing — skills/AGENTS changes must not break
   the existing OpenCode workflow.

## 3. Current state

- **MCP server:** `tkt mcp-server` exposes a sandboxed `bash` tool (warm holder,
  fresh child per call, tracked cwd, timeout enforcement). Registered as a Zed
  context server. Design and hardening are done.
- **Agent profile:** a custom Zed profile already maps `terminal` -> `tkt:bash`
  (disables built-in `terminal`, enables the MCP `bash`). Lives on the human's
  machine (not visible from this repo).
- **Skills:** the `superpowers` skills are *mostly* adapted to Zed already. The
  only gaps are `terminal` -> `tkt:bash` in `zed-tools.md` and the absence of
  `ask_user`.

## 4. Target tool suite

| Tool | Backed by | Claude Code analog | Notes |
|---|---|---|---|
| `Bash` | tkt MCP (sandboxed) | `Bash` | **done** |
| `Read` | tkt MCP (sandboxed, `$HOME`-blocked) | `Read` | see R2 tension below |
| `Grep` | tkt MCP (sandboxed) | `Grep` | |
| `Glob` | tkt MCP (sandboxed) | `Glob` | |
| `LS` | tkt MCP (sandboxed) | `LS` | |
| `Write` / `Edit` | **Zed native** `write_file`/`edit_file` | `Write`/`Edit` | kept native for UI diffs; regex-scope to `.agent/**` |
| `TodoWrite` | tkt MCP | `TodoWrite` | added per decision |
| `Task` | **Zed native** `spawn_agent` | `Task` | |
| `Skill` | **Zed native** `skill` | `Skill` | |
| `WebFetch` | deferred | `WebFetch` | use native `fetch` meanwhile (R4) |
| `AskUserQuestion` | **Zed native** `ask_user` | `AskUserQuestion` | encourage use; went unused in prior Zed support |
| (`delete`/`copy`/`move`/`mkdir`) | Zed native | — | write-ish ops; native + regex |

Target total is roughly 6–8 primary tools versus ~59 offered today.

## 5. Zed system-prompt adaptation findings

Zed's system prompt (`crates/agent/src/templates/system_prompt.hbs`) references tools
both conditionally and unconditionally. We cannot change Zed source, so adaptation is
mitigation via an override prompt:

- **Conditional on `available_tools`** (disabling the tool removes its guidance — a
  clean win):
  - `grep`/`find_path` (lines 60–63): the whole "prefer `grep`… use `find_path`…"
    block disappears when `grep` is disabled.
  - `terminal` (lines 157–202): the entire "Terminal sandbox" section (sandbox
    permissions, `fs_write_paths`, elevated-permission requests) is gated on
    `terminal`; disabling it removes a long section that would otherwise mislead the
    model about sandbox semantics.
- **Unconditional** (the override must be explicit here):
  - Line 39: "…and terminal commands for build, test…" — the override should state
    shell commands run via the `bash` tool.
  - Lines 243–245 (Agent Skills): "If the Skill references additional files, use
    `read_file` to access them." Conflicts with replacing `read_file` with sandboxed
    MCP `Read`.
- **Tension for the `Read` batch:** global skills live in `~/.agents/skills/`, which
  is under `$HOME` — exactly what the sandbox blocks. A sandboxed `Read` cannot read
  skill reference files unless we (a) mount `~/.agents/skills` read-only into the
  sandbox, (b) keep native `read_file` for skill files, or (c) skills stop relying on
  `$HOME` reference files. Resolve as a first-class decision in the `Read` batch.

The system-prompt override is maintained **per-batch inside R2**, alongside the skills
and profile updates.

## 6. Roadmap phases

### R0 — Catchup (maintenance)
- Update `superpowers/skills/using-superpowers/references/zed-tools.md`: `terminal` ->
  `tkt:bash`; add `ask_user` (and the question-asking skills). Small; the profile
  already maps `terminal` -> `tkt:bash`.

### R1 — Prompts -> skills (FIRST, before tools)
- **Primary `sp-*` agents** (design/plan/build/debug/review) are thin shells around
  skills; mostly subsume them into the global Zed `AGENTS.md` or a modification to the
  `using-superpowers` skill, covering change flow and gates. With Zed it is more
  natural for the human to **invoke a skill directly to signal a gate**; agents wait
  for that signal. Optional: rename skills to `design`/`plan`/`build` for brevity.
- **Subagent prompts** (`sp-review` and OpenCode built-ins) go into new skills the
  primary tells the subagent to load; consider superpowers' subagent-prompt templates;
  reconcile duplication with `sp-review`.
- **Deliverable: content-placement rules** — documented rules for what belongs in
  skills vs. `AGENTS.md` vs. OpenCode profiles vs. the Zed override, to end the
  scattered placement.

### R2 — Iterative MCP tool batches
Each batch is one design -> plan -> build cycle that updates **all of**: the MCP server
tool(s), the skills references, the agent profile, and the Zed system-prompt override —
together. Expected order:
1. `Read` (resolve the `~/.agents/skills`/`$HOME` tension first).
2. `Grep`, `Glob`, `LS`.
3. `TodoWrite`.

OpenCode is kept working throughout.

### R4 — Deferred: WebFetch security
Explore why Zed sandboxes `fetch` and `terminal`; decide whether/how to provide a
sandboxed `webfetch`. Use Zed native `fetch` in the meantime. Last on the list, done
after everything else.

### Verify-only (was R3): `AGENTS.md.in`
Confirm whether the workspace instructions template needs any adaptation to the
host-agent model. Likely nothing; treat as a check, and ride along inside R2 batches
if anything does come up.

## 7. Design decisions log

1. **Tool suite = redesign, not 1:1 port.** Claude Code baseline; small, familiar.
2. **Sandbox = default for read/execute; native only for UI** (`write_file`/`edit_file`
   diffs), regex-scoped to `.agent/**`.
3. **Don't fight Zed tool names** of tools we use natively (e.g. keep
   `write_file`/`edit_file`); only *our* MCP tools get Claude-style names.
4. **Add `TodoWrite`** as an MCP tool.
5. **`WebFetch` deferred** to the end (R4); use native `fetch` meanwhile.
6. **`ask_user` should be used**; add it to `zed-tools.md` and question-asking skills
   (it was overlooked in the prior Zed support work).
7. **OpenCode support is retained**, coexisting with the Zed setup during testing.
8. **Prompts-first order:** move prompts to skills (R1) before expanding tools (R2).
9. **System-prompt override maintained per-batch** in R2, alongside skills + profile.
10. **Normalize content placement** with documented rules (skills vs. AGENTS.md vs.
    profiles vs. override).

## 8. Deferred / open items

- WebFetch security rationale and design (R4).
- `AGENTS.md.in` — confirm whether anything needs adapting (verify-only).
- Skill renaming to `design`/`plan`/`build` (R1).
- Subagent-prompt templates vs. `sp-review` duplication reconciliation (R1).
