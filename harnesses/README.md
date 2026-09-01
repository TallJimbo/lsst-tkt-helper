# Harness Specializations

This directory holds per-harness agent/prompt content for tkt. Each harness
layer is separate because prompts and prompt placement differ by harness.

```
harnesses/
  opencode/agents/          OpenCode sp-* agent shells (symlinked to
                            ~/.config/opencode/agents)
  zed/rules.md              Zed global AGENTS.md content — role-scoped dispatch
                            table (symlinked to ~/.config/zed/AGENTS.md)
  zed/skills/<name>/        Zed-only skills (symlinked to ~/.agents/skills/<name>)
```

## Content-placement rules

- **Per-phase how-to (brainstorming, writing-plans, subagent-driven-development,
  systematic-debugging)** — shared superpowers skills
- **OpenCode subagent templates (implementer-prompt, task-reviewer-prompt,
  re-review-prompt, code-reviewer)** — superpowers templates
- **Zed role prompts** — Zed-only skills (`zed-explorer`, `zed-implementer`,
  `zed-reviewer`, `zed-primary-agent`), usable by a primary or a subagent
- **Primary phase orchestration + gate signal** — per-harness: OpenCode
  `sp-*.md` shells; Zed `rules.md` + `zed-primary-agent`
- **Tool mapping / subagent names / permissions** — harness layer
  (`opencode-tools.md` / `zed-tools.md`)
- **Project facts for all agents incl. subagents** — project `AGENTS.md`

Guidelines:

- Per-phase _how-to_ lives in shared superpowers skills, never in a harness
  shell. A shell only says which skill to load and the harness-specific gate
  mechanism.
- Zed _role_ prompts are Zed-only skills, framed for use by a primary or a
  subagent (Zed's `spawn_agent` has no built-in role prompt); OpenCode keeps its
  own templates/`sp-review`.
- Primary _gate orchestration_ is per-harness, written once, concretely — do
  not split it into a shared "conceptual" copy plus harness "concrete" copies.
- `using-superpowers` stays purely about skill discovery.
