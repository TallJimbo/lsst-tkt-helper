# R1 — Prompts → Skills (Zed native-agent harness) — Design Handover

**Date:** 2026-09-01
**Status:** Approved by human in conversation (before implementation).
**Implements:** phase R1 of `docs/zed-agent-roadmap.md`.

## Goal

Move agent *prompt content* out of OpenCode-only homes and into a Zed-native
harness, and produce documented **content-placement rules**. Concretely: build
the Zed harness layer (a role-scoped dispatch table + Zed-only skills), thin the
OpenCode `sp-review` shell, restructure the repo under a new `harnesses/`
directory, and add two `tkt` install subcommands. OpenCode keeps working
throughout (Goal 4 of the roadmap).

The broader roadmap drives this: OpenCode → Zed's native agent, with the tkt MCP
server (sandboxed `bash`, later `Read`/`Grep`/`Glob`/`LS`/`TodoWrite`) as the
agent's read/execute surface and Zed's native tools (`write_file`/`edit_file`)
for UI-diff editing.

## Architecture (the model that resolves the roadmap's open questions)

The roadmap offered a false binary for the *primary-agent prompts*: "subsume into
the global Zed AGENTS.md **or** `using-superpowers`." Tradeoff exploration
resolved it differently:

- **Primary gate orchestration is per-harness, not shared.** The phase
  *sequence* and *how a gate is signaled* depend on the harness's agent model
  (OpenCode: switch agents; Zed: human invokes a skill). Putting it once as a
  "conceptual" block in a shared skill and again "concretely" per-harness is
  duplicative. So each harness gets **one concrete description in one place**.
- **Subagent role prompts are Zed-only skills.** OpenCode already has its
  subagent mechanism (built-in `explore` prompt + `general` perms + the
  superpowers templates it pastes). Zed's `spawn_agent` has **no built-in role
  prompt and no permission profile** — that's the gap. So the role prompts
  become **Zed-only skills**, free to reference Zed-specific tools. *(Human
  deviation post-build: these are framed as general Zed skills usable by a
  primary or a subagent — see the deviation note below.)*
- **`using-superpowers` stays purely about skill discovery** — no gate content
  added. The per-phase how-to skills (brainstorming, writing-plans,
  subagent-driven-development, systematic-debugging) are unchanged.

### Content-placement rules (the R1 deliverable)

| Content | Home |
| --- | --- |
| Per-phase how-to (brainstorming, writing-plans, subagent-driven-development, systematic-debugging) | shared superpowers skills (unchanged) |
| OpenCode subagent templates (implementer-prompt, task-reviewer-prompt, re-review-prompt, code-reviewer) | superpowers templates (unchanged — self-contained, pasted) |
| Zed subagent role prompts | **Zed-only skills** (`zed-explorer`, `zed-implementer`, `zed-reviewer`) — now general Zed skills usable by primary or subagent (human deviation) |
| Primary phase orchestration + gate signal | **per-harness**: OpenCode `sp-*.md` shells (unchanged); Zed `rules.md` + `zed-primary-agent` |
| Tool mapping / subagent names / permissions | harness layer (`opencode-tools.md` / `zed-tools.md`) — unchanged |
| Project facts for all agents incl. subagents | project `AGENTS.md` |
| Zed system-prompt tool mitigation | Zed system-prompt override (R2, separate — out of R1 scope) |

### Why Zed's global AGENTS.md (not per-project) carries the dispatch table

Zed's global AGENTS.md is read by both primaries and subagents. Rather than a
reason to avoid it for primary-only content, this makes it the natural home for a
**role-scoped dispatch table**: each row is conditional on the reader's role
("if you are a primary…", "if you are a subagent asked to do Y…"), so readers
filter by role and nothing primary-only leaks harmfully. Subagents also
self-select their role skill from the table, so a primary need not enumerate
skill-loading in its dispatch — reducing the primary's prompting load.

## File layout

```
harnesses/
  opencode/
    agents/                       <- copy of agents/opencode (original kept until
                                     install verified, then removed)
      sp-design.md                (unchanged)
      sp-plan.md                  (unchanged)
      sp-build.md                 (unchanged)
      sp-debug.md                 (unchanged)
      sp-review.md                (THINNED — see verbatim below)
  zed/
    rules.md                      -> symlink to ~/.config/zed/AGENTS.md
    skills/
      zed-primary-agent/SKILL.md
      zed-explorer/SKILL.md
      zed-implementer/SKILL.md
      zed-reviewer/SKILL.md
  README.md                       <- content-placement rules deliverable
```

The original `agents/opencode/` is **copied** (not moved) to
`harnesses/opencode/agents` and kept in place through the build so the current
OpenCode setup keeps working; it is removed only after the human has verified
`install-opencode-agent` from the new location (safety against breaking the
working harness mid-build).

Symlinks (created by `tkt install-*`, absolute targets):
- `harnesses/zed/skills/<name>` → `~/.agents/skills/<name>`
- `harnesses/zed/rules.md` → `~/.config/zed/AGENTS.md`
- `harnesses/opencode/agents` → `~/.config/opencode/agents` (re-pointed)

## Zed harness layer content

Each Zed-only `SKILL.md` starts with YAML front-matter (`name` = directory
name, `description`), as the shared superpowers skills do — Zed's skill loader
requires it (`parse_skill_frontmatter` bails without a leading `---` block
containing `name` + `description`). Keep descriptions concise and every line
≤110 chars.

### Deviations applied by the human (post-build, before install)

After the R1 build the human hand-revised the Zed harness content. The docs
below describe the ORIGINAL design; this note records what changed so the
handoff matches reality (code in `harnesses/zed/` is source of truth):

- **Reframing.** The zed skills are now framed as general Zed skills usable by a
  primary OR a subagent, not strictly "Zed-only subagent role prompts". Titles
  are generic (`# Zed Primary Agent`, `# Exploring Code`, `# Implementing a code
  change`, `# Reviewing code`). Only `zed-explorer` fully generalizes (added
  "Subagent delegation": subagent does the work itself; primary delegates
  aggressively); `zed-implementer`/`zed-reviewer` keep subagent dispatch
  contracts in their bodies.
- **Primary agent flow.** `zed-primary-agent` no longer strictly waits for a
  human-invoked phase gate. It first **categorizes the request** (simple question
  → answer directly; debug → `systematic-debugging`; feature/refactor/bugfix →
  `brainstorming`; question about code → `zed-explorer`), and the gate rule
  became "ask when intent is unclear" rather than "never advance on your own".
  `rules.md`'s PRIMARY section simplified to "load `zed-primary-agent` and
  follow its instructions" (the detailed gate list moved into the skill).
- **Review phase.** Removed from the surfaced primary flow (design/plan/build
  only); review still happens inside `subagent-driven-development`.
- **`zed-explorer` additions.** New "External code" (installed deps, GitHub MCP
  only) and "Subagent delegation" sections; probe scratch is `./.agent` if it
  exists else `/tmp`; read-only extends to documentation.
- **rules.md SUBAGENT section** dropped the trailing "You are a subagent: do the
  task you were given. You do not drive the change flow." guardrail.

### `harnesses/zed/rules.md` (Zed global AGENTS.md) — role-scoped dispatch table

```markdown
# Zed Agent Dispatch Table

Read by every agent (primary and subagent) at session start. Load the skill
indicated by your role and the task you were given.

## If you are a PRIMARY agent

You drive the superpowers change flow. The human signals each gate by invoking
the next skill; do not advance on your own. Load `zed-primary-agent` for the
full flow.

- design  → load `brainstorming`
- plan    → load `writing-plans`
- build   → load `subagent-driven-development`
- debug   → load `systematic-debugging`
- review  → load `requesting-code-review`

When a phase's work is done, STOP and report; wait for the human to invoke the
next skill.

## If you are a SUBAGENT (dispatched via spawn_agent)

Load the skill matching the task you were asked to do:

- explore the codebase / find files / answer "how does X work" → `zed-explorer`
- implement a task (brief + report file) → `zed-implementer`
- review a task's diff            → `zed-reviewer` (scope: task)
- re-review a fix round           → `zed-reviewer` (scope: re-review)
- final whole-branch review       → `zed-reviewer` (scope: final)

You are a subagent: do the task you were given. You do not drive the change
flow.
```

### `zed-primary-agent/SKILL.md` — the non-linear primary flow

The flow is **not** a strict pipeline. Content must convey:

- Common path: `design → plan → build → review`.
- `debug` is an **optional start phase**, not usually part of the main flow.
- `plan` may be skipped for small changes; `build` may never happen for
  design-documentation work.
- `plan` or `build` may reveal a design problem and loop **back** to `design`.
- The **gate rule**: the human signals which phase to do next (by invoking the
  next skill); the agent does not advance, skip, or loop on its own. The
  wait-for-signal rule is what makes the non-linear flow safe.

### `zed-explorer/SKILL.md` — investigation + investigative coding

Splits from OpenCode's read-only `explore`: because the security model is
sandbox-oriented (read vs. run is not a security boundary), the explorer may run
or probe throwaway code in the sandbox to learn what it does. Used in the
design/plan/debug phases (covers OpenCode `general`'s exploratory role). Content:
file-search/glob/grep/read discipline, thoroughness levels, report findings, do
not modify production code, may write throwaway scratch in the sandbox.

### `zed-implementer/SKILL.md` — build-phase SDD contract role

The build-phase implementer (OpenCode `general` in build). Content: read the
task brief file; write the report file; implement, test, commit; escalation
statuses DONE / DONE_WITH_CONCERNS / BLOCKED / NEEDS_CONTEXT; never dispatch
subagents; self-review before reporting.

### `zed-reviewer/SKILL.md` — one skill, shared core + scopes

Reconciles `sp-review` + `task-reviewer-prompt` + `re-review-prompt` +
`code-reviewer` into one Zed skill. Shared core written once; three scope
sections.

```markdown
# zed-reviewer (Zed-only)

You are an independent, read-only reviewer. You never modify files and never
dispatch subagents.

## Shared discipline
- Treat the implementer's report as unverified claims; verify against the diff.
- Cite file:line for every finding; acknowledge strengths before issues.
- Calibrate severity: Critical / Important / Minor (not everything is Critical).
- Final message is the report itself — verdicts and file:line, no narration.

## Scope: task
[spec compliance + task quality; brief, report, diff; ⚠️ items; verdicts]

## Scope: re-review
[verdict each finding ADDRESSED/NOT ADDRESSED; new breakage in fix diff;
out-of-scope observations]

## Scope: final
[whole-branch vs plan; merge readiness]
```

## OpenCode change

Only `sp-review` is thinned (removes discipline restated by the templates the
controller passes). The other `sp-*.md` shells and the superpowers templates are
**unchanged** (coexistence; their eventual trimming is noted in the rules doc).

```markdown
# sp-review (OpenCode) — thinned
---
# frontmatter: mode: subagent; read/glob/grep/list/bash: allow;
# edit/task/skill/webfetch/websearch: deny (unchanged — structural read-only)
---

You are an independent, read-only reviewer. You never modify files.

The controller passes you a filled review template (task / re-review / final
whole-branch) plus the review package, brief, and report file paths. Follow
that template exactly and report your findings clearly. Do not dispatch
subagents or load skills.
```

## Install commands

`tkt install-zed-agent` and `tkt install-opencode-agent` — standalone click
commands (like `fix-openspec`), backed by a new `tkt/install.py`. They write
only `$HOME` symlinks; they do **not** edit `local.json`.

- **Absolute** symlink targets.
- Idempotent: skip if already correct; replace if pointing elsewhere.
- `--dry-run`: report without changing.
- **Stale cleanup:** after (re)creating managed entries, scan the target dir for
  entries that are not being (re)created by this command, **warn** about each,
  and (unless `--dry-run`) offer to remove them (interactive confirm), keeping the
  dirs clean under renames.
- Testable: functions accept an injectable `home` root and `confirm` callable.

`install-zed-agent` creates `~/.agents/skills/` and `~/.config/zed/`, links each
`harnesses/zed/skills/<name>` and `harnesses/zed/rules.md`, and also links each
shared superpowers skill (`superpowers/skills/<name>`) into `~/.agents/skills/`
so Zed can load them (skipped gracefully when `superpowers/skills` is absent).
`install-opencode-agent` links `harnesses/opencode/agents` → `~/.config/opencode/agents`.

Repo root located via `os.path.dirname(os.path.dirname(__file__))` (the
established pattern for non-`.py` assets; repo root = parent of the `tkt/`
package).

## Sandbox (`local.json`) change

- `~/.config/opencode` moves from `mounts.rw` → `mounts.ro`. It was rw from
  before the OpenCode agents were version-controlled here; they are now, so
  read-only suffices.
- `~/.agents/skills` is added to `mounts.ro` (read-only). The shared
  superpowers skills (and their bundled `scripts/`) are written from the
  perspective that the skill's installed directory is visible to the agent's
  shell. The `tkt install-zed-agent` symlinks under `~/.agents/skills` resolve
  into the `ro`-mounted `tkt2` tree, so a sandboxed tool-call reaches
  `~/.agents/skills/<skill>/scripts/...` exactly as the skills expect. This
  supersedes the earlier "no `~/.agents` mount" decision: Zed still loads
  skills/rules natively outside the sandbox, but the installed skill
  directories (incl. bundled scripts) are now `ro`-visible inside it.
- **No** `~/.config/zed` mount. Zed reads `~/.config/zed/AGENTS.md` natively
  (outside the sandbox); no sandboxed tool-call needs it.
  This is validated by the human test task (see plan) rather than assumed.

## Key decisions log

1. **Primary gate orchestration per-harness** (OpenCode shells / Zed `rules.md` + `zed-primary-agent`), not a shared conceptual block — avoids duplication.
2. **Subagent role prompts are Zed-only skills** (Zed `spawn_agent` has no built-in), free to reference Zed tools.
3. **Zed global AGENTS.md = role-scoped dispatch table** (seen by primaries and subagents; role conditionals prevent harmful leakage; subagents self-select their role skill).
4. **`using-superpowers` stays skill-discovery only**; no gate content.
5. **Zed-only skills split by contract, not read/write**: `zed-explorer` (investigation + investigative coding) vs `zed-implementer` (SDD build contract). Sandbox-oriented security makes read/run a non-issue.
6. **One `zed-reviewer`** with shared core + task/re-review/final scopes, reconciling sp-review + 3 templates.
7. **OpenCode unchanged except `sp-review` thinned** (coexistence).
8. **Install as `tkt` subcommands**, not a shell script — absolute symlinks, idempotent, `--dry-run`, warn+offer stale cleanup.
9. **No `~/.agents`/`~/.config/zed` sandbox mounts**; only `~/.config/opencode` rw→ro.
10. **Content-placement rules doc at `harnesses/README.md`.**
11. **Copy-then-cleanup for the OpenCode agents dir:** the original `agents/opencode` is copied (not moved) and kept until the human verifies `install-opencode-agent` from the new location, then removed — avoids breaking the working harness mid-build.

## Open items / assumptions (verified by human in plan)

- Whether superpowers *shared* skills are reachable by the Zed agent (e.g. Zed
  `skills.paths` → `tkt2/superpowers/skills`, mirroring opencode.jsonc) so their
  `scripts/*` resolve to a `tkt2` (mounted) path — tested by the human task
  ("Zed agent resolves a superpowers script").
- `~/.config/zed/AGENTS.md` is the global-prompt mechanism (name chosen to avoid
  project/directory-level `AGENTS.md` interpretation).
- `~/.config/opencode` is safe to make read-only (verified by OpenCode coexistence test).
