# OpenCode Catch-Up: Shared Harness Layer, Review/PR-Response Agents, GitHub MCP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port Zed's review and PR-response workflows to OpenCode as standalone primary agents via a new `harnesses/shared/skills/` layer, and wire a read-only GitHub MCP allow-list into OpenCode.

**Architecture:** Two tkt-authored cross-harness skills (`reviewing-code`, `responding-to-pr-review`) hold all per-phase how-to; Zed's wrapper skills are deleted and its dispatcher re-pointed; OpenCode gets two thin primary agents (`code-review`, `pr-responder`) whose frontmatter carries a GitHub MCP read-only allow-list under a global default-deny. `tkt install-zed-agent` learns to link skills from three sources.

**Tech Stack:** Markdown skills/agent files, Python 3.13 (click, pytest, ruff, mypy), OpenCode v2 config (JSONC), Zed skill symlink farm.

**Spec:** `docs/superpowers/specs/2026-10-03-opencode-catchup-design.md` (read it with this plan; decisions and rationale live there).

## Global constraints

- All new `.py` files keep the BSD-3-Clause header; existing files keep theirs.
- ruff: line-length 110, numpy docstrings; mypy clean over `tkt/`; `python -m pytest` green.
- Commit messages: one-line summary ≤72 chars, no ticket numbers or planning context (repo rule).
- Do **not** add packaging configuration.
- Skills must not use relative links to other skill roots (harnesses/README rule); harness variance is inline prose.
- GitHub access is read-only **by design**: no OpenCode agent may hold GitHub write tools; the human posts every PR reply.
- The agent allow-list block (spec §7.3) appears **verbatim and complete** in both agent files — no cross-references.

---

### Task 1: Feature branch + docs commit

**Files:**

- Create: branch `feature/opencode-catchup` from current `main`
- Commit: `docs/superpowers/specs/2026-10-03-opencode-catchup-design.md`, `docs/superpowers/plans/2026-10-03-opencode-catchup.md`

**Interfaces:**

- Consumes: nothing.
- Produces: the branch all later tasks commit on; the spec/plan docs under version control.

- [ ] **Step 1: Create the feature branch**

```bash
git status   # expect clean; if dirty, STOP and report — do not stash the human's work
git checkout -b feature/opencode-catchup
```

- [ ] **Step 2: Commit spec and plan**

```bash
git add docs/superpowers/specs/2026-10-03-opencode-catchup-design.md \
        docs/superpowers/plans/2026-10-03-opencode-catchup.md
git commit -m "Add spec and plan for OpenCode catch-up."
```

---

### Task 2: Shared skill — `reviewing-code`

Port `harnesses/zed/skills/zed-reviewer/SKILL.md` (the content source — read it first) into the shared layer. All discipline from the spec §4 survives verbatim-in-substance; only harness-variance prose changes.

**Files:**

- Create: `harnesses/shared/skills/reviewing-code/SKILL.md`

**Interfaces:**

- Consumes: nothing.
- Produces: skill name `reviewing-code` — loaded by `code-review` (Task 6) and by Zed's dispatcher (Task 5). Its markdown-table format `Severity | Finding | File:line` and the "no edits, no dispatch, no drafted replies" contract are the interface later tasks and reviewers check against.

- [ ] **Step 1: Write the skill file**

Create `harnesses/shared/skills/reviewing-code/SKILL.md` with exactly:

````markdown
---
name: reviewing-code
description: Use when the human asks you to review their code or review somebody else's pull request; read-only, findings walked through in batches.
---

# Reviewing code or a pull request

You are the primary agent acting as a reviewer. You never modify files and
never dispatch subagents. The human drives the walk-through with your
harness's structured-question tool (Zed: `ask_user` with `allow_free_text:
true`; OpenCode: `question`).

## Mode

Pick the mode from how the human invoked you; confirm with the question tool
(free text allowed) if ambiguous:

- **Code review** — the human's own work: a branch vs. its base, a commit
  range, uncommitted changes, or named files.
- **PR review** — somebody else's pull request that the human is reviewing.

## Setup

Pin the scope before reviewing anything, and state it back in one line
(worktree, base/head SHAs or PR number, how the diff was produced).

- Code review: resolve the exact diff — `git merge-base` against the base
  branch for branch reviews, `BASE_SHA..HEAD_SHA` for ranges, the working
  tree for uncommitted changes.
- PR review: check that GitHub read tools are available (see "GitHub access"
  below). If they are not, STOP and ask the human — this is probably an
  oversight. Do not infer review content from branch names or local diffs.
  Fetch the PR description, the base..head diff, and the existing review
  threads; findings already covered by posted comments are out of scope.
  Verify line numbers against the PR head before citing them — review-thread
  anchors go stale relative to the current code.

## GitHub access

- **Zed:** verify read-only GitHub MCP tools are available; STOP and ask the
  human if they are absent.
- **OpenCode:** GitHub tools appear only for the agents the user has
  allow-listed (`code-review`, `pr-responder`); STOP and ask if absent. The
  remote GitHub MCP server is unreachable inside a network-restricted tkt
  sandbox — run PR reviews in a host OpenCode session (or `tkt sandbox-run
  --network`).

## Review discipline

- Verify everything against the diff and the stated requirements (PR
  description, plan, brief); trust no report or description you have not
  checked against code.
- Cite file:line for every finding.
- Acknowledge strengths before issues.
- Calibrate severity: Critical / Important / Minor (not everything is
  Critical).

## Walk-through

Present findings in batches, not one message:

- Bulk findings go in markdown tables with a severity column
  (`Severity | Finding | File:line`).
- Order by severity (Critical first), then walk the remainder by file or
  theme.
- Pause after each batch with the question tool so the human can dispute a
  finding, re-prioritize, or skip a category before you continue.
- The review is the deliverable: you make no edits, post no GitHub
  comments, and draft no replies. If the human wants fixes, hand off to
  the normal design/build flow — or `responding-to-pr-review` when the
  fixes respond to PR review comments.
````

- [ ] **Step 2: Verify against the Zed source**

Read `harnesses/zed/skills/zed-reviewer/SKILL.md` side by side: every bullet must be present or intentionally re-worded for harness neutrality (`ask_user` → question tool, GitHub MCP → GitHub access section, `zed-pr-responder` → `responding-to-pr-review`). No discipline may be dropped.

- [ ] **Step 3: Commit**

```bash
git add harnesses/shared/skills/reviewing-code/SKILL.md
git commit -m "Add shared reviewing-code skill."
```

---

### Task 3: Shared skill — `responding-to-pr-review`

Port `harnesses/zed/skills/zed-pr-responder/SKILL.md` (the content source — read it first).

**Files:**

- Create: `harnesses/shared/skills/responding-to-pr-review/SKILL.md`

**Interfaces:**

- Consumes: nothing.
- Produces: skill name `responding-to-pr-review` — loaded by `pr-responder` (Task 6) and by Zed's dispatcher (Task 5). The ledger filename format `pr-response-<owner>-<repo>-PR<number>.md`, append-only event-line grammar, and `A1…`/`N1…` verdict labels are the contract.

- [ ] **Step 1: Write the skill file**

Create `harnesses/shared/skills/responding-to-pr-review/SKILL.md` with exactly:

````markdown
---
name: responding-to-pr-review
description: Use to work through PR review comments - categorize them, propose and land small fixes as fixup! commits, and produce batched responses for the human to post.
---

# Responding to a PR review

You are helping the human respond to review comments on their pull request.
The human writes every response actually posted to GitHub; you analyze,
propose code changes, and keep the bookkeeping tidy.

## Setup

- Establish the PR from the dispatch prompt or by asking.
- Check that GitHub read tools are available (see "GitHub access" below). If
  they are not, STOP and ask the human — this is probably an oversight. Do not
  try to infer review content from branch names or local diffs.
- Fetch all review comments and threads, then note the environment: the
  human's PR checkout (work directly in it) or a tkt sandbox branch (land
  work on the sandbox branch; it crosses over via `tkt pull-sandbox`).
  Open the walk-through with a one-line environment recap: worktree,
  branch, HEAD, and how fixups reach the human.
- Verify line numbers against the PR head before citing them; review-thread
  anchors are often stale relative to the current code. Resolve real paths
  (including subpackages and subdirectories) rather than guessing them.
- Look for an existing ledger at the repo root matching
  `pr-response-<owner>-<repo>-PR<number>.md`. If one exists and its first
  line names this PR, read it and resume from the first unsettled item
  rather than restarting the categorization.

## GitHub access

- **Zed:** verify read-only GitHub MCP tools are available; STOP and ask the
  human if they are absent.
- **OpenCode:** GitHub tools appear only for the agents the user has
  allow-listed (`code-review`, `pr-responder`); STOP and ask if absent. The
  remote GitHub MCP server is unreachable inside a network-restricted tkt
  sandbox — run the PR flows in a host OpenCode session (or `tkt
  sandbox-run --network`).

## Question tool

Use your harness's structured-question tool for every decision point (Zed:
`ask_user` with `allow_free_text: true`; OpenCode: `question`, which always
allows a typed answer).

## Ledger

Conversation memory does not survive compaction. Track progress in a
markdown ledger file, not only in todos.

- Ledger path: `pr-response-<owner>-<repo>-PR<number>.md` at the root of
  the repo holding the PR branch — plain sight, not a hidden directory.
  It is scratch; mention once that a global gitignore of
  `pr-response-*.md` keeps it out of `git status`.
- Create it with its identity as the first line:
  `# PR-response ledger — PR: <url> — branch: <branch name>`. Name the
  branch, not a SHA; rebasing churns SHAs.
- Append one line per event; never rewrite history, so a partial ledger
  is always valid:
  - after categorizing: `- cat: A1 verified ("Refactor parser") | N2 trivial typo`
  - after each decision:
    `- N1: decision=fix-now → fixup! "Add sort to results" (7c1d9e2, targets "Refactor parser")`
  - after each batch: `- batch addressed: presented, human confirmed`
- Identify commits by subject first, hash second. Rebases churn hashes;
  `fixup! <subject>` subjects survive by construction. On recovery,
  resolve subjects with `git log --grep` and treat recorded hashes as
  hints only; if a subject resolves to zero or multiple commits, ask the
  human rather than guessing.
- After compaction, trust the ledger and `git log` over your own
  recollection. Items with verdict/decision lines are settled — do not
  re-categorize or re-walk them; resume at the first unsettled item.
- A ledger whose first line names a different PR is another PR's
  progress: leave it in place and start your own, fresh.

## Categorize first

Before proposing any change, assign every comment one verdict, verified
against the code (diff from base to HEAD, recent commits) rather than the
review text alone, and give it a short label the human can use to refer to
it: `A1, A2, ...` (addressed), `N1, N2, ...` (needs work).

- **Addressed** — the branch already reflects the request; cite the commit
  or current lines. Also flag any already-posted reply whose promise
  contradicts the branch (e.g. "I'll add a sort" when a sort is already
  present) — the fix is a follow-up reply, not a fixup.
- **Needs work** — everything else, including trivial one-liners (typos,
  wording, obvious mechanical fixes). Trivial items get a one-line
  walk-through rather than a separate batch.

Present the full categorized list before acting on anything; the human may
re-bucket items. Ledger the final verdicts once settled (one `cat:` line is
enough).

## Presentation

- Bulk categories go in markdown tables with a label column
  (`Label | Comment | Status/action`).
- Pause after each bulk category and after each needs-work item rather than
  delivering everything in one message; the human decides before you move
  on.
- Use the question tool for multiple-choice questions about how to resolve
  or proceed (e.g. fix now / defer / explain only / disagree, or choosing
  between two proposed options).
- Reference source as [`file.py:LINE`](/absolute/path/to/file.py:LINE):
  display text is the short `file.py:line`, the target is an **absolute**
  path. Workspace-relative links do not resolve inside a multi-repo tkt
  workspace, and no harness auto-links bare paths — only the explicit
  absolute link is reliable. Point targets at the human's own checkout
  (the `.agent/<repo>/...` paths without the `.agent` prefix when in a
  sandbox), and at the `.agent/...` worktree when the content exists only
  there (e.g. a fixup you landed that has not been pulled over). Ranges do
  not link; use a single representative line or one reference per line.

## Walk through the "needs work" comments

One at a time, each as its own `### <label>. <short topic>` section: quote
the comment, give context (linked), your analysis, and a proposed change —
or a case for disagreeing. You never draft PR replies. The human decides
per comment: fix now / defer / explain only / disagree. Make code changes
only on an explicit go-ahead, gathered via the question tool. Ledger each
decision as soon as it is made.

## Batches

The addressed bucket accumulates as a compact `comment → action /
rationale` table row — what already covers it (commit or linked lines).
Tables are presented progressively (with pauses) rather than saved for the
end; finish the walk-through with a final confirmation pass so the human
can post one response per batch on a settled list. Compact rows, not draft
prose. Ledger each batch as `presented` and append a `human confirmed`
line once the confirmation pass settles it.

## Landing code changes

- Small changes: one `fixup! <subject>` commit per comment, targeted at the
  branch commit that introduced the code being fixed (ask if ambiguous).
  Run a quick lint/tests check before committing each fixup. Ledger the
  fixup with subject first and hash second, naming the target by subject.
- Larger changes: discuss first, land as ordinary commits.
- Never run a destructive rebase (e.g. `rebase --autosquash`) unsolicited;
  remind the human that the fixups are waiting instead.
````

- [ ] **Step 2: Verify against the Zed source**

Compare with `harnesses/zed/skills/zed-pr-responder/SKILL.md`: ledger grammar, categorization rules, fixup discipline, and the "never draft replies / human posts everything" contract must all survive; only `ask_user`/MCP wording changes.

- [ ] **Step 3: Commit**

```bash
git add harnesses/shared/skills/responding-to-pr-review/SKILL.md
git commit -m "Add shared responding-to-pr-review skill."
```

---

### Task 4: `install_zed_agent` — three skill sources (TDD)

Extend the Zed installer to link skills from `harnesses/shared/skills/` as a third source, with a factored helper and collision warn-and-skip.

**Files:**

- Modify: `tkt/install.py` (docstring lines 85–92; loop body lines 102–117)
- Test: `tests/test_install.py`

**Interfaces:**

- Consumes: nothing new.
- Produces: `~/.agents/skills/<name>` links from three sources in precedence order `harnesses/zed/skills` → `harnesses/shared/skills` → `superpowers/skills`; helper `_link_skill_dirs(skills_src: str, skills_dst: str, managed: set[str], *, dry_run: bool) -> None`. Every skill dir under any source must contain `SKILL.md` to be linked (new unified rule; the old Zed loop did not check, which let empty stray directories through).

- [ ] **Step 1: Write the failing tests**

In `tests/test_install.py`, add after `SUPERPOWERS_SKILLS = ("sp-one", "sp-two")`:

```python
SHARED_SKILLS = ("shared-one",)
```

Extend `_make_repo` (insert before the `superpowers` block):

```python
    shared = os.path.join(root, "harnesses", "shared", "skills")
    for name in SHARED_SKILLS:
        d = os.path.join(shared, name)
        os.makedirs(d)
        with open(os.path.join(d, "SKILL.md"), "w") as f:
            f.write(f"# {name}\n")
```

Add two tests at the end of the file:

```python
def test_install_zed_agent_links_shared_skills(tmp_path):
    """Verify shared skills are linked from harnesses/shared/skills."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_zed_agent(repo_root=str(tmp_path / "repo"), home=home)
    for name in SHARED_SKILLS:
        link = os.path.join(home, ".agents", "skills", name)
        assert os.path.islink(link), link
        assert os.readlink(link) == os.path.join(
            str(tmp_path / "repo"),
            "harnesses",
            "shared",
            "skills",
            name,
        )


def test_install_zed_agent_shared_collision_prefers_earlier_source(tmp_path):
    """A name in two sources links from the earlier source; later is skipped."""
    _make_repo(str(tmp_path / "repo"))
    dup = os.path.join(str(tmp_path / "repo"), "harnesses", "shared", "skills", "zed-explorer")
    os.makedirs(dup)
    with open(os.path.join(dup, "SKILL.md"), "w") as f:
        f.write("# dup\n")
    home = str(tmp_path / "home")
    install_zed_agent(repo_root=str(tmp_path / "repo"), home=home)
    link = os.path.join(home, ".agents", "skills", "zed-explorer")
    assert os.readlink(link) == os.path.join(
        str(tmp_path / "repo"), "harnesses", "zed", "skills", "zed-explorer"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_install.py -v -k "shared or collision"`
Expected: FAIL — shared skill not linked (`AssertionError`), collision test may pass vacuously (no shared source exists yet; the link is correct either way).

- [ ] **Step 3: Implement**

In `tkt/install.py`, add the source tuple and helper between `_clean_stale_links` and `install_zed_agent`:

```python
# Skill source roots linked into ~/.agents/skills, in precedence order:
# an earlier source wins on a name collision (warned about).
_SKILL_SOURCES: tuple[tuple[str, ...], ...] = (
    ("harnesses", "zed", "skills"),
    ("harnesses", "shared", "skills"),
    ("superpowers", "skills"),
)
```

Add the helper after that tuple:

```python
def _link_skill_dirs(skills_src: str, skills_dst: str, managed: set[str], *, dry_run: bool) -> None:
    """Symlink each skill dir (containing ``SKILL.md``) under ``skills_src`` into ``skills_dst``.

    Names already in ``managed`` (linked from an earlier source) are skipped
    with a warning. ``managed`` is updated in place.
    """
    if not os.path.isdir(skills_src):
        return
    for name in sorted(os.listdir(skills_src)):
        src = os.path.join(skills_src, name)
        if not os.path.isdir(src) or not os.path.isfile(os.path.join(src, "SKILL.md")):
            continue
        if name in managed:
            logging.warning(f"Skill {name} in {skills_src} is shadowed by an earlier source; skipping")
            continue
        managed.add(name)
        _ensure_link(os.path.join(skills_dst, name), src, dry_run=dry_run)
```

Replace the body of `install_zed_agent` from its docstring's skills sentence (lines 85–92 docstring text and the two loop blocks at lines 102–117) with:

```python
    """Symlink the Zed harness skills and rules into the user's Zed config.

    Creates ``~/.agents/skills/<name>`` for each skill (directory containing
    ``SKILL.md``) in ``harnesses/zed/skills``, ``harnesses/shared/skills`` and
    ``superpowers/skills`` (earlier sources win on name collisions) and
    ``~/.config/zed/AGENTS.md`` -> ``harnesses/zed/rules.md``. Warns about
    (and, when confirmed, removes) stale symlinks under ``~/.agents/skills``
    that this command no longer manages.
    """
    repo_root = repo_root or _repo_root()
    home = home or os.path.expanduser("~")
    confirm = confirm or (lambda msg: False)
    skills_dst = os.path.join(home, ".agents", "skills")
    zed_cfg = os.path.join(home, ".config", "zed")
    if not dry_run:
        os.makedirs(skills_dst, exist_ok=True)
        os.makedirs(zed_cfg, exist_ok=True)
    managed: set[str] = set()
    for parts in _SKILL_SOURCES:
        _link_skill_dirs(os.path.join(repo_root, *parts), skills_dst, managed, dry_run=dry_run)
    _ensure_link(
        os.path.join(zed_cfg, "AGENTS.md"),
        os.path.join(repo_root, "harnesses", "zed", "rules.md"),
        dry_run=dry_run,
    )
    _clean_stale_links(skills_dst, managed, dry_run=dry_run, confirm=confirm)
```

(The `skills_src = ...` local and both old loop blocks are deleted; everything else in `install.py` — `_ensure_link`, `_clean_stale_links`, `install_opencode_agent` — is untouched.)

- [ ] **Step 4: Run the full test module + lint/type gates**

```bash
python -m pytest tests/test_install.py -v        # all PASS, including pre-existing tests
ruff check tkt/install.py tests/test_install.py && ruff format --check tkt/install.py tests/test_install.py
mypy tkt/
```

Expected: all pass. (Pre-existing fixture tests are unaffected — their synthetic skills all have `SKILL.md`.)

- [ ] **Step 5: Commit**

```bash
git add tkt/install.py tests/test_install.py
git commit -m "Link shared harness skills in install-zed-agent."
```

---

### Task 5: Zed deletions + dispatcher re-point

Delete the two superseded Zed skills (content now lives in the shared skills from Tasks 2–3) and re-point the primary dispatcher.

**Files:**

- Delete: `harnesses/zed/skills/zed-reviewer/`, `harnesses/zed/skills/zed-pr-responder/`
- Modify: `harnesses/zed/skills/zed-primary-agent/SKILL.md` (dispatch bullets, lines 18–21)
- Remove: `harnesses/zed/skills/zed-shared-worktree-review/` (empty, untracked — `rmdir`, not a git operation)

**Interfaces:**

- Consumes: shared skill names `reviewing-code`, `responding-to-pr-review` (Tasks 2–3).
- Produces: `~/.agents/skills` free of `zed-reviewer`/`zed-pr-responder` after the next `tkt install-zed-agent` (stale-link cleanup handles the user's existing links); Zed primary dispatch routes to the shared skills.

- [ ] **Step 1: Delete the superseded skills**

```bash
git rm -r harnesses/zed/skills/zed-reviewer harnesses/zed/skills/zed-pr-responder
rmdir harnesses/zed/skills/zed-shared-worktree-review   # empty leftover; ignore error if absent
```

- [ ] **Step 2: Re-point `zed-primary-agent` dispatch**

In `harnesses/zed/skills/zed-primary-agent/SKILL.md`, replace the two dispatch bullets:

```markdown
- If this is responding to review comments on an existing pull request,
  invoke `responding-to-pr-review`.
- If this is a request to review code or review somebody else's pull
  request, invoke `reviewing-code`.
```

(Replaces the bullets naming `zed-pr-responder` and `zed-reviewer`; no other text in the file changes.)

- [ ] **Step 3: Verify no dangling references**

```bash
grep -rn "zed-reviewer\|zed-pr-responder" harnesses/ tkt/ docs/zed-agent-roadmap.md
```

Expected: no matches in `harnesses/` or `tkt/`. (`docs/zed-agent-roadmap.md` is historical Zed prose — if it mentions the names, leave it; per its own header the roadmap is curated when next picked up, not back-edited.)

- [ ] **Step 4: Commit**

```bash
git add -A harnesses/zed/skills
git commit -m "Route Zed review and PR-response flow to shared skills."
```

---

### Task 6: OpenCode primary agents — `code-review` and `pr-responder`

Two standalone (no `sp-` prefix) primary agent files in the OpenCode harness, each carrying the GitHub read-only allow-list verbatim (spec §7.3; deny-first, exact-name allows after — last match wins per `packages/opencode/src/permission/index.ts` in the OpenCode source).

**Files:**

- Create: `harnesses/opencode/agents/code-review.md`
- Create: `harnesses/opencode/agents/pr-responder.md`

**Interfaces:**

- Consumes: skill names `reviewing-code` (Task 2), `responding-to-pr-review` (Task 3); permission keys `github_<tool>` which exist once the server is named `github` (Task 7 — the agents are inert without it, and both STOP-and-ask by skill design).
- Produces: OpenCode agents `code-review` and `pr-responder` (invoked as primary agents by the human), and the GitHub permission block reused by Task 7's verification expectations.

- [ ] **Step 1: Write `harnesses/opencode/agents/code-review.md`** (complete file):

````markdown
---
name: code-review
description: Review the human's own code (branch, range, working tree, files)
  or somebody else's pull request; read-only, findings walked through in
  batches with the human.
mode: primary
permissions:
  - { action: read, resource: "*", effect: allow }
  - { action: glob, resource: "*", effect: allow }
  - { action: grep, resource: "*", effect: allow }
  - { action: list, resource: "*", effect: allow }
  - { action: shell, resource: "*", effect: allow }
  - { action: question, resource: "*", effect: allow }
  - { action: skill, resource: "*", effect: allow }
  - { action: edit, resource: "*", effect: deny }
  - { action: subagent, resource: "*", effect: deny }
  - { action: webfetch, resource: "*", effect: deny }
  - { action: websearch, resource: "*", effect: deny }
  # GitHub MCP read-only allow-list (mirrors the Zed "mcp-server-github" set)
  - { action: "github_*", resource: "*", effect: deny }
  - { action: "github_search_repositories", resource: "*", effect: allow }
  - { action: "github_search_pull_requests", resource: "*", effect: allow }
  - { action: "github_search_issues", resource: "*", effect: allow }
  - { action: "github_search_commits", resource: "*", effect: allow }
  - { action: "github_search_code", resource: "*", effect: allow }
  - { action: "github_pull_request_read", resource: "*", effect: allow }
  - { action: "github_list_pull_requests", resource: "*", effect: allow }
  - { action: "github_list_commits", resource: "*", effect: allow }
  - { action: "github_list_branches", resource: "*", effect: allow }
  - { action: "github_list_tags", resource: "*", effect: allow }
  - { action: "github_list_releases", resource: "*", effect: allow }
  - { action: "github_list_issues", resource: "*", effect: allow }
  - { action: "github_list_issue_types", resource: "*", effect: allow }
  - { action: "github_list_issue_fields", resource: "*", effect: allow }
  - { action: "github_issue_read", resource: "*", effect: allow }
  - { action: "github_get_tag", resource: "*", effect: allow }
  - { action: "github_get_release_by_tag", resource: "*", effect: allow }
  - { action: "github_get_latest_release", resource: "*", effect: allow }
  - { action: "github_get_file_contents", resource: "*", effect: allow }
  - { action: "github_get_commit", resource: "*", effect: allow }
---

You are an independent reviewer. Load the `reviewing-code` skill at the start
of the session and follow it.

You never modify files, never dispatch subagents, post no GitHub comments, and
draft no replies — the batched walk-through is the deliverable.

Tool mapping (OpenCode):

- GitHub reads -> the GitHub MCP tools (read-only set above); if absent, STOP
  and ask the user — do not infer review content from branch names or diffs.
- Git/diff inspection -> bash (read-only use)
- Findings walk-through pacing -> question
- Load skills -> skill
````

- [ ] **Step 2: Write `harnesses/opencode/agents/pr-responder.md`** (complete file):

````markdown
---
name: pr-responder
description: Work through PR review comments - categorize them, land small
  fixes as fixup! commits, and prepare batched responses for the human to post.
mode: primary
permissions:
  - { action: read, resource: "*", effect: allow }
  - { action: glob, resource: "*", effect: allow }
  - { action: grep, resource: "*", effect: allow }
  - { action: list, resource: "*", effect: allow }
  - { action: shell, resource: "*", effect: allow } # git bookkeeping only
  - { action: edit, resource: "*", effect: allow } # fixup commits only
  - { action: question, resource: "*", effect: allow }
  - { action: skill, resource: "*", effect: allow }
  - { action: subagent, resource: "*", effect: deny }
  - { action: webfetch, resource: "*", effect: deny }
  - { action: websearch, resource: "*", effect: deny }
  # GitHub MCP read-only allow-list (mirrors the Zed "mcp-server-github" set)
  - { action: "github_*", resource: "*", effect: deny }
  - { action: "github_search_repositories", resource: "*", effect: allow }
  - { action: "github_search_pull_requests", resource: "*", effect: allow }
  - { action: "github_search_issues", resource: "*", effect: allow }
  - { action: "github_search_commits", resource: "*", effect: allow }
  - { action: "github_search_code", resource: "*", effect: allow }
  - { action: "github_pull_request_read", resource: "*", effect: allow }
  - { action: "github_list_pull_requests", resource: "*", effect: allow }
  - { action: "github_list_commits", resource: "*", effect: allow }
  - { action: "github_list_branches", resource: "*", effect: allow }
  - { action: "github_list_tags", resource: "*", effect: allow }
  - { action: "github_list_releases", resource: "*", effect: allow }
  - { action: "github_list_issues", resource: "*", effect: allow }
  - { action: "github_list_issue_types", resource: "*", effect: allow }
  - { action: "github_list_issue_fields", resource: "*", effect: allow }
  - { action: "github_issue_read", resource: "*", effect: allow }
  - { action: "github_get_tag", resource: "*", effect: allow }
  - { action: "github_get_release_by_tag", resource: "*", effect: allow }
  - { action: "github_get_latest_release", resource: "*", effect: allow }
  - { action: "github_get_file_contents", resource: "*", effect: allow }
  - { action: "github_get_commit", resource: "*", effect: allow }
---

You are responding to PR review comments. Load the `responding-to-pr-review`
skill at the start of the session and follow it.

Tool mapping (OpenCode):

- GitHub reads -> the GitHub MCP tools (read-only set above); if absent, STOP
  and ask the user — do not infer review content from branch names or diffs.
- Git bookkeeping, ledgers, fixup commits -> bash + write/edit (the ledger
  file and code fixes only; never push, never post to GitHub, never rebase
  unsolicited)
- Per-item decisions and pacing -> question
- Load skills -> skill
````

- [ ] **Step 3: Cross-check the two blocks match**

```bash
diff <(grep 'action: "github_' harnesses/opencode/agents/code-review.md) \
     <(grep 'action: "github_' harnesses/opencode/agents/pr-responder.md)
```

Expected: no output (identical 21-line blocks).

- [ ] **Step 4: Commit**

```bash
git add harnesses/opencode/agents/code-review.md harnesses/opencode/agents/pr-responder.md
git commit -m "Add OpenCode code-review and pr-responder agents."
```

---

### Task 7: OpenCode user configuration + live GitHub verification

Configure the user's `~/.config/opencode/opencode.jsonc` and the GitHub MCP server, then verify the §7 contract live. This task touches **no repo files**; it edits user config with the human's awareness (they approved this in the design) and validates against the live install.

**Files:**

- Modify: `~/.config/opencode/opencode.jsonc` (user config — outside the repo)
- Reference: `harnesses/opencode/agents/code-review.md` (Task 6 — expected tool set)

**Interfaces:**

- Consumes: agent files from Task 6 (the allow-list defines the expected tool set); `harnesses/shared/skills/` from Tasks 2–3 (skills path).
- Produces: a connected `github` MCP server; global `github_*` deny; both agents seeing exactly the 20 allow-listed GitHub tools, all other agents seeing none.

- [ ] **Step 1: Add the shared skills root to discovery**

In `~/.config/opencode/opencode.jsonc`, extend the `skills` array:

```jsonc
"skills": ["/home/jbosch/LSST/tkt2/superpowers/skills", "/home/jbosch/LSST/tkt2/harnesses/shared/skills"],
```

- [ ] **Step 2: Register the GitHub MCP server (named `github` — required)**

```bash
/home/jbosch/.opencode/bin/opencode mcp add github --global --url https://api.githubcopilot.com/mcp/
/home/jbosch/.opencode/bin/opencode mcp list
```

Expected: `github` listed. If it reports needing authentication, STOP and ask the human to sign in from the TUI (`/mcps` → select `github` → OAuth) — do not run `opencode mcp auth` from a shell tool (interactive flow). Re-check with `mcp list` until connected.

**Fallback if OAuth proves unavailable for this endpoint:** the human mints a fine-grained PAT (read-only *Contents*, *Pull requests*, *Issues*, *Metadata*, scoped to the repos to be reviewed) and exports it as `GITHUB_TOKEN` in the environment the background **service** starts from (not just the TUI shell). Then set the header-based auth in `~/.config/opencode/opencode.jsonc` under the `github` server entry:

```jsonc
"headers": { "Authorization": "Bearer {env:GITHUB_TOKEN}" }
```

Restart the service and re-check `mcp list`. (Per spec §7.1: header auth only if OAuth fails; never write the token literally into config.)

- [ ] **Step 3: Add the global default-deny rule**

In `~/.config/opencode/opencode.jsonc`, append to the top-level `permissions` array (after the existing `external_directory` rules):

```jsonc
    { "action": "github_*", "resource": "*", "effect": "deny" }
```

- [ ] **Step 4: Restart the service and verify tool scoping**

```bash
/home/jbosch/.opencode/bin/opencode service restart
```

Then verify (host session; the sandbox has no network for the remote MCP):

```bash
/home/jbosch/.opencode/bin/opencode run --agent code-review \
  "Reply with exactly one line per tool you can call whose name starts with github_, one per line, nothing else."
```

Expected: exactly the 20 allow-listed names, no `github_update_*`/`github_create_*`/`github_add_*`, no `github_get_me`/`github_search_users`. Then:

```bash
/home/jbosch/.opencode/bin/opencode run --agent sp-build \
  "Reply with exactly one line per tool you can call whose name starts with github_, or NONE."
```

Expected: `NONE`. (Also repeat for `pr-responder`.)

- [ ] **Step 5: Contract-failure fallback**

If allow-listed agents see too few/many tools: do NOT reorder blindly. OpenCode's matcher is last-match-wins (`findLast` in `packages/opencode/src/permission/index.ts`, and agent rules append after global ones per `agent.ts`), so the deny-before-allows ordering already in the files is the correct shape — moving allows *ahead of* the deny would make the wildcard deny last and deny everything. Diagnose first (`opencode debug agent` or service logs) to confirm where the deviation comes from (merge order, stale service state, server-name mismatch producing non-`github_` tool names), then fix the diagnosed cause. The outcome contract (two agents read-only, everyone else none) is the requirement.

- [ ] **Step 6: Zed-side install refresh**

```bash
python -m tkt install-zed-agent --dry-run   # expect: shared skills listed as new links; zed-reviewer/zed-pr-responder flagged stale
python -m tkt install-zed-agent            # confirm stale removal when prompted
ls ~/.agents/skills/reviewing-code/SKILL.md ~/.agents/skills/responding-to-pr-review/SKILL.md
```

Expected: shared skills present; `zed-reviewer`/`zed-pr-responder` gone from `~/.agents/skills`.

---

### Task 8: `tkt/AGENTS.md.in` visual-companion reword

Stop branding OpenCode's normal companion path as "legacy" (spec §9). One paragraph; keep the two hard rules (no in-sandbox launch when the MCP tool exists; verbatim URL).

**Files:**

- Modify: `tkt/AGENTS.md.in` (the visual-companion paragraph, ~lines 48–59)

**Interfaces:**

- Consumes: nothing.
- Produces: mode-conditional template unchanged elsewhere — `render_agents_md`'s `{{vc_port}}` substitution and the `shared|worktree` blocks are untouched.

- [ ] **Step 1: Replace the paragraph's tail**

In `tkt/AGENTS.md.in`, change:

```
Only in harnesses without the tool (legacy
sandbox runs) launch it yourself: there the localhost port `{{vc_port}}` is
bridged back to the host, so bind to `127.0.0.1:{{vc_port}}` and give the
human the returned URL verbatim (no auto-open — there is no display in the
sandbox).
```

to:

```
In harnesses without the tool — the
normal case for OpenCode sandbox runs, and for legacy `tkt sandbox-run`
runs — launch it yourself: the sandbox bridges the localhost port
`{{vc_port}}` back to the host, so bind to `127.0.0.1:{{vc_port}}` and give
the human the returned URL verbatim (no auto-open — there is no display in
the sandbox).
```

(Everything above this tail — the `brainstorm_server` preference and file-mediated usage — stays. The unrelated "legacy sandbox runs: `tkt sandbox-run --network`" line about network access is not part of this change.)

- [ ] **Step 2: Verify rendering unaffected**

```bash
python -m pytest tests/test_sandbox.py -v   # render_agents_md tests pass (both modes)
grep -o "{{vc_port}}" tkt/AGENTS.md.in | wc -l   # expect: 2 (grep -c counts lines; both uses share one line)
```

- [ ] **Step 3: Commit**

```bash
git add tkt/AGENTS.md.in
git commit -m "AGENTS.md.in: name OpenCode sandbox runs in companion guidance."
```

---

### Task 9: Harness docs — README placement rules + repo AGENTS.md

**Files:**

- Modify: `harnesses/README.md` (whole file)
- Modify: `AGENTS.md` (file-layout bullets for `harnesses/`)

**Interfaces:**

- Consumes: final state of `harnesses/` from Tasks 2–6.
- Produces: documentation asserting the fork-purity rule and the three-source placement taxonomy (used by all future contributions).

- [ ] **Step 1: Replace `harnesses/README.md` wholesale**

````markdown
# Harness Specializations

This directory holds per-harness and cross-harness agent/prompt content for
tkt. Each harness layer is separate because prompts and prompt placement
differ by harness.

```
harnesses/
  shared/skills/<name>/       tkt-authored, cross-harness per-phase skills
                              (symlinked into ~/.agents/skills for Zed by
                              `tkt install-zed-agent`; registered via
                              skills.paths in opencode.jsonc for OpenCode)
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
````

- [ ] **Step 2: Update the root `AGENTS.md` file-layout bullets**

Replace the existing `harnesses/opencode/agents/` bullet with:

```markdown
- **`harnesses/opencode/agents/`** — Custom OpenCode workflow agents
  `sp-design`, `sp-plan`, `sp-build`, `sp-debug`, `sp-review`, plus the
  standalone (non-superpowers) primary agents `code-review` and
  `pr-responder`; `~/.config/opencode/agents/` is a symlink to it (via `tkt
install-opencode-agent`).
```

And insert a new bullet immediately after it:

```markdown
- **`harnesses/shared/skills/`** — tkt-authored, cross-harness per-phase
  skills (`reviewing-code`, `responding-to-pr-review`): the place for new
  shared workflows. The `superpowers/` submodule holds only modifications to
  upstream-shipped skills. Linked into `~/.agents/skills` by `tkt
install-zed-agent` and registered via `skills.paths` for OpenCode.
```

- [ ] **Step 3: Verify links/names agree with reality**

```bash
ls harnesses/shared/skills/ harnesses/opencode/agents/ harnesses/zed/skills/
```

Expected: shared has exactly the two new skills; agents dir has five `sp-*` + `code-review.md` + `pr-responder.md`; zed skills has three role-prompt dirs.

- [ ] **Step 4: Commit**

```bash
git add harnesses/README.md AGENTS.md
git commit -m "Document shared harness skill layer and new agents."
```

---

### Task 10: Full verification sweep

**Files:**

- None (verification only).

**Interfaces:**

- Consumes: everything above.
- Produces: the evidence block for the final review (and the `finishing-a-development-branch` step that follows this plan).

- [ ] **Step 1: Repo gates**

```bash
python -m pytest -q          # green (investigations/superpowers excluded via pyproject)
ruff check . && ruff format --check . && mypy tkt/
```

- [ ] **Step 2: Spec §11 live checklist**

- `opencode mcp list` → `github` connected.
- `opencode run --agent code-review` tool self-report → exactly the 20 allow-listed `github_*` tools (Task 7 Step 4 commands).
- `opencode run --agent pr-responder` → same; `sp-build` → none.
- `~/.agents/skills` has `reviewing-code`, `responding-to-pr-review`; lacks `zed-reviewer`, `zed-pr-responder`.
- In a Zed primary session: "review my branch" dispatches `reviewing-code` (skill loads, batched walk-through starts).

- [ ] **Step 3: Smoke the PR-response flow**

On a scratch branch in a scratch repo (not the tkt workspace): create 3 fixture commits, a fake ledger + fixture review comments, run `pr-responder` locally with the GitHub MCP tools, verify: categorize-first output, ledger created with the identity first line, one `fixup!` commit produced per go-ahead, no push and no rebase executed.

- [ ] **Step 4: Hand off to finishing**

Commit any Task 7/8 config-adjacent repo changes missed (there should be none). Then run the superpowers `finishing-a-development-branch` skill: full test suite, commit-by-commit presentation, offer the squash-and-rebase onto `main` per the repo's git workflow. Do not push.

---

## Plan self-review notes (executor may ignore)

- Spec coverage: §3 layout → Tasks 2/3/5/6/9; §4 skills → Tasks 2/3; §5 Zed → Task 5; §6 install → Task 4; §7 GitHub → Tasks 6/7 (naming, global deny, per-agent allow, OAuth-first, live contract); §8 agents → Task 6; §9 wording → Task 8; §10 docs → Task 9; §11 tests → Tasks 4/7/10.
- Unified `SKILL.md`-required linking (Task 4) is a deliberate small hardening beyond the old Zed-only loop; it is what stops the empty stray dir from ever linking.
- If a task's verification step contradicts this plan's expectations, stop and report — do not "fix" expectations silently.
