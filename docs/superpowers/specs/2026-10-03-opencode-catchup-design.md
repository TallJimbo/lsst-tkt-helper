# OpenCode catch-up: shared harness layer, review/PR-response agents, GitHub MCP

**Date:** 2026-10-03
**Status:** Approved (Gate 1) — design reviewed in conversation
**Designs from:** git-history scan of `5c9db89..HEAD` (Zed-side work since 2026-09-01)

## 1. Context & motivation

Since `5c9db89` most workflow work landed in the Zed harness: a reworked
primary-driven review skill (`zed-reviewer`, 2dffe96), a PR-response workflow
with a compaction-resilient ledger (`zed-pr-responder`, ff56ef2 + b707dae),
behavioral guidance, and the sandboxed MCP tool suite. OpenCode kept only its
`sp-*` phase shells (spec-writing move cb70035, docs-worktree paths 3ad3f0d,
v2 agent form bcdcf36).

This project closes the gap:

1. Port the **PR-response workflow** and the **primary-driven review flow** to
   OpenCode as custom **primary agents** (not skills, not `sp-*`).
2. Introduce a **shared harness skill layer** under `harnesses/` for tkt-authored
   cross-harness workflows; the superpowers submodule thereafter holds **only**
   modifications to upstream-shipped skills.
3. Wire **GitHub MCP** into OpenCode with a read-only per-tool allow-list
   mirroring the existing Zed configuration.
4. Fix the visual-companion wording in `tkt/AGENTS.md.in` (it brands OpenCode's
   normal path as "legacy").

Explicitly out of scope: the tkt MCP server (stays Zed-only), trace-proxy rules
for OpenCode, hiding `~/.agents/skills` from OpenCode discovery, and any
tkt-side management of `~/.config/opencode/opencode.jsonc`.

## 2. Decisions made (conversation log)

| # | Decision | Choice & rationale |
|---|----------|--------------------|
| 1 | Where per-phase how-to for the new workflows lives | New `harnesses/shared/skills/` layer (user decision). Superpowers fork restricted to modifications of upstream-shipped skills only. |
| 2 | Structure | Shared skills + thin OpenCode shells + **deleted** Zed wrappers (option "A"); single copy of the ledger/review discipline, per `harnesses/README.md` placement rules. |
| 3 | Skill self-containment | Skills avoid relative links across skill roots (Zed symlinks skills individually into `~/.agents/skills`; OpenCode roots are separate). Harness variance is inline prose, like `brainstorming` already does. |
| 4 | Zed wrappers | `zed-reviewer` and `zed-pr-responder` are **deleted** (all content moves to shared skills); `zed-primary-agent` dispatch re-points to the shared skill names. |
| 5 | OpenCode agent naming | No `sp-` prefix (not superpowers): **`code-review`** and **`pr-responder`**. |
| 6 | GitHub access mechanism | GitHub **MCP server** configured in OpenCode (user decision; supersedes the initial `gh`-CLI proposal). Server name must be `github` so tools are exposed as `github_<tool>`. |
| 7 | GitHub tool scoping | Global `opencode.jsonc`: single default-deny rule `github_*` → deny (stricter than the literal Zed list: unlisted and future upstream tools are denied until explicitly allowed). The 20-tool read allow-list lives **in the two agents' frontmatter**, not globally. |
| 8 | `code-review` GitHub access | Carries the same read allow-list, preserving zed-reviewer's full PR-review mode (fetch description, base..head diff, review threads). |
| 9 | Question-tool advice (item 4) | **Skipped** — the Zed `ask_user` rules were specific to Zed particulars. |
| 10 | Harness-bug reporting nudge (item 5) | **Skipped** — OpenCode is a mature harness used as-is; Zed's was customized extensively. |
| 11 | Visual-companion wording (item 3) | **Yes** — reword `tkt/AGENTS.md.in` so self-launching `start-server.sh` is the normal route for harnesses without `brainstorm_server`, not "legacy". |
| 12 | Leftovers | Remove empty untracked `harnesses/zed/skills/zed-shared-worktree-review/`. |

## 3. Target layout

```
harnesses/
  shared/skills/<name>/SKILL.md    # NEW: tkt-authored, cross-harness per-phase how-to
  opencode/agents/sp-*.md          # superpowers phase shells (unchanged)
  opencode/agents/code-review.md   # NEW primary agent (no sp- prefix)
  opencode/agents/pr-responder.md  # NEW primary agent (no sp- prefix)
  zed/rules.md                     # Zed dispatch table (unchanged)
  zed/skills/<name>/               # Zed-only role prompts (reviewer/pr-responder deleted)
superpowers/                       # submodule: ONLY mods to upstream-shipped skills
```

`harnesses/README.md` gains the placement rule: **new tkt-authored workflows go
in `harnesses/shared/skills/`; the superpowers fork holds only modifications to
upstream-shipped skills.** Existing fork content (e.g.
`using-superpowers/references/{zed,opencode}-tools.md`, brainstorming tweaks)
stays where it is.

## 4. The two shared skills

Content source of truth: the current Zed skills, ported largely verbatim with
harness-variance rewritten as inline, tool-neutral prose.

- `harnesses/shared/skills/reviewing-code/SKILL.md` ← `harnesses/zed/skills/zed-reviewer/SKILL.md`
- `harnesses/shared/skills/responding-to-pr-review/SKILL.md` ← `harnesses/zed/skills/zed-pr-responder/SKILL.md`

Discipline that must survive the port (do not paraphrase away):

- **reviewing-code**: mode selection (code review vs somebody's PR) with
  confirmation when ambiguous; scope pinning restated in one line
  (`git merge-base` for branches, `BASE_SHA..HEAD_SHA`, working tree, or PR
  number); STOP-and-ask when GitHub reads are unavailable rather than inferring
  from branch names/local diffs; verify-against-code for every finding;
  `file:line` citations; strengths before issues; Critical/Important/Minor
  calibration; batched markdown tables (`Severity | Finding | File:line`),
  Critical first; question-tool pause after each batch; the review is the
  deliverable — no edits, no posted comments, no drafted replies, hand off
  fixes to the design/build flow or to the PR-response workflow.
- **responding-to-pr-review**: ledger file `pr-response-<owner>-<repo>-PR<number>.md`
  at the root of the repo holding the PR branch; identity first line naming PR
  URL + branch name (not SHA); append-only event lines (`cat:`, decision,
  fixup, batch); commits identified subject-first/hash-second (rebases churn
  hashes; resolve subjects via `git log --grep`); trust ledger + `git log` over
  recollection after compaction; resume at first unsettled item; foreign-PR
  ledger left in place; categorize-first (`A1…` addressed / `N1…` needs-work,
  incl. contradicted-promise detection → follow-up reply, not a fixup); present
  the full categorized list before acting; per-item walk-through with per-comment
  decisions (fix now / defer / explain only / disagree); `fixup! <subject>` per
  small comment targeting the introducing commit, quick lint/tests check per
  fixup; larger changes discussed and landed as ordinary commits; never a
  destructive rebase unsolicited — remind, don't autosquash; batched
  `comment → action/rationale` table ending in a human confirmation pass (the
  human posts every reply).

Harness-variance sections inside each skill (short, inline):

- *Question tool*: "your harness's structured-question tool — Zed `ask_user`
  with `allow_free_text: true`; OpenCode `question` (free-text is automatic)."
- *GitHub access*: "Zed: verify read-only GitHub MCP tools; STOP and ask the
  human if absent. OpenCode: GitHub tools appear only for allow-listed agents
  (`code-review`, `pr-responder`); STOP and ask if absent. Inside a
  network-restricted tkt sandbox the remote GitHub MCP is unreachable — run the
  PR flows in a host OpenCode session or `tkt sandbox-run --network`."
- *Path links* (pr-responder): keep the Zed guidance about absolute-path link
  targets in chat (multi-repo tkt workspaces don't resolve workspace-relative
  links); phrase for OpenCode chat rendering too (OpenCode does not auto-link
  bare paths — use a link or backticked path).

Frontmatter of both skills follows the standard `name` + `description` form
(Zed discovers them via the `~/.agents/skills` symlink farm; OpenCode via
`skills.paths`, which already includes tkt2-repo roots).

## 5. Zed-side changes

- Delete `harnesses/zed/skills/zed-reviewer/` and
  `harnesses/zed/skills/zed-pr-responder/`.
- `harnesses/zed/skills/zed-primary-agent/SKILL.md` dispatch bullets:
  `zed-pr-responder` → `responding-to-pr-review`, `zed-reviewer` →
  `reviewing-code`. `harnesses/zed/skills/` ends up with only the three role
  prompts (`zed-explorer`, `zed-implementer`, `zed-primary-agent`).
- `rmdir harnesses/zed/skills/zed-shared-worktree-review/` (empty, untracked).
- The "primary-only" framing of the review flow moves into the shared skill's
  own wording ("you are the primary agent acting as a reviewer"; Zed's
  `zed-shared-worktree-review` and subagent dispatch are untouched).

## 6. Install wiring (`tkt/install.py`)

`install_zed_agent` currently links skill dirs from `harnesses/zed/skills` and
`superpowers/skills` into `~/.agents/skills/<name>`. Change: three sources,
name-collision warn-and-skip, factored helper.

```python
_SKILL_SOURCES = (
    ("harnesses", "zed", "skills"),
    ("harnesses", "shared", "skills"),
    ("superpowers", "skills"),
)

def _link_skill_dirs(skills_src: str, skills_dst: str, managed: set[str], *, dry_run: bool) -> None:
    """Symlink each skill dir (containing SKILL.md) under skills_src into skills_dst.

    Names already in ``managed`` (linked from an earlier source) are skipped
    with a warning. Shared by install_zed_agent's three source roots.
    """
```

Precedence note: `zed` first so a Zed-only skill name keeps priority over a
shared skill of the same name (collision is a bug in either case — the warning
surfaces it). `install_opencode_agent` is unchanged (agents symlink only).

`tests/test_install.py` extends: a skill under `harnesses/shared/skills` gets
linked into `~/.agents/skills`; a name collision with an earlier source warns
and skips without clobbering; existing zed/superpowers coverage untouched.

OpenCode skill discovery is configured explicitly: `~/.config/opencode/opencode.jsonc`
`skills` gains `"harnesses/shared/skills"` (a one-line user-config edit done
with this work and documented here — **not** managed by tkt). Sandbox runs are
fine: `~/LSST/tkt2` and `~/.config/opencode` are already bind-mounted into the
sandbox (read-only).

## 7. GitHub MCP for OpenCode

### 7.1 Server registration (user config)

```sh
opencode mcp add github --global --url https://api.githubcopilot.com/mcp/
```

- The server **must** be named `github`: OpenCode exposes MCP tools as
  `<sanitized-server>_<sanitized-tool>` (`packages/opencode/src/mcp/catalog.ts`:
  `toolName = sanitize(client) + "_" + sanitize(name)`), matching this design's
  `github_*` permission keys 1:1 with the Zed tool names.
- Auth: OAuth-first per OpenCode guidance — **resolved 2026-10-04:
  unavailable.** GitHub's auth server rejects dynamic client registration
  ("Incompatible auth server"; OpenCode `mcp/index.ts` DCR path), so the
  header route is the live configuration: fine-grained read-only PAT in
  `GITHUB_TOKEN`, visible to the background service (export in the profile,
  restart the service), `"headers": { "Authorization": "Bearer
  {env:GITHUB_TOKEN}" }` on the `github` server entry (key verified against
  `mcp/index.ts` requestInit). A pre-registered GitHub OAuth app + `clientId`
  remains the theoretical OAuth route.
- Allowing writes is out of the question by design: both workflows are
  GitHub-read-only (the human posts every reply; fixes land locally).

### 7.2 Global default-deny (`~/.config/opencode/opencode.jsonc`)

One addition to the top-level `permissions` array:

```jsonc
{ "action": "github_*", "resource": "*", "effect": "deny" }
```

Every OpenCode agent is GitHub-dark by default — including GitHub MCP tools
added upstream later. (Deliberate deviation from an exact Zed mirror; approved.)

### 7.3 Per-agent read allow-list (both new agents' frontmatter)

Zed source list (tools set `true`) — 20 tools. Semantics from
`packages/opencode/src/permission/index.ts` (`disabled()` /
`visibleTools()`): the MCP tool's qualified name is the permission **action**,
wildcard-matched, **last matching rule wins**, deny hides the tool and blocks
calls; a hiding rule needs `resource: "*"`. Deny-all first, then exact-name
allows:

```yaml
  # GitHub MCP read-only allow-list (mirrors Zed "mcp-server-github")
  - { action: "github_*",                   resource: "*", effect: deny }
  - { action: "github_search_repositories", resource: "*", effect: allow }
  - { action: "github_search_pull_requests", resource: "*", effect: allow }
  - { action: "github_search_issues",       resource: "*", effect: allow }
  - { action: "github_search_commits",      resource: "*", effect: allow }
  - { action: "github_search_code",         resource: "*", effect: allow }
  - { action: "github_pull_request_read",   resource: "*", effect: allow }
  - { action: "github_list_pull_requests",  resource: "*", effect: allow }
  - { action: "github_list_commits",        resource: "*", effect: allow }
  - { action: "github_list_branches",       resource: "*", effect: allow }
  - { action: "github_list_tags",           resource: "*", effect: allow }
  - { action: "github_list_releases",       resource: "*", effect: allow }
  - { action: "github_list_issues",         resource: "*", effect: allow }
  - { action: "github_list_issue_types",    resource: "*", effect: allow }
  - { action: "github_list_issue_fields",   resource: "*", effect: allow }
  - { action: "github_issue_read",          resource: "*", effect: allow }
  - { action: "github_get_tag",             resource: "*", effect: allow }
  - { action: "github_get_release_by_tag",  resource: "*", effect: allow }
  - { action: "github_get_latest_release",  resource: "*", effect: allow }
  - { action: "github_get_file_contents",   resource: "*", effect: allow }
  - { action: "github_get_commit",          resource: "*", effect: allow }
```

Everything Zed sets `false` (`update_pull_request`,
`update_pull_request_branch`, `search_users`, `list_repository_collaborators`,
`get_teams`, `get_team_members`, `get_me`, `get_label`, and all unlisted
write/create/comment/merge tools) falls under the deny.

**Implementation-time verification (required):** that agent frontmatter rules
override the global rule (ruleset merge order → agent rules evaluated after
global; confirm via `opencode debug agent`/tool visibility and one live
read-only GitHub call), and that a non-allow-listed agent (e.g. `sp-build`)
sees no `github_*` tools. **Source-verified 2026-10-04** against
`investigations/opencode-src`: `disabled()`/`visibleTools()` use `findLast`
(last-match-wins) and `agent.ts` appends agent rules after global ones, so
deny-first/allow-after in the frontmatter is the correct shape — diagnose any
live deviation's cause (merge order, stale service, server-name mismatch)
rather than reordering. The live confirmation is scheduled with the host
script; the *outcome* (the two agents read-only; everyone else denied) is the
contract.

## 8. New OpenCode primary agents

### 8.1 `harnesses/opencode/agents/pr-responder.md`

```markdown
---
name: pr-responder
description: Work through PR review comments - categorize them, land small
  fixes as fixup! commits, and prepare batched responses for the human to post.
mode: primary
permissions:
  - { action: read,     resource: "*", effect: allow }
  - { action: glob,     resource: "*", effect: allow }
  - { action: grep,     resource: "*", effect: allow }
  - { action: list,     resource: "*", effect: allow }
  - { action: shell,    resource: "*", effect: allow }   # git bookkeeping only
  - { action: edit,     resource: "*", effect: allow }   # fixup commits only
  - { action: question, resource: "*", effect: allow }
  - { action: skill,    resource: "*", effect: allow }
  - { action: subagent, resource: "*", effect: deny }
  - { action: webfetch, resource: "*", effect: deny }
  - { action: websearch, resource: "*", effect: deny }
  # GitHub MCP read-only allow-list: see spec §7.3 block, verbatim
---

You are responding to PR review comments. Load the
`responding-to-pr-review` skill at the start of the session and follow it.

Tool mapping (OpenCode): GitHub reads -> the GitHub MCP tools; git bookkeeping
and fixups -> bash; structured questions -> question; scratch -> none (no
subagents). If the GitHub MCP tools are absent, STOP and ask the human — do
not infer review content from branch names or local diffs. Never run a
destructive rebase unsolicited.
```

### 8.2 `harnesses/opencode/agents/code-review.md`

Identical frontmatter **minus the `edit` line** (edit defaults denied as in
`sp-review`), plus the same §7.3 GitHub block. Body: load `reviewing-code` and
follow it; you never modify files, never dispatch subagents, post no comments,
draft no replies; the batched walk-through is the deliverable.

## 9. `tkt/AGENTS.md.in` wording (item 3)

The visual-companion paragraph currently reads: "Only in harnesses without the
tool (legacy sandbox runs) launch it yourself…". Reword so that launching
`start-server.sh` against the bridged `{{vc_port}}` is the **normal** route for
harnesses without the `brainstorm_server` MCP tool — explicitly naming OpenCode
sandbox runs — with `tkt sandbox-run --network` remaining the genuinely-legacy
variant. Keep the "do NOT launch in-sandbox when the MCP tool is available"
rule and the "give the URL verbatim" rule.

## 10. Docs

- `harnesses/README.md`: new `shared/skills/` layer + fork-purity rule + note
  that `opencode/agents/` holds non-superpowers primary agents too; update the
  "Zed role prompts" bullet (reviewer/pr-responder are gone).
- Root `AGENTS.md` file layout: add `harnesses/shared/` and amend the
  `harnesses/opencode/agents/` description.

## 11. Testing & verification

- `tests/test_install.py`: shared-skill linking, collision skip (warn, no
  clobber), existing behavior intact. Run `python -m pytest`, `ruff check .`,
  `ruff format --check .`, `mypy tkt/`.
- Live OpenCode checks (host): `opencode mcp list` shows `github` connected;
  `code-review` and `pr-responder` see exactly the 20 allow-listed tools and no
  others; `sp-build` sees none; one end-to-end `github_pull_request_read` call
  succeeds through an allow-listed agent.
- Live Zed check: primary dispatch reaches the two shared skills via
  `zed-primary-agent` (skills present in `~/.agents/skills` after
  `tkt install-zed-agent`; stale `zed-reviewer`/`zed-pr-responder` links removed
  by `_clean_stale_links`).
- Smoke: `pr-responder` against a scratch ledger + fixture commits produces
  categorize-first behavior and `fixup!` commits without pushing.

## 12. Sequencing (for the plan phase)

1. `harnesses/shared/skills/` — write both skills (port content from the two
   Zed skills; this is the bulk).
2. Zed deletions + `zed-primary-agent` dispatch update; `install.py` three-source
   linking + tests.
3. OpenCode agents `code-review` + `pr-responder`; `opencode.jsonc` edits
   (skills path; GitHub MCP server + global deny) and live permission
   verification (§7.3 contract).
4. `AGENTS.md.in` reword; README + AGENTS.md docs; leftover dir removal.

Steps 1–2 and 3–4 are near-independent halves; the GitHub-config pieces
(§7.1/§7.2) can land first since they touch no repo files.
