# R1 — Prompts → Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Zed native-agent harness layer (a role-scoped dispatch table + Zed-only skills), restructure the repo under a new `harnesses/` directory, add `tkt install-zed-agent` / `tkt install-opencode-agent`, thin the OpenCode `sp-review` shell, and document content-placement rules — without breaking the still-active OpenCode workflow.

**Architecture:** Prompts move per-harness. Primary gate orchestration lives in each harness's own layer (OpenCode `sp-*.md` shells unchanged; Zed in `harnesses/zed/rules.md` + `harnesses/zed/skills/zed-primary-agent/`). Subagent role prompts become Zed-only skills (`zed-explorer` / `zed-implementer` / `zed-reviewer`) because Zed's `spawn_agent` has no built-in role prompt. `using-superpowers` and the shared how-to skills stay unchanged. A `tkt/install.py` module symlinks the Zed/OpenCode harness content into `$HOME`, and `local.json` drops `~/.config/opencode` to read-only.

**Tech Stack:** Python 3.13, click, pytest. No new third-party deps.

**Spec:** `docs/superpowers/specs/2026-09-01-r1-prompts-to-skills-design.md`

## Global Constraints

- Python 3.13; deps are `click`, `GitPython`, `pyyaml`, `json5` — no new third-party dependencies.
- Every `.py` file carries the BSD-3-Clause header exactly as in existing modules (preserve it; do not alter).
- Must pass before each commit and at the end: `ruff check .` and `ruff format --check .` and `mypy tkt/`.
- `tkt` is not pip-distributed; do not add packaging config.
- OpenCode workflow must keep working throughout (coexistence).
- Repo root for `tkt/install.py` is the parent of the `tkt/` package: `os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`.
- Do not touch `superpowers/` (submodule) or the superpowers templates in this plan.

---

### Task 1: Copy the OpenCode agents directory to the new location

**Files:**
- Create: `harnesses/opencode/agents/` — a **copy** of `agents/opencode/` (do NOT delete the original yet)
- Verify: `harnesses/opencode/agents/` contains the five `sp-*.md` shells

**Interfaces:**
- Produces: the new canonical location `harnesses/opencode/agents/` that `install-opencode-agent` (Task 9/10) symlinks into `~/.config/opencode/agents`.
- The original `agents/opencode/` is **kept in place** through the whole build so the current OpenCode setup (whose `~/.config/opencode/agents` symlink still points at it) keeps working. It is removed only in Task 14, after the human has verified the install from the new location.

- [ ] **Step 1: Copy the directory (keep the original)**

```bash
mkdir -p harnesses/opencode
cp -r agents/opencode harnesses/opencode/agents
```

(`mkdir -p harnesses/opencode` creates the parent; `cp -r` copies so the original `agents/opencode` remains until Task 14.) The `agents/opencode` symlink under `~/.config/opencode/` is on the host and still points at the original; it is re-pointed by `install-opencode-agent` in Task 13.

- [ ] **Step 2: Verify the copy**

```bash
ls harnesses/opencode/agents
```
Expected: `sp-build.md  sp-debug.md  sp-design.md  sp-plan.md  sp-review.md`
And confirm the original still exists: `ls agents/opencode` shows the same five files.

- [ ] **Step 3: Confirm no in-repo reference breaks (other than AGENTS.md, fixed in Task 12)**

```bash
grep -rn "agents/opencode" --include=*.py --include=*.sh --include=*.md --include=*.json* . | grep -v "\.git/" | grep -v investigations/
```
Expected: only `AGENTS.md` (updated in Task 12) plus the new `harnesses/opencode/agents/` paths introduced here. No `.py`/config references should name the old path as a source of truth.

- [ ] **Step 4: Commit**

```bash
git add harnesses/opencode
git commit -m "Copy OpenCode agents under harnesses/opencode/agents"
```

---

### Task 2: Content-placement rules doc (`harnesses/README.md`)

**Files:**
- Create: `harnesses/README.md`

**Interfaces:**
- Produces: the R1 deliverable; documents the layout and the rules that the Zed skills and future R2 work follow. `AGENTS.md` (Task 12) points here.

- [ ] **Step 1: Write `harnesses/README.md`**

```markdown
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

| Content | Home |
| --- | --- |
| Per-phase how-to (brainstorming, writing-plans, subagent-driven-development, systematic-debugging) | shared superpowers skills |
| OpenCode subagent templates (implementer-prompt, task-reviewer-prompt, re-review-prompt, code-reviewer) | superpowers templates |
| Zed subagent role prompts | Zed-only skills (`zed-explorer`, `zed-implementer`, `zed-reviewer`) |
| Primary phase orchestration + gate signal | per-harness: OpenCode `sp-*.md` shells; Zed `rules.md` + `zed-primary-agent` |
| Tool mapping / subagent names / permissions | harness layer (`opencode-tools.md` / `zed-tools.md`) |
| Project facts for all agents incl. subagents | project `AGENTS.md` |
| Zed system-prompt tool mitigation | Zed system-prompt override (R2) |

Guidelines:

- Per-phase *how-to* lives in shared superpowers skills, never in a harness
  shell. A shell only says which skill to load and the harness-specific gate
  mechanism.
- Subagent *role* prompts are Zed-only skills (Zed's `spawn_agent` has no
  built-in role prompt); OpenCode keeps its own templates/`sp-review`.
- Primary *gate orchestration* is per-harness, written once, concretely — do
  not split it into a shared "conceptual" copy plus harness "concrete" copies.
- `using-superpowers` stays purely about skill discovery.
```

(Keep the fenced `harnesses/...` block and the table exactly as shown.)

- [ ] **Step 2: Verify**

```bash
head -5 harnesses/README.md
```
Expected: the `# Harness Specializations` heading.

- [ ] **Step 3: Commit**

```bash
git add harnesses/README.md
git commit -m "Document content-placement rules in harnesses/README.md"
```

---

### Task 3: Zed dispatch table (`harnesses/zed/rules.md`)

**Files:**
- Create: `harnesses/zed/rules.md`

**Interfaces:**
- Produces: the file symlinked to `~/.config/zed/AGENTS.md` by `install-zed-agent` (Task 9/10); read by every Zed primary and subagent at session start.

- [ ] **Step 1: Write `harnesses/zed/rules.md`**

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

- [ ] **Step 2: Verify**

```bash
grep -c "zed-" harnesses/zed/rules.md
```
Expected: 5 (zed-primary-agent, zed-explorer, zed-implementer, zed-reviewer x3) or more; confirm the file reads as the dispatch table above.

- [ ] **Step 3: Commit**

```bash
git add harnesses/zed/rules.md
git commit -m "Add Zed agent dispatch table (harnesses/zed/rules.md)"
```

---

### Task 4: `zed-primary-agent` skill

> Note: all Zed-only `SKILL.md` files must begin with YAML front-matter (`name`
> = directory name, `description`), as the shared superpowers skills do — Zed's
> loader requires it. Keep every line ≤110 chars.
>
> Note: after the build, the human hand-revised the zed skills (see the design
> doc "Deviations applied by the human"): they are now general Zed skills usable
> by a primary or a subagent; `zed-primary-agent` categorizes the request and
> loads follow-up skills (gate = "ask when intent unclear"); `rules.md`'s
> PRIMARY section is just "load zed-primary-agent"; the review phase is not
> surfaced in the primary flow. The step snippets below predate that revision —
> code in `harnesses/zed/` is source of truth.

**Files:**
- Create: `harnesses/zed/skills/zed-primary-agent/SKILL.md`

**Interfaces:**
- Produces: the skill `rules.md` tells a Zed primary to load; carries the non-linear primary flow and the wait-for-signal gate rule.

- [ ] **Step 1: Write `harnesses/zed/skills/zed-primary-agent/SKILL.md`**

```markdown
---
name: zed-primary-agent
description: Use as the primary Zed agent to orchestrate a superpowers phase and gate on human signals.
---

# zed-primary-agent (Zed-only)

You are a primary (top-level) Zed agent. Subagents do not load this skill.

## The superpowers change flow

The phases are: design, plan, build, debug, review. The common path is
design → plan → build → review, but the flow is not a fixed pipeline:

- **debug** is an optional *start* phase (diagnose a bug before designing a
  fix), not usually part of the main flow.
- **plan** may be skipped for small changes.
- **build** may never happen for design/documentation-only work.
- **plan** or **build** may reveal a design problem and loop **back** to
  design.

## Gates — wait for the human

The human drives the flow by invoking a skill to signal each gate:

- design  → invoke `brainstorming`
- plan    → invoke `writing-plans`
- build   → invoke `subagent-driven-development`
- debug   → invoke `systematic-debugging`
- review  → invoke `requesting-code-review`

Never advance, skip, or loop to a later phase on your own. When the current
phase's work is complete, STOP and report; wait for the human to invoke the
next skill. That invocation is the only gate that moves you forward. Because
the human chooses the next phase, they may skip, repeat, or loop back — follow
whatever phase they signal next.
```

- [ ] **Step 2: Verify**

```bash
grep -q "wait for the human" harnesses/zed/skills/zed-primary-agent/SKILL.md
```
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add harnesses/zed/skills/zed-primary-agent/SKILL.md
git commit -m "Add zed-primary-agent skill with non-linear flow and gate rule"
```

---

### Task 5: `zed-explorer` skill

**Files:**
- Create: `harnesses/zed/skills/zed-explorer/SKILL.md`

**Interfaces:**
- Produces: the skill a Zed subagent loads for codebase investigation (design/plan/debug phases), including investigative coding.

- [ ] **Step 1: Write `harnesses/zed/skills/zed-explorer/SKILL.md`**

```markdown
# zed-explorer (Zed-only)

You are a subagent tasked with investigating a codebase. You are read-only with
respect to production code, but the sandbox is writable scratch space: you may
write and run throwaway probe scripts to learn what code does.

## Investigation discipline

- Find files with `find_path` (glob patterns); search contents with `grep`;
  read with `read_file`; list with `list_directory`; run read-only shell with
  `bash`.
- Match the thoroughness level the caller asked for: quick (basic searches),
  medium (moderate), or very thorough (comprehensive across multiple locations
  and naming conventions).
- Report file paths as absolute paths. Avoid emojis.

## Investigative coding

- When reading is not enough, write a throwaway probe under the sandbox's
  writable scratch area and run it with `bash` to learn behavior.
- Do not modify production code. Do not commit anything.

## Reporting

Report your findings clearly, citing the files and evidence you inspected. If
the task needs implementation (not just investigation), say so rather than
doing it.
```

- [ ] **Step 2: Verify**

```bash
grep -q "Investigative coding" harnesses/zed/skills/zed-explorer/SKILL.md
```
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add harnesses/zed/skills/zed-explorer/SKILL.md
git commit -m "Add zed-explorer skill (investigation + investigative coding)"
```

---

### Task 6: `zed-implementer` skill

**Files:**
- Create: `harnesses/zed/skills/zed-implementer/SKILL.md`

**Interfaces:**
- Produces: the skill a Zed subagent loads to implement a build-phase task (the SDD implementer contract).

- [ ] **Step 1: Write `harnesses/zed/skills/zed-implementer/SKILL.md`**

```markdown
# zed-implementer (Zed-only)

You are a subagent implementing one task from an approved plan.

## Contract

- Your dispatch prompt gives you a task brief file path and a report file path.
- Read the brief first — it is your requirements, with the exact values to use
  verbatim.
- Implement exactly what the brief specifies; write tests (TDD if the brief
  says so); verify; commit.
- Write your full report to the report file, then return only status, commits,
  a one-line test summary, concerns, and the report file path.
- Ask questions before starting or mid-task if anything is unclear; never guess
  silently.

## Escalation

Report one of:

- DONE — work complete and tests pass.
- DONE_WITH_CONCERNS — complete but with doubts about correctness.
- NEEDS_CONTEXT — need information that was not provided.
- BLOCKED — cannot complete; describe what you tried and what help you need.

## Rules

- You do not dispatch subagents. Do all of this task's work yourself.
- Self-review your diff before reporting (completeness, quality, YAGNI,
  test validity).
- Run the focused test for what you are changing while iterating; run the full
  suite once before committing.
```

- [ ] **Step 2: Verify**

```bash
grep -q "You do not dispatch subagents" harnesses/zed/skills/zed-implementer/SKILL.md
```
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add harnesses/zed/skills/zed-implementer/SKILL.md
git commit -m "Add zed-implementer skill (SDD build contract)"
```

---

### Task 7: `zed-reviewer` skill (shared core + scopes)

**Files:**
- Create: `harnesses/zed/skills/zed-reviewer/SKILL.md`

**Interfaces:**
- Produces: the skill a Zed subagent loads for task / re-review / final reviews; reconciles sp-review + the three superpowers reviewer templates into one shared core.

- [ ] **Step 1: Write `harnesses/zed/skills/zed-reviewer/SKILL.md`**

```markdown
# zed-reviewer (Zed-only)

You are an independent, read-only reviewer. You never modify files and never
dispatch subagents.

Your dispatch prompt gives you the scope (task, re-review, or final) and the
paths for the brief, report, and diff package. Follow the scope section below.

## Shared discipline

- Treat the implementer's report as unverified claims; verify against the diff.
- Cite file:line for every finding; acknowledge strengths before issues.
- Calibrate severity: Critical / Important / Minor (not everything is Critical).
- Your final message is the report itself — verdicts and file:line, no
  preamble or closing summary.

## Scope: task

Verify one task's implementation: first whether it matches its requirements
(spec compliance), then whether it is well-built (quality).

- Missing / Extra / Misunderstood against the brief.
- Report requirements you cannot verify from the diff as ⚠️ items.
- Output: Spec Compliance verdict (✅/❌/⚠️), Strengths, Issues
  (Critical/Important/Minor), Task quality verdict.

## Scope: re-review

Verify a fix round addressed the previous findings.

- Verdict each finding ADDRESSED or NOT ADDRESSED, with file:line evidence.
  "Attempted" is not addressed.
- Inspect the fix diff for new breakage the fix itself introduced.
- Out-of-scope observations go in a separate, non-blocking section.

## Scope: final

Review the whole branch against its plan/requirements.

- Plan alignment, code quality, architecture, testing, production readiness.
- Output: Strengths, Issues (Critical/Important/Minor), Recommendations,
  Assessment, and a merge verdict (Yes / No / With fixes).
```

- [ ] **Step 2: Verify**

```bash
grep -q "Scope: final" harnesses/zed/skills/zed-reviewer/SKILL.md
```
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add harnesses/zed/skills/zed-reviewer/SKILL.md
git commit -m "Add zed-reviewer skill (shared core + task/re-review/final scopes)"
```

---

### Task 8: Thin the OpenCode `sp-review` shell

**Files:**
- Modify: `harnesses/opencode/agents/sp-review.md` (replace the body; keep frontmatter)

**Interfaces:**
- Consumes: the existing `sp-review.md` at `harnesses/opencode/agents/`.
- Produces: a thin shell whose frontmatter is unchanged (structural read-only via `edit/task/skill: deny`) and whose body no longer restates review discipline carried by the templates the controller passes.

- [ ] **Step 1: Replace the body of `harnesses/opencode/agents/sp-review.md`**

Keep the YAML frontmatter exactly as-is (mode: subagent; read/glob/grep/list/bash: allow; edit/task/skill/webfetch/websearch: deny). Replace everything after the closing `---` with:

```markdown
You are an independent, read-only reviewer. You never modify files.

The controller passes you a filled review template (task / re-review / final
whole-branch) plus the review package, brief, and report file paths. Follow
that template exactly and report your findings clearly. Do not dispatch
subagents or load skills.
```

- [ ] **Step 2: Verify**

```bash
sed -n '1,30p' harnesses/opencode/agents/sp-review.md
```
Expected: unchanged frontmatter, then the new two-paragraph body.

- [ ] **Step 3: Commit**

```bash
git add harnesses/opencode/agents/sp-review.md
git commit -m "Thin sp-review shell (review discipline lives in templates)"
```

---

### Task 9: `tkt/install.py` — install functions (TDD)

**Files:**
- Create: `tkt/install.py`
- Create: `tests/test_install.py`

**Interfaces:**
- Produces: `install_zed_agent(repo_root=None, home=None, *, dry_run=False, confirm=None)` and `install_opencode_agent(repo_root=None, home=None, *, dry_run=False)`.
  - `home` defaults to `os.path.expanduser("~")`; injectable for tests.
  - `confirm` is a `Callable[[str], bool]` used for stale-symlink removal; defaults to a no-op `False`.
  - `repo_root` defaults to the repo root (parent of the `tkt/` package).
- Consumed by: the click commands in Task 10.

- [ ] **Step 1: Write the failing tests (`tests/test_install.py`)**

```python
# Copyright 2020-2026 Jim Bosch
# (BSD-3-Clause header — same as other test modules; preserve exact header)

import os

from tkt.install import install_opencode_agent, install_zed_agent

ZED_SKILLS = ("zed-primary-agent", "zed-explorer", "zed-implementer", "zed-reviewer")


def _make_repo(root: str) -> None:
    skills = os.path.join(root, "harnesses", "zed", "skills")
    for name in ZED_SKILLS:
        d = os.path.join(skills, name)
        os.makedirs(d)
        with open(os.path.join(d, "SKILL.md"), "w") as f:
            f.write(f"# {name}\n")
    os.makedirs(os.path.join(root, "harnesses", "zed"))
    with open(os.path.join(root, "harnesses", "zed", "rules.md"), "w") as f:
        f.write("# rules\n")
    os.makedirs(os.path.join(root, "harnesses", "opencode", "agents"))
    with open(os.path.join(root, "harnesses", "opencode", "agents", "sp-build.md"), "w") as f:
        f.write("# sp-build\n")


def test_install_zed_agent_creates_symlinks(tmp_path):
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_zed_agent(repo_root=str(tmp_path / "repo"), home=home)
    for name in ZED_SKILLS:
        link = os.path.join(home, ".agents", "skills", name)
        assert os.path.islink(link), link
        assert os.readlink(link) == os.path.join(str(tmp_path / "repo"), "harnesses", "zed", "skills", name)
    rules = os.path.join(home, ".config", "zed", "AGENTS.md")
    assert os.path.islink(rules)
    assert os.readlink(rules) == os.path.join(str(tmp_path / "repo"), "harnesses", "zed", "rules.md")


def test_install_zed_agent_dry_run_writes_nothing(tmp_path):
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_zed_agent(repo_root=str(tmp_path / "repo"), home=home, dry_run=True)
    assert not os.path.exists(os.path.join(home, ".agents"))


def test_install_zed_agent_removes_stale_symlink_when_confirmed(tmp_path):
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    skills_dst = os.path.join(home, ".agents", "skills")
    os.makedirs(skills_dst)
    stale = os.path.join(skills_dst, "zed-old-name")
    os.symlink("/somewhere/old", stale)
    install_zed_agent(repo_root=str(tmp_path / "repo"), home=home, confirm=lambda m: True)
    assert not os.path.lexists(stale)
    assert os.path.islink(os.path.join(skills_dst, "zed-explorer"))


def test_install_zed_agent_keeps_stale_when_not_confirmed(tmp_path):
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    skills_dst = os.path.join(home, ".agents", "skills")
    os.makedirs(skills_dst)
    stale = os.path.join(skills_dst, "zed-old-name")
    os.symlink("/somewhere/old", stale)
    install_zed_agent(repo_root=str(tmp_path / "repo"), home=home, confirm=lambda m: False)
    assert os.path.islink(stale)


def test_install_zed_agent_idempotent(tmp_path):
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_zed_agent(repo_root=str(tmp_path / "repo"), home=home)
    install_zed_agent(repo_root=str(tmp_path / "repo"), home=home)
    link = os.path.join(home, ".agents", "skills", "zed-explorer")
    assert os.path.islink(link)
    assert os.readlink(link) == os.path.join(str(tmp_path / "repo"), "harnesses", "zed", "skills", "zed-explorer")


def test_install_opencode_agent_repoints_symlink(tmp_path):
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    dst_dir = os.path.join(home, ".config", "opencode")
    os.makedirs(dst_dir)
    dst = os.path.join(dst_dir, "agents")
    os.symlink(os.path.join(str(tmp_path / "repo"), "agents", "opencode"), dst)
    install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home)
    assert os.path.islink(dst)
    assert os.readlink(dst) == os.path.join(str(tmp_path / "repo"), "harnesses", "opencode", "agents")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_install.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tkt.install'`.

- [ ] **Step 3: Write the minimal implementation (`tkt/install.py`)**

```python
# Copyright 2020-2026 Jim Bosch
# (BSD-3-Clause header — copy the full header from an existing module, e.g.
#  tkt/_cli.py lines 1-23, and paste it here verbatim.)

from __future__ import annotations

import logging
import os
from collections.abc import Callable

__all__ = ("install_zed_agent", "install_opencode_agent")


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ensure_link(link: str, target: str, *, dry_run: bool) -> None:
    """Make ``link`` a symlink to absolute ``target``.

    Idempotent: leaves a correct link alone, replaces a stale symlink, and
    refuses to clobber a real file or directory.
    """
    if os.path.islink(link):
        if os.path.realpath(link) == os.path.realpath(target):
            logging.info(f"OK: {link} -> {target}")
            return
        logging.warning(f"Replacing stale symlink {link} -> {os.readlink(link)}")
        if not dry_run:
            os.remove(link)
    elif os.path.lexists(link):
        logging.warning(f"Refusing to replace non-symlink {link}")
        return
    verb = "Would link" if dry_run else "Linking"
    logging.info(f"{verb} {link} -> {target}")
    if not dry_run:
        os.symlink(target, link)


def _clean_stale_links(
    directory: str, keep: set[str], *, dry_run: bool, confirm: Callable[[str], bool]
) -> None:
    """Warn about, and offer to remove, symlinks in ``directory`` not in ``keep``."""
    if not os.path.isdir(directory):
        return
    for name in sorted(os.listdir(directory)):
        if name in keep:
            continue
        path = os.path.join(directory, name)
        if not os.path.islink(path):
            logging.warning(f"Stale entry not managed by tkt (not a symlink; leaving it): {path}")
            continue
        logging.warning(f"Stale symlink not managed by tkt: {path}")
        if not dry_run and confirm(f"Remove stale symlink {path}?"):
            os.remove(path)


def install_zed_agent(
    repo_root: str | None = None,
    home: str | None = None,
    *,
    dry_run: bool = False,
    confirm: Callable[[str], bool] | None = None,
) -> None:
    """Symlink the Zed harness skills and rules into the user's Zed config.

    Creates ``~/.agents/skills/<name>`` for each ``harnesses/zed/skills/<name>``
    AND for each shared superpowers skill (``superpowers/skills/<name>``), and
    ``~/.config/zed/AGENTS.md`` -> ``harnesses/zed/rules.md``. Warns about
    (and, when confirmed, removes) stale symlinks under ``~/.agents/skills``
    that this command no longer manages.
    """
    repo_root = repo_root or _repo_root()
    home = home or os.path.expanduser("~")
    confirm = confirm or (lambda msg: False)
    skills_src = os.path.join(repo_root, "harnesses", "zed", "skills")
    skills_dst = os.path.join(home, ".agents", "skills")
    zed_cfg = os.path.join(home, ".config", "zed")
    if not dry_run:
        os.makedirs(skills_dst, exist_ok=True)
        os.makedirs(zed_cfg, exist_ok=True)
    managed: set[str] = set()
    if os.path.isdir(skills_src):
        for name in sorted(os.listdir(skills_src)):
            src = os.path.join(skills_src, name)
            if not os.path.isdir(src):
                continue
            managed.add(name)
            _ensure_link(os.path.join(skills_dst, name), src, dry_run=dry_run)
    _ensure_link(
        os.path.join(zed_cfg, "AGENTS.md"),
        os.path.join(repo_root, "harnesses", "zed", "rules.md"),
        dry_run=dry_run,
    )
    _clean_stale_links(skills_dst, managed, dry_run=dry_run, confirm=confirm)


def install_opencode_agent(
    repo_root: str | None = None,
    home: str | None = None,
    *,
    dry_run: bool = False,
) -> None:
    """Symlink the OpenCode harness agents dir into the user's OpenCode config.

    Makes ``~/.config/opencode/agents`` a symlink to
    ``harnesses/opencode/agents``, replacing any stale symlink.
    """
    repo_root = repo_root or _repo_root()
    home = home or os.path.expanduser("~")
    src = os.path.join(repo_root, "harnesses", "opencode", "agents")
    dst_dir = os.path.join(home, ".config", "opencode")
    if not dry_run:
        os.makedirs(dst_dir, exist_ok=True)
    _ensure_link(os.path.join(dst_dir, "agents"), src, dry_run=dry_run)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_install.py -v`
Expected: PASS (all 6 tests).

- [ ] **Step 5: Run lint and type checks**

```bash
ruff check tkt/install.py tests/test_install.py
ruff format --check tkt/install.py tests/test_install.py
mypy tkt/install.py
```
Expected: clean. If ruff format complains about a single-line docstring closing
quote, shorten the line or move the last word to the second line (see AGENTS.md
gotcha).

- [ ] **Step 6: Commit**

```bash
git add tkt/install.py tests/test_install.py
git commit -m "Add tkt install helpers for Zed and OpenCode harness symlinks"
```

---

### Task 10: Wire `tkt install-zed-agent` / `tkt install-opencode-agent`

**Files:**
- Modify: `tkt/_cli.py` (add two `@cli.command` functions near `fix_openspec`)

**Interfaces:**
- Consumes: `install_zed_agent` and `install_opencode_agent` from `tkt/install.py` (Task 9).
- Produces: the two CLI commands the human runs in Task 13.

- [ ] **Step 1: Add the two commands to `tkt/_cli.py`**

Add after the `fix_openspec` command definition:

```python
@cli.command(
    "install-zed-agent",
    help=(
        "Symlink the Zed harness skills into ~/.agents/skills and rules.md into "
        "~/.config/zed/AGENTS.md. Warns about (and, with --yes, removes) stale "
        "entries under ~/.agents/skills."
    ),
)
@click.option("-n", "--dry-run", is_flag=True)
@click.option("--yes", "yes", is_flag=True, help="Remove stale entries without prompting.")
@click.option("-v", "--verbose", count=True)
def install_zed_agent(*, dry_run: bool = False, yes: bool = False, verbose: int = 0) -> None:
    _setup_logging(verbose)
    from .install import install_zed_agent as _install_zed

    confirm = (lambda msg: True) if yes else click.confirm
    _install_zed(dry_run=dry_run, confirm=confirm)


@cli.command(
    "install-opencode-agent",
    help=(
        "Symlink the OpenCode harness agents dir into ~/.config/opencode/agents, "
        "replacing any stale symlink."
    ),
)
@click.option("-n", "--dry-run", is_flag=True)
@click.option("-v", "--verbose", count=True)
def install_opencode_agent(*, dry_run: bool = False, verbose: int = 0) -> None:
    _setup_logging(verbose)
    from .install import install_opencode_agent as _install_opencode

    _install_opencode(dry_run=dry_run)
```

- [ ] **Step 2: Verify the commands are registered**

```bash
./bin/tkt --help | grep -E "install-(zed|opencode)-agent"
```
Expected: both `install-zed-agent` and `install-opencode-agent` listed.

- [ ] **Step 3: Smoke-test dry-run against a temp HOME**

```bash
tmp=$(mktemp -d); HOME=$tmp ./bin/tkt install-zed-agent --dry-run -v; HOME=$tmp ./bin/tkt install-opencode-agent --dry-run -v
```
Expected: logs "Would link ..." lines; no `$tmp` content created.

- [ ] **Step 4: Run lint and type checks**

```bash
ruff check tkt/_cli.py
ruff format --check tkt/_cli.py
mypy tkt/
```
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add tkt/_cli.py
git commit -m "Add tkt install-zed-agent and install-opencode-agent commands"
```

---

### Task 11: Lock `~/.config/opencode` to read-only in the sandbox

**Files:**
- Modify: `local.json` (sandbox tool mounts)

**Interfaces:**
- Consumes: the sandbox mount config; the `~/.config/opencode/agents` symlink is now version-controlled under `harnesses/`, so the sandbox only needs to read it.

> Follow-up (post-Task 13): Task 13 Step 4 found that skills reference their
> bundled `scripts/` from their installed `~/.agents/skills/<skill>` directory,
> which the sandbox's `$HOME` tmpfs hid. So `~/.agents/skills` was ALSO added
> to the sandbox `mounts.ro` (read-only). This supersedes the earlier
> "no `~/.agents` mount" decision.

- [ ] **Step 1: Move `~/.config/opencode` from `rw` to `ro` in `local.json`**

In the `sandbox` tool's `mounts`, change `"~/.config/opencode"` from the `"rw"` list to the `"ro"` list. The `"rw"` list should no longer contain `~/.config/opencode`; the `"ro"` list gains it (placed among the other `~/.config`-style read-only entries). Leave all other entries unchanged.

- [ ] **Step 2: Verify the JSON is still valid and the entry moved**

```bash
python -c "import json; d=json.load(open('local.json')); m=d['tools']['sandbox']['mounts']; print('ro:', '~/.config/opencode' in m['ro']); print('rw:', '~/.config/opencode' in m['rw'])"
```
Expected: `ro: True`, `rw: False`.

- [ ] **Step 3: Commit**

```bash
git add local.json
git commit -m "Sandbox: mount ~/.config/opencode read-only"
```

---

### Task 12: Update `AGENTS.md` for the new layout

**Files:**
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: the new `harnesses/` layout (Tasks 1-8).
- Produces: updated project instructions reflecting the move and the Zed harness, pointing at `harnesses/README.md`.

- [ ] **Step 1: Update the File layout table row for `agents/opencode/`**

Replace the row:

```
| `agents/opencode/`    | Custom OpenCode workflow agents `sp-design`, `sp-plan`, `sp-build`, `sp-debug`, `sp-review`; `~/.config/opencode/agents/` is a symlink to it. |
```

with:

```
| `harnesses/opencode/agents/` | Custom OpenCode workflow agents `sp-design`, `sp-plan`, `sp-build`, `sp-debug`, `sp-review`; `~/.config/opencode/agents/` is a symlink to it (via `tkt install-opencode-agent`). |
| `harnesses/zed/`       | Zed harness: `rules.md` (role-scoped dispatch table, symlinked to `~/.config/zed/AGENTS.md`) and `skills/<name>/` (Zed-only skills, symlinked to `~/.agents/skills/<name>`); see `harnesses/README.md` for content-placement rules. |
```

- [ ] **Step 2: Update the OpenCode integration section**

In the `## OpenCode integration` section, replace `agents/opencode` with `harnesses/opencode/agents` in the prose, and note that the symlink is created by `tkt install-opencode-agent`.

- [ ] **Step 3: Add a short Zed integration note**

Add a `## Zed integration` section after the OpenCode one:

```markdown
## Zed integration

The Zed native-agent harness lives in `harnesses/zed/`: `rules.md` is the
role-scoped dispatch table (the Zed global AGENTS.md) and `skills/` holds the
Zed-only skills. They are exposed via `tkt install-zed-agent`, which symlinks
`harnesses/zed/skills/<name>` to `~/.agents/skills/<name>` and
`harnesses/zed/rules.md` to `~/.config/zed/AGENTS.md`. See
`harnesses/README.md` for the content-placement rules.
```

- [ ] **Step 4: Verify**

```bash
grep -n "harnesses/opencode/agents\|harnesses/zed" AGENTS.md
```
Expected: the updated table rows and the new Zed section appear.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md
git commit -m "Document harnesses/ layout and Zed integration in AGENTS.md"
```

---

### Task 13: HUMAN — run installs and verify (no code changes)

This task is performed by the human (the sandbox cannot see `$HOME` Zed/agent
directories). Do NOT have an implementing subagent run it.

- [ ] **Step 1: Run both install commands**

```bash
tkt install-opencode-agent
tkt install-zed-agent
```
Confirm the symlinks: `ls -l ~/.config/opencode/agents ~/.agents/skills ~/.config/zed/AGENTS.md` show symlinks into the `harnesses/` tree. Run `tkt install-zed-agent` a second time to confirm idempotence.

- [ ] **Step 2: Confirm OpenCode still works**

Run a normal OpenCode session (e.g. `tkt sandbox-run` or an OpenCode agent use) and confirm the `sp-*` agents load and behave as before. This also validates the Task 11 change (`~/.config/opencode` read-only in the sandbox).

- [ ] **Step 3: Smoke-test the Zed native agent**

Open Zed, start a native-agent thread, and confirm:
- The dispatch table loads: a primary agent reads `rules.md` (the `~/.config/zed/AGENTS.md` global prompt) and `zed-primary-agent` describes the flow.
- Invoking a phase skill (e.g. `brainstorming`) works.
- Subagent role skills resolve: dispatching a subagent to "explore the codebase" loads `zed-explorer`.

- [ ] **Step 4: Test that a Zed agent can run a superpowers script at its installed `~/.agents/skills` path**

This validates the sandbox change: `~/.agents/skills` is now `ro`-mounted into
the sandbox so a skill's bundled `scripts/` are reachable exactly where the
skills expect them (they were written assuming the installed skill directory is
visible to the shell). Ask a Zed native agent to load the
`subagent-driven-development` skill, then run a superpowers script and report
the resolved path.

Concrete example to try: run
`scripts/sdd-workspace docs/superpowers/plans/2026-09-01-r1-prompts-to-skills.md`
from the skill's directory.

Check: the agent can find and run the script, resolving it under
`~/.agents/skills/subagent-driven-development/scripts/sdd-workspace` (the
`ro`-mounted installed location), and the run succeeds. Report the resolved
path the agent used and whether it ran. If the agent instead fails to reach the
`~/.agents/skills` path, that is a finding to bring back (e.g. the `ro`-bind
did not shadow the `$HOME` tmpfs, or the symlinks into `tkt2` did not resolve).

- [ ] **Step 5: Confirm `~/.config/zed/AGENTS.md` is the global-prompt mechanism**

In the Zed smoke test, confirm the `rules.md` dispatch table is actually read as the global agent prompt (the agent behaves per the table). This validates the `~/.config/zed/AGENTS.md` naming assumption.

---

### Task 14: HUMAN — remove the old OpenCode agents directory (after verifying the install)

This task is performed by the human, only after Task 13's `install-opencode-agent`
has re-pointed `~/.config/opencode/agents` to `harnesses/opencode/agents` and
OpenCode has been confirmed working from the new location. Do NOT run this
before verifying Task 13 Step 2.

- [ ] **Step 1: Confirm the symlink points at the new location**

```bash
readlink ~/.config/opencode/agents
```
Expected: `.../tkt2/harnesses/opencode/agents`.

- [ ] **Step 2: Confirm OpenCode loads the agents from the new location**

Open a normal OpenCode session and confirm the `sp-*` agents load and behave as before. (If anything still resolves through the old path, stop and investigate before deleting.)

- [ ] **Step 3: Delete the old, now-superseded directory**

```bash
git rm -r agents/opencode
```

- [ ] **Step 4: Verify nothing still references the old path**

```bash
grep -rn "agents/opencode" --include=*.py --include=*.sh --include=*.md --include=*.json* . | grep -v "\.git/" | grep -v investigations/
```
Expected: no matches (AGENTS.md was updated in Task 12).

- [ ] **Step 5: Commit**

```bash
git commit -m "Remove superseded agents/opencode (now harnesses/opencode/agents)"
```

---

## Self-review notes

- Spec coverage: dispatch table (T3), primary flow (T4), subagent roles (T5/T6/T7), sp-review thinning (T8), install commands (T9/T10), sandbox rw->ro (T11), rules doc + AGENTS.md (T2/T12), human verification incl. the superpowers-script test (T13), old-dir cleanup after verified install (T14). All sections of the design handover are covered.
- No placeholders: each task contains actual file content to write.
- Type consistency: `install_zed_agent`/`install_opencode_agent` signatures match between T9 (definition) and T10 (call sites) and T13 (CLI usage); `home`/`repo_root`/`dry_run`/`confirm` names are consistent.
- Coexistence safety: the original `agents/opencode` stays intact until Task 14, so the current OpenCode symlink keeps working throughout the build and only the human removes the old directory after verifying the new install.
