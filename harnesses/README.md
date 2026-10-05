# Harness Specializations

This directory holds per-harness and cross-harness agent/prompt content for
tkt. Each harness layer is separate because prompts and prompt placement
differ by harness.

```
harnesses/
  shared/skills/<name>/       tkt-authored, cross-harness per-phase skills
                              (symlinked into ~/.agents/skills for Zed by
                              `tkt install-zed-agent`; registered via the
                              `skills` array in opencode.jsonc for OpenCode)
  opencode/agents/            OpenCode agent shells: superpowers sp-* plus
                              standalone primary agents (symlinked to
                              ~/.config/opencode/agents)
  zed/rules.md                Zed global AGENTS.md content — role-scoped dispatch
                              table (symlinked to ~/.config/zed/AGENTS.md)
  zed/skills/<name>/          Zed-only skills (symlinked to ~/.agents/skills/<name>)
```

## Content-placement rules

- **Per-phase how-to (brainstorming, writing-plans, subagent-driven-development,
  systematic-debugging)** — shared superpowers skills
- **New tkt-authored cross-harness workflows (e.g. `reviewing-code`,
  `responding-to-pr-review`)** — `harnesses/shared/skills/`; the superpowers
  submodule holds **only** modifications to upstream-shipped skills
- **OpenCode subagent templates (implementer-prompt, task-reviewer-prompt,
  re-review-prompt, code-reviewer)** — superpowers templates
- **Zed role prompts** — Zed-only skills (`zed-explorer`, `zed-implementer`,
  `zed-primary-agent`), usable by a primary or a subagent
- **Primary phase orchestration + gate signal** — per-harness: OpenCode
  `sp-*.md` shells; Zed `rules.md` + `zed-primary-agent`
- **Tool mapping / subagent names / permissions** — harness layer
  (`opencode-tools.md` / `zed-tools.md`)
- **Project facts for all agents incl. subagents** — project `AGENTS.md`

Guidelines:

- Per-phase _how-to_ lives in shared skills, never in a harness shell. A
  shell only says which skill to load and the harness-specific gate
  mechanism.
- Cross-harness skills stay self-contained: no relative links to other skill
  roots (Zed symlinks each skill individually into `~/.agents/skills`;
  OpenCode skill roots are separate). Harness variance is inline prose.
- Zed _role_ prompts are Zed-only skills, framed for use by a primary or a
  subagent (Zed's `spawn_agent` has no built-in role prompt); OpenCode keeps
  its own templates/`sp-review`.
- Primary _gate orchestration_ is per-harness, written once, concretely — do
  not split it into a shared "conceptual" copy plus harness "concrete" copies.
- `using-superpowers` stays purely about skill discovery.
