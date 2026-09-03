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
   _shape and naming_; OpenCode is second-best; Zed is worst on familiarity.
   Fewer, familiar tools is itself a goal.
2. **Sandboxed default for the read/execute surface** (where `$HOME` read-blocking
   and bulletproof rules matter), and **Zed native tools kept only where the UI is
   the point** (`write_file`/`edit_file` for clickable diffs). We deliberately do
   not rely on `tool_permissions` regexes for _reads_ — they cannot block reads of
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
- **Agent profile:** a custom Zed profile already maps `terminal` -> `bash`
  (disables built-in `terminal`, enables the MCP `bash`). Lives on the human's
  machine (not visible from this repo).
- **Skills:** the `superpowers` skills are _mostly_ adapted to Zed already. The
  only gaps are `terminal` -> `bash` in `zed-tools.md` and the absence of
  `ask_user`.

## 4. Target tool suite

| Tool                             | Backed by                               | Claude Code analog | Notes                                                |
| -------------------------------- | --------------------------------------- | ------------------ | ---------------------------------------------------- |
| `Bash`                           | tkt MCP (sandboxed)                     | `Bash`             | **done**                                             |
| `Read`                           | tkt MCP (sandboxed, `$HOME`-blocked)    | `Read`             | see R2 tension below                                 |
| `Grep`                           | tkt MCP (sandboxed)                     | `Grep`             |                                                      |
| `Glob`                           | tkt MCP (sandboxed)                     | `Glob`             |                                                      |
| `LS`                             | tkt MCP (sandboxed)                     | `LS`               |                                                      |
| `Write` / `Edit`                 | **Zed native** `write_file`/`edit_file` | `Write`/`Edit`     | kept native for UI diffs; regex-scope to `.agent/**` |
| `TodoWrite`                      | tkt MCP                                 | `TodoWrite`        | added per decision                                   |
| `Task`                           | **Zed native** `spawn_agent`            | `Task`             |                                                      |
| `Skill`                          | **Zed native** `skill`                  | `Skill`            |                                                      |
| `WebFetch`                       | deferred                                | `WebFetch`         | use native `fetch` meanwhile (R4)                    |
| `AskUserQuestion`                | **Zed native** `ask_user`               | `AskUserQuestion`  | primary only; discouraged in subagents (E1, DONE)    |
| (`delete`/`copy`/`move`/`mkdir`) | Zed native                              | —                  | write-ish ops; native + regex                        |

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
  `$HOME` reference files. **R1 chose (a)** — `~/.agents/skills` is mounted read-only
  into the sandbox (so bundled skill `scripts/` resolve); the `Read` batch still
  needs to confirm sandboxed reads of skill files work and aren't misled by the
  native `read_file` guidance.

The system-prompt override is maintained **per-batch inside R2**, alongside the skills
and profile updates.

## 6. Roadmap phases

### R0 — Catchup (maintenance)

- [x] Update `superpowers/skills/using-superpowers/references/zed-tools.md`: `terminal` ->
      `bash`; add `ask_user` (and the question-asking skills). Small; the profile
      already maps `terminal` -> `bash`.

### R1 — Prompts -> skills (DONE, 2026-09-01)

Prompts moved out of OpenCode agent definitions into a per-harness Zed layer, with
documented content-placement rules.

- [x] **Zed harness layer** under `harnesses/zed/`: `rules.md` (role-scoped dispatch
      table, symlinked to `~/.config/zed/AGENTS.md`) + Zed-only skills
      (`zed-primary-agent`, `zed-explorer`, `zed-implementer`, `zed-reviewer`), each
      with YAML front-matter, usable by a primary or a subagent.
- [x] **Install commands**: `tkt install-zed-agent` symlinks `harnesses/zed/skills/*`
      and the shared `superpowers/skills/*` into `~/.agents/skills`, plus `rules.md` ->
      `~/.config/zed/AGENTS.md`; `tkt install-opencode-agent` links
      `harnesses/opencode/agents` -> `~/.config/opencode/agents`. OpenCode `sp-*`
      agents copied under `harnesses/opencode/agents`; `sp-review` thinned.
- [x] **Deliverable: content-placement rules** documented in `harnesses/README.md`
      (what belongs in skills vs. `AGENTS.md` vs. OpenCode profiles vs. the Zed
      override).
- [x] **Sandbox**: `~/.config/opencode` and `~/.agents/skills` mounted read-only so
      bundled skill scripts resolve at the paths the skills expect.

R1 deviations from the original sketch: the primary agent is framed as a categorizer
that loads follow-up skills (gate = ask when intent is unclear), not a strict
"wait for a human-invoked gate"; subagent role prompts became general Zed skills
rather than strictly subagent-only; the optional skill renaming to
`design`/`plan`/`build` was not done.

### R2 — Iterative MCP tool batches

Each batch is one design -> plan -> build cycle that updates **all of**: the MCP server
tool(s), the skills references, the agent profile, and the Zed system-prompt override —
together. Expected order:

1. `Read` (resolve the `~/.agents/skills`/`$HOME` tension first). **DONE, 2026-09-01** —
   sandboxed `read` MCP tool, `Read a file -> `read`mapping in`zed-tools.md`and`zed-explorer`, native `read_file` disabled machine-side.
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
   `write_file`/`edit_file`); only _our_ MCP tools get Claude-style names.
4. **Add `TodoWrite`** as an MCP tool.
5. **`WebFetch` deferred** to the end (R4); use native `fetch` meanwhile.
6. **`ask_user` should be used** for the primary agent; add it to `zed-tools.md` and
   question-asking skills (it was overlooked in the prior Zed support work). — **Refined
   by E1**: keep encouraging it for the primary agent, but discourage/block it for
   _subagents_, which can call it too and where it works poorly. See §9.
7. **OpenCode support is retained**, coexisting with the Zed setup during testing.
8. **Prompts-first order:** move prompts to skills (R1) before expanding tools (R2).
9. **System-prompt override maintained per-batch** in R2, alongside skills + profile.
10. **Normalize content placement** with documented rules (skills vs. AGENTS.md vs.
    profiles vs. override).

## 8. Deferred / open items

- WebFetch security rationale and design (R4).
- `AGENTS.md.in` — confirm whether anything needs adapting (verify-only).
- Skill renaming to `design`/`plan`/`build` — dropped as optional; not done in R1.
- Task 14 (manual): remove the superseded `agents/opencode` directory now that
  `~/.config/opencode/agents` points at `harnesses/opencode/agents`.
- **`tests/test_pull.py` `forkpty()` deprecation warning** (maintenance): the suite
  emits `DeprecationWarning: this process is multi-threaded, use of forkpty() may
lead to deadlocks` from `pty.fork()` in
  `test_diverged_rebase_with_tty_editor_succeeds`. Pre-existing and harmless today
  (currently a single-threaded run), but worth addressing eventually — e.g. by
  running that test in a way that avoids `forkpty` on a multi-threaded process, or
  switching its mechanism.

## 9. Emergent work (added 2026-09-01)

Emergent items surfaced since the roadmap was approved. **None are scheduled for
action yet** — they are tracked here so they aren't lost. Each should be triaged into
its own design -> plan -> build cycle (or an investigation) before being acted on.

### E1 — Keep `ask_user` for the primary agent; discourage/block it in subagents (DONE, 2026-09-02)

`ask_user` is wanted and encouraged for the **primary agent**. The problem is that
**subagents** (dispatched via `spawn_agent`) can also call it, and it does not work well
in that context (interrupts their flow / renders poorly). This _refines_ — but does not
reverse — design decision #6: primary agents keep encouragement, subagents should not
use it.

**Feasibility finding (2026-09-02): blocking is not possible in current Zed.** Tool
availability is purely profile-driven (`Thread::enabled_tools` filters by the active
profile's `tools` allowlist), and subagents inherit the parent's `profile_id` via
`inherit_parent_settings` — there is no `subagent_profile` setting (only
`subagent_model`). `enabled_tools` never special-cases `subagent_context`, so a subagent
sees exactly the tools its inherited profile enables, `ask_user` included. So the
"block it" option is out; we implement the fallback — discourage at the prompt/skill
level.

- **Skills** (`zed-explorer` — when used as a subagent — and `zed-implementer`,
  `zed-reviewer`) now instruct subagents not to call `ask_user`; surface
  uncertainty/blockers in their report instead. `zed-explorer` is also usable by
  the primary agent, where `ask_user` remains available.
- **`zed-tools.md`** updated: `ask_user` is marked primary-agent-only, with a note
  explaining it can't be blocked but is discouraged in subagents.
- **Tool-suite table (see §4)** updated to reflect the primary-vs-subagent split.

### E2 — Compaction: OpenCode vs Zed

OpenCode compacts (summarizes) conversation history differently from Zed. Compare the
two approaches and, if possible, improve Zed's compaction based on what we learn from
OpenCode. Research/design first — no code changes yet.

### E3 — Long-running model-degradation tracing proxy (DONE, 2026-09-02)

Observed model degradation with the Zed agent that isn't seen with OpenCode: incorrect
use (or non-use) of thinking tags and repeated tool-use mistakes. A prior investigation
into this was inconclusive but produced a debugging proxy server for tracing the API
calls. The throwaway proxy is now productized into two continuously-running commands so
it can gather a large, session-linked dataset for a future investigation.

- **`tkt trace-proxy`** — long-lived capture. Relays model HTTP traffic through a
  sandboxed-forwarding proxy (optionally co-invoked over SSH to the model host) into the
  data root, masking the `Authorization` header (case-insensitive) to `<redacted>`.
- **`tkt trace-log`** — retroactive session segmentation and labeling: `segment` splits
  the flat capture into conversation sessions (by OpenCode `x-session-id` header, or by
  content-connected components for Zed, which sends no session header and may interleave a
  primary conversation with empty-context `spawn_agent` subagents that are split into
  their own sessions); plus `list`, `show`, `pin`/`unpin`, and `prune`.
- **Data layout** — `~/.tkt/traces/` (override with `TKT_TRACES_DIR`): `capture.jsonl`
  for the live stream, `sessions/<date>_<id>.jsonl.gz` per session, and paired
  `*.meta.json` (label, id, start, end, client user-agent, pinned).
- **Labeling** — sessions are labeled from the generated title (title-gen request at
  session start), falling back to the session id / start time. **Pin/prune** — pinned
  sessions are never pruned; `prune` removes old/extra unpinned sessions (default 30-day
  horizon, keep 20).

This supersedes the throwaway `investigations/bad-thinking/zed-agent-request-proxy/`
prototype (left in place). Run it during normal Zed-agent use to accumulate data for the
future degradation investigation.

### E4 — `read` tool: byte-level output cap (surfaced during bash truncation)

Surfaced while implementing the `bash` MCP tool's output truncation (2026-09-02). The
`bash` fix caps each stream head+tail at 5000 chars on the host and hard-kills runaway
output at the source (50,000-byte `head -c` pipe). The built-in `read` tool was
**deliberately left untouched** by that work, but it shares the same class of bug in
narrower form.

`read` truncates by **line count**, not bytes
(`build_read_command` runs `sed -n "{start},{end}p" "$f" | base64 -w0`, and
`read_tool` caps at `limit` lines, default 2000). For a file with one **extremely long
line** (a multi-GB single-line file — minified JS, a huge JSONL record, a log with no
newlines), the line-based truncation does not bound bytes: `sed` buffers the whole line
in memory (OOM risk) and `base64` ships it up unmodified. This is the same
OOM/server-hang risk the `bash` truncation fixes.

- Add a byte-level cap to `read` — e.g. a `head -c` bound inside `build_read_command`,
  and/or capping the base64 line before host-side decode — so a huge single-line file
  cannot blow the context window or OOM the server.
- Triage into its own design -> plan -> build cycle before implementation.
