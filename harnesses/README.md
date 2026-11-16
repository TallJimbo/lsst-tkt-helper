# Harness Specializations

This directory holds per-harness and cross-harness agent/prompt content for
tkt. Each harness layer is separate because prompts and prompt placement
differ by harness. `tkt install-harness` installs everything by **copying**
(no symlinks: OpenCode's live reload ignores symlinked roots, and copies mean
every content kind updates the same way); re-run it after editing a source or
updating the `superpowers/` submodule.

```
harnesses/
  shared/AGENTS.md            Cross-harness global agent rules — concatenated
                              ahead of each harness's own file when
                              `tkt install-harness` writes that harness's
                              global AGENTS.md
  shared/skills/<name>/       Cross-harness skills — tkt-authored workflows
                              plus vendored Plannotator skills (see
                              "Vendored skills" below). Copied into
                              ~/.agents/skills, which Zed and OpenCode both
                              discover; installed by `tkt install-harness`
  opencode/AGENTS.md          OpenCode-specific global rules, concatenated
                              after shared/AGENTS.md into
                              ~/.config/opencode/AGENTS.md
  opencode/agents/            OpenCode agent shells: superpowers sp-*,
                              standalone primary agents, and plan.md/build.md
                              same-stem merges into the built-in Plan/Build
                              agents (copied into ~/.config/opencode/agents)
  zed/rules.md                Zed global AGENTS.md content — role-scoped dispatch
                              table (concatenated after shared/AGENTS.md into
                              ~/.config/zed/AGENTS.md)
  zed/skills/<name>/          Zed-only skills (copied into
                              ~/.agents/skills/<name>; OpenCode hides them via
                              a `zed-*` skill permission deny in opencode.jsonc
                              — Zed has no other global skills directory, so
                              physical separation is impossible)
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
- **Built-in agent (Plan/Build) permission parity and skill gating** —
  same-stem markdown in `harnesses/opencode/agents/`: frontmatter
  `permissions` append to the built-in ruleset (last match wins) and an
  empty body keeps the built-in system prompt
- **Project facts for all agents incl. subagents** — project `AGENTS.md`
- **Cross-harness global agent rules** — `harnesses/shared/AGENTS.md`, concatenated
  ahead of the harness's own file (`harnesses/zed/rules.md`,
  `harnesses/opencode/AGENTS.md`) into that harness's global `AGENTS.md`. The
  installer writes a generated file, not a symlink, so edit a source and re-run
  `tkt install-harness`.

## Vendored skills

`plannotator`, `plannotator-annotate`, and `plannotator-review` are verbatim
copies of skills installed by the Plannotator CLI, adopted here because the
sandbox-bridged `plannotator_open` session tool makes them usable in restricted
runs. Plannotator's other skills are host-only (they shell out to the
`plannotator` CLI or read host data directories) and stay out of tkt. The
vendored `SKILL.md` files stay verbatim upstream text and `tkt install-harness`
installs them unchanged; tkt's own guidance for reaching the session tools from
a sandbox (the code-mode call form, the read-the-file result contract, the
inbox tool list) lives in `tkt/AGENTS.md.in`, where tkt owns the text. To
update: run Plannotator's installer into a scratch directory (or read its
upstream source), copy the three skill directories over these, and commit.

Plannotator's installer writes the same skills into `~/.agents/skills` and
reinstalls them on every upgrade; `tkt install-harness` overwrites them with
tkt's vendored copies. Plannotator skills tkt deliberately excludes
are listed in `PLANNOTATOR_HOST_ONLY_SKILLS`: install removes them from
`~/.agents/skills` and sandboxed agents deny them outright
(`tkt.sandbox.opencode_config_content`), so a Plannotator upgrade cannot put
them back in an agent's view. Any other unrecognized skill in
`~/.agents/skills` is warned about and left in place — decide by reading it
whether it belongs vendored, in `PLANNOTATOR_HOST_ONLY_SKILLS`, or nowhere.

Guidelines:

- Per-phase _how-to_ lives in shared skills, never in a harness shell. A
  shell only says which skill to load and the harness-specific gate
  mechanism.
- Cross-harness skills stay self-contained: no relative links to other skill
  roots (both harnesses discover flat `~/.agents/skills`; each skill must
  stand alone). Harness variance is inline prose.
- Zed _role_ prompts are Zed-only skills, framed for use by a primary or a
  subagent (Zed's `spawn_agent` has no built-in role prompt); OpenCode keeps
  its own templates/`sp-review`.
- Primary _gate orchestration_ is per-harness, written once, concretely — do
  not split it into a shared "conceptual" copy plus harness "concrete" copies.
- `using-superpowers` stays purely about skill discovery.
