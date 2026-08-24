# Superpowers Workflow for tkt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make superpowers the default change-control workflow for tkt (in multi-repo DM workspaces and single-repo projects), storing durable docs in a shared git repo namespaced by DM-XXXXX, while keeping OpenSpec installed and providing a migration path.

**Architecture:** The superpowers fork skills read `SUPERPOWERS_DIR` and, when it is set, write specs/plans under it instead of the in-repo `docs/superpowers/`. A tkt `Superpowers` tool creates a per-ticket namespace in a shared repo (`~/LSST/superpowers-docs/<ticket>/{specs,plans}`) and the workspace EUPS table sets `SUPERPOWERS_DIR` to that namespace, so an in-sandbox agent writes docs into the shared repo. A `Tool.remove()` hook plus an `update`-time prompt let an OpenSpec workspace migrate (clean up openspec artifacts) while declining the prompt keeps the tools coexisting.

**Tech Stack:** Python 3.13 (click, GitPython), EUPS table files, Markdown skill files, opencode agent/config YAML+JSONC.

**Spec:** `docs/superpowers/specs/2026-08-24-superpowers-workflow-design.md`

## Global Constraints

- OpenSpec stays installed in `local.json` `tools` (non-goal: do not remove it); only `default_tools` swaps `openspec` → `superpowers`.
- The shared repo `~/LSST/superpowers-docs` is a **plain git repo, not an EUPS product**.
- `SUPERPOWERS_DIR` namespacing: workspace table sets it to `<shared-path>/<ticket>` (literal path), matching the existing `setupRequired(tkt -r {tkt_dir})` pattern.
- `Tool.remove(directory)` is a default no-op; `Superpowers` keeps the no-op (its namespace dirs live in the shared repo and are not destroyed on removal).
- Coexistence = declining the `update` removal prompt.
- All `.py` files carry the BSD-3-Clause license header (copy from an existing `tkt/*.py`).
- Do not change sandbox bridge behavior.
- Every `Task` ends with a commit. `ruff check .`, `ruff format --check .`, `mypy tkt/`, and `python -m pytest` must stay green.

### Sandbox checkpoint note (READ FIRST)

`/home/jbosch` in this sandbox is a tmpfs with real bind points layered on top.
The shared repo `~/LSST/superpowers-docs` is **not** bound into the current
sandbox and does **not** exist on the host yet. Therefore:

- The shared repo **must be created on the host** (by the human) before the
  sandbox is reloaded — bwrap needs the source to exist to bind it. Do not try
  to scaffold it inside the sandbox; anything written to `~/LSST/superpowers-docs`
  in-sandbox is a phantom tmpfs dir and will not persist.
- The rw mount of the shared repo only takes effect after a **sandbox reload**.
  Execution pauses at **Task 11 (CHECKPOINT)** for the human to scaffold on the
  host and reload the sandbox; all tasks after it assume the mount is present.

---

## Task 1: Fork — brainstorming skill honors `SUPERPOWERS_DIR`

**Files:**
- Modify: `~/.config/opencode/superpowers/skills/brainstorming/SKILL.md` (lines 100 and 206-207)
- Test: grep verification (this is a Markdown skill file, not code)

**Interfaces:**
- Consumes: nothing.
- Produces: the `brainstorming` skill text directs the agent to save specs to `$SUPERPOWERS_DIR/specs/` when the env var is set, else in-repo `docs/superpowers/specs/`.

- [ ] **Step 1: Verify current text**

Read `~/.config/opencode/superpowers/skills/brainstorming/SKILL.md`. Confirm lines 100 and 206-207 currently reference `docs/superpowers/specs/`.

- [ ] **Step 2: Edit the "Documentation" section (line 206)**

Replace:

```markdown
- Write the validated design (spec) to `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`
  - (User preferences for spec location override this default)
```

with:

```markdown
- Write the validated design (spec) to `specs/` under the docs root: if the
  `SUPERPOWERS_DIR` environment variable is set it points at the shared docs
  repo's per-ticket namespace, otherwise fall back to in-repo. I.e. save to
  `$SUPERPOWERS_DIR/specs/YYYY-MM-DD-<topic>-design.md` if set, else
  `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`, creating the target
  directory as needed.
```

- [ ] **Step 3: Edit the checklist item (line 100)**

Replace:

```markdown
6. **Write design doc** — save to `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` and commit
```

with:

```markdown
6. **Write design doc** — save to `$SUPERPOWERS_DIR/specs/` when `SUPERPOWERS_DIR` is set, otherwise `docs/superpowers/specs/` (path `YYYY-MM-DD-<topic>-design.md`), and commit
```

- [ ] **Step 4: Verify the change**

Run:

```bash
grep -n "SUPERPOWERS_DIR" ~/.config/opencode/superpowers/skills/brainstorming/SKILL.md
grep -n "docs/superpowers/specs" ~/.config/opencode/superpowers/skills/brainstorming/SKILL.md
```

Expected: both `SUPERPOWERS_DIR` and `docs/superpowers/specs` appear (fallback preserved).

- [ ] **Step 5: Commit**

```bash
git -C ~/.config/opencode/superpowers add skills/brainstorming/SKILL.md
git -C ~/.config/opencode/superpowers commit -m "feat(brainstorming): honor SUPERPOWERS_DIR for spec location"
```

---

## Task 2: Fork — writing-plans skill honors `SUPERPOWERS_DIR`

**Files:**
- Modify: `~/.config/opencode/superpowers/skills/writing-plans/SKILL.md` (lines 20-21 and 159)
- Test: grep verification

**Interfaces:**
- Consumes: nothing.
- Produces: the `writing-plans` skill text directs the agent to save plans to `$SUPERPOWERS_DIR/plans/` when set, else in-repo `docs/superpowers/plans/`.

- [ ] **Step 1: Edit the "Save plans to" line (line 20-21)**

Replace:

```markdown
**Save plans to:** `docs/superpowers/plans/YYYY-MM-DD-<feature-name>.md`
- (User preferences for plan location override this default)
```

with:

```markdown
**Save plans to:** `$SUPERPOWERS_DIR/plans/YYYY-MM-DD-<feature-name>.md` if the
`SUPERPOWERS_DIR` environment variable is set (it points at the shared docs
repo's per-ticket namespace), otherwise
`docs/superpowers/plans/YYYY-MM-DD-<feature-name>.md` (in-repo default).
```

- [ ] **Step 2: Edit the handoff line (line 159)**

Replace:

```markdown
**"Plan complete and saved to `docs/superpowers/plans/<filename>.md`. Switch to
```

with:

```markdown
**"Plan complete and saved to `$SUPERPOWERS_DIR/plans/<filename>.md` (or `docs/superpowers/plans/<filename>.md` if `SUPERPOWERS_DIR` is unset). Switch to
```

- [ ] **Step 3: Verify the change**

Run:

```bash
grep -n "SUPERPOWERS_DIR" ~/.config/opencode/superpowers/skills/writing-plans/SKILL.md
grep -n "docs/superpowers/plans" ~/.config/opencode/superpowers/skills/writing-plans/SKILL.md
```

Expected: both `SUPERPOWERS_DIR` and `docs/superpowers/plans` appear.

- [ ] **Step 4: Commit**

```bash
git -C ~/.config/opencode/superpowers add skills/writing-plans/SKILL.md
git -C ~/.config/opencode/superpowers commit -m "feat(writing-plans): honor SUPERPOWERS_DIR for plan location"
```

---

## Task 3: Fork — open `edit` and `bash` for `sp-brainstorm` and `sp-plan`

**Files:**
- Modify: `~/.config/opencode/superpowers/.opencode/agents/sp-brainstorm.md`
- Modify: `~/.config/opencode/superpowers/.opencode/agents/sp-plan.md`
- Test: grep verification

**Interfaces:**
- Consumes: nothing.
- Produces: `sp-brainstorm`/`sp-plan` allow full `edit` and `bash`, so they can write to the external shared repo and `git add`/`git commit` their own docs without prompting.

- [ ] **Step 1: Edit `sp-brainstorm.md` permission block**

In `~/.config/opencode/superpowers/.opencode/agents/sp-brainstorm.md`, replace the `permission:` block:

```yaml
permission:
  read: allow
  glob: allow
  grep: allow
  list: allow
  bash:
    "*": ask
    "git": allow
  question: allow
  skill: allow
  task: allow
  webfetch: ask
  websearch: ask
  edit:
    "*": deny
    "docs/superpowers/specs/*.md": allow
```

with:

```yaml
permission:
  read: allow
  glob: allow
  grep: allow
  list: allow
  bash: allow
  question: allow
  skill: allow
  task: allow
  webfetch: ask
  websearch: ask
  edit: allow
```

- [ ] **Step 2: Edit `sp-plan.md` permission block**

In `~/.config/opencode/superpowers/.opencode/agents/sp-plan.md`, apply the same replacement, but note its `edit` allow line is `"docs/superpowers/plans/*.md"` (plans, not specs). Resulting block is identical to Step 1's output.

- [ ] **Step 3: Verify**

Run:

```bash
grep -n -A9 "^permission:" ~/.config/opencode/superpowers/.opencode/agents/sp-brainstorm.md
grep -n -A9 "^permission:" ~/.config/opencode/superpowers/.opencode/agents/sp-plan.md
```

Expected: `bash: allow` and `edit: allow` present in both; no `"*": deny` edit line.

- [ ] **Step 4: Commit**

```bash
git -C ~/.config/opencode/superpowers add .opencode/agents/sp-brainstorm.md .opencode/agents/sp-plan.md
git -C ~/.config/opencode/superpowers commit -m "feat(agents): open edit and bash for sp-brainstorm and sp-plan"
```

---

## Task 4: opencode.jsonc — allow the shared repo as an external directory

**Files:**
- Modify: `~/.config/opencode/opencode.jsonc` (`permission.external_directory`)
- Test: json5 parse verification

**Interfaces:**
- Consumes: nothing.
- Produces: opencode permits agent writes to the shared repo outside any project root.

- [ ] **Step 1: Edit `opencode.jsonc`**

In `~/.config/opencode/opencode.jsonc`, under `permission.external_directory`, add an entry alongside the existing `/home/jbosch/LSST/openspec/**`, `/home/jbosch/LSST/install/**`, and `/tmp/**` entries:

```json
{
  "permission": {
    "external_directory": {
      "/home/jbosch/LSST/openspec/**": "allow",
      "/home/jbosch/LSST/install/**": "allow",
      "/tmp/**": "allow",
      "/home/jbosch/LSST/superpowers-docs/**": "allow"
    }
  }
}
```

(Only add the last line; keep the file's existing trailing-comma style.)

- [ ] **Step 2: Verify it still parses as json5**

Run:

```bash
cd /home/jbosch/LSST/tkt2 && python -c "from tkt.utils import read_json_file; d = read_json_file('/home/jbosch/.config/opencode/opencode.jsonc'); print(d['permission']['external_directory']['/home/jbosch/LSST/superpowers-docs/**'])"
```

Expected: prints `allow`.

- [ ] **Step 3: Note (no commit needed)**

`opencode.jsonc` is a global config file, not part of a git repo (`~/.config/opencode` is not a git repository), so no commit is required. Verify only (Step 2).

---

## Task 5: tkt — add `Tool.remove()` default no-op

**Files:**
- Modify: `tkt/_environment.py` (`Tool` ABC, after `write`)
- Test: `tests/test_tools.py` (new)

**Interfaces:**
- Consumes: nothing.
- Produces: `Tool.remove(self, directory: str) -> None`, a concrete method defaulting to a no-op. Later tasks (`OpenSpec.remove`, `update` removal) rely on it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tools.py`:

```python
from __future__ import annotations

import pytest

from tkt._environment import Environment, Tool


def test_tool_default_remove_is_noop(tmp_path):
    class _Tool(Tool):
        @classmethod
        def from_json_data(cls, data):
            return cls()

        def write(self, ticket, directory, packages, workspace, environment):
            pass

    tool = _Tool()
    tool.remove(str(tmp_path))  # must not raise
    assert list(tmp_path.iterdir()) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_tools.py -v`
Expected: FAIL with `AttributeError: 'Tool' object has no attribute 'remove'`.

- [ ] **Step 3: Add `remove` to the `Tool` ABC**

In `tkt/_environment.py`, inside `class Tool(ABC)`, after `write` (line 54), add:

```python
    def remove(self, directory: str) -> None:
        """Remove artifacts this tool wrote into a workspace. Default: no-op."""
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
cd /home/jbosch/LSST/tkt2 && ruff check . && ruff format --check . && mypy tkt/
git add tkt/_environment.py tests/test_tools.py
git commit -m "feat(environment): add default Tool.remove() no-op hook"
```

---

## Task 6: tkt — `OpenSpec.remove()` cleans up its artifacts

**Files:**
- Modify: `tkt/openspec.py` (add `remove`)
- Test: `tests/test_tools.py` (extend)

**Interfaces:**
- Consumes: `Tool.remove(directory)` from Task 5.
- Produces: `OpenSpec.remove(self, directory: str) -> None` deletes the workspace `openspec/` dir and `.opencode/skills/openspec-*` (and the `.opencode/skills` dir if it becomes empty).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools.py`:

```python
from tkt.openspec import OpenSpec


def test_openspec_remove_cleans_artifacts(tmp_path):
    (tmp_path / "openspec").mkdir()
    (tmp_path / "openspec" / "config.yaml").write_text("x")
    skills = tmp_path / ".opencode" / "skills"
    (skills / "openspec-apply-change" / "SKILL.md").parent.mkdir(parents=True)
    (skills / "openspec-apply-change" / "SKILL.md").write_text("x")
    (skills / "other" / "SKILL.md").parent.mkdir(parents=True)
    (skills / "other" / "SKILL.md").write_text("x")
    OpenSpec(store="lsst").remove(str(tmp_path))
    assert not (tmp_path / "openspec").exists()
    assert not (skills / "openspec-apply-change").exists()
    assert (skills / "other").exists()  # unrelated skills kept


def test_openspec_remove_empties_skills_dir(tmp_path):
    (tmp_path / "openspec").mkdir()
    skills = tmp_path / ".opencode" / "skills"
    (skills / "openspec-apply-change" / "SKILL.md").parent.mkdir(parents=True)
    (skills / "openspec-apply-change" / "SKILL.md").write_text("x")
    OpenSpec(store="lsst").remove(str(tmp_path))
    assert not skills.exists()  # removed once empty


def test_openspec_remove_missing_ok(tmp_path):
    OpenSpec(store="lsst").remove(str(tmp_path))  # no error if nothing present
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_tools.py -v`
Expected: FAIL with `AttributeError: 'OpenSpec' object has no attribute 'remove'`.

- [ ] **Step 3: Implement `OpenSpec.remove`**

In `tkt/openspec.py`, add inside `class OpenSpec`, after `write`:

```python
    def remove(self, directory: str) -> None:
        """Remove the OpenSpec artifacts this tool wrote into a workspace."""
        openspec_dir = os.path.join(directory, "openspec")
        if os.path.isdir(openspec_dir):
            shutil.rmtree(openspec_dir)
        skills_dir = os.path.join(directory, ".opencode", "skills")
        if os.path.isdir(skills_dir):
            for path in Path(skills_dir).glob("openspec-*"):
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            if not any(Path(skills_dir).iterdir()):
                shutil.rmtree(skills_dir)
```

(`os`, `shutil`, and `Path` are already imported in `tkt/openspec.py`.)

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_tools.py -v`
Expected: PASS (3 new + Task 5's test).

- [ ] **Step 5: Lint and commit**

```bash
cd /home/jbosch/LSST/tkt2 && ruff check . && ruff format --check . && mypy tkt/
git add tkt/openspec.py tests/test_tools.py
git commit -m "feat(openspec): add remove() to clean up workspace artifacts"
```

---

## Task 7: tkt — `Superpowers` tool

**Files:**
- Create: `tkt/superpowers.py`
- Test: `tests/test_superpowers.py` (new)

**Interfaces:**
- Consumes: `Tool` ABC from `tkt/_environment.py`.
- Produces: `Superpowers(Tool)` with `__init__(self, path: str)`, `path` attribute, `from_json_data(cls, data)`, and `write(ticket, directory, packages, workspace, environment)` that creates `<path>/<ticket>/specs` and `<path>/<ticket>/plans`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_superpowers.py`:

```python
from __future__ import annotations

import pytest

from tkt.superpowers import Superpowers


def test_from_json_data():
    tool = Superpowers.from_json_data({"path": "/shared"})
    assert isinstance(tool, Superpowers)
    assert tool.path == "/shared"


def test_from_json_data_rejects_extra():
    with pytest.raises(ValueError):
        Superpowers.from_json_data({"path": "/shared", "x": 1})


def test_from_json_data_requires_path():
    with pytest.raises(KeyError):
        Superpowers.from_json_data({})


def test_write_creates_namespace(tmp_path):
    sp = Superpowers(path=str(tmp_path))
    sp.write("DM-1", str(tmp_path / "ws"), [], workspace=object(), environment=object())
    assert (tmp_path / "DM-1" / "specs").is_dir()
    assert (tmp_path / "DM-1" / "plans").is_dir()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_superpowers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tkt.superpowers'`.

- [ ] **Step 3: Implement `tkt/superpowers.py`**

Create `tkt/superpowers.py` with the BSD-3-Clause license header (copy from `tkt/openspec.py`), then:

```python
from __future__ import annotations

__all__ = ("Superpowers",)

import os
from collections.abc import Iterable
from typing import Any

from ._environment import Environment, Tool
from ._workspace import Workspace


class Superpowers(Tool):
    """Tool that gives a workspace a shared superpowers docs home.

    ``write`` creates the per-ticket namespace ``<path>/<ticket>/specs`` and
    ``<path>/<ticket>/plans`` in the shared repo so the ticket has a
    ready-to-use docs location.  The workspace EUPS table sets ``SUPERPOWERS_DIR``
    to ``<path>/<ticket>``, which the superpowers skills read.
    """

    def __init__(self, path: str):
        self.path = path

    @classmethod
    def from_json_data(cls, data: dict[str, Any]) -> Tool:
        path = data.pop("path")
        if data:
            raise ValueError(f"Unexpected entries in superpowers configuration: {data}.")
        return cls(path)

    def write(
        self,
        ticket: str,
        directory: str,
        packages: Iterable[str],
        workspace: Workspace,
        environment: Environment,
    ) -> None:
        namespace = os.path.join(self.path, ticket)
        os.makedirs(os.path.join(namespace, "specs"), exist_ok=True)
        os.makedirs(os.path.join(namespace, "plans"), exist_ok=True)
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_superpowers.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint and commit**

```bash
cd /home/jbosch/LSST/tkt2 && ruff check . && ruff format --check . && mypy tkt/
git add tkt/superpowers.py tests/test_superpowers.py
git commit -m "feat(superpowers): add Superpowers tool creating a per-ticket docs namespace"
```

---

## Task 8: tkt — workspace EUPS table sets `SUPERPOWERS_DIR`

**Files:**
- Modify: `tkt/_workspace.py` (add `_superpowers_env_line`, change `_write_eups_table` signature and its 3 call sites at lines 207, 226, 265)
- Test: `tests/test_superpowers.py` (extend)

**Interfaces:**
- Consumes: `Superpowers.path` from Task 7; `environment.get_tool`.
- Produces: `_superpowers_env_line(workspace_tools, ticket, get_tool) -> str | None`; `_write_eups_table(self, environment)` emits `envSet(SUPERPOWERS_DIR, <path>/<ticket>)` when `superpowers` is in tools.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_superpowers.py`:

```python
from tkt._workspace import Workspace
from tkt.superpowers import Superpowers


class _FakeEnv:
    def __init__(self, tools):
        self._tools = tools

    def get_tool(self, name):
        return self._tools.get(name)


def _workspace(tmp_path, tools):
    return Workspace(
        ticket="DM-1",
        directory=str(tmp_path),
        metapackage_name="m",
        metapackage_tag="t",
        packages={},
        externals={},
        workspace_eups_product="x",
        tools=tools,
    )


def test_write_eups_table_superpowers(tmp_path):
    ws = _workspace(tmp_path, ("superpowers",))
    ws._write_eups_table(_FakeEnv({"superpowers": Superpowers(path="/shared")}))
    text = (tmp_path / "ups" / "x.table").read_text()
    assert "envSet(SUPERPOWERS_DIR, /shared/DM-1)" in text


def test_write_eups_table_no_superpowers(tmp_path):
    ws = _workspace(tmp_path, ())
    ws._write_eups_table(_FakeEnv({}))
    text = (tmp_path / "ups" / "x.table").read_text()
    assert "SUPERPOWERS_DIR" not in text
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_superpowers.py -v`
Expected: FAIL with `TypeError: _write_eups_table() takes 1 positional argument but 2 were given`.

- [ ] **Step 3: Add the helper and change `_write_eups_table`**

In `tkt/_workspace.py`, add a module-level function (near the top, after imports):

```python
def _superpowers_env_line(
    workspace_tools: Iterable[str], ticket: str, get_tool: Any
) -> str | None:
    """Return the EUPS line setting SUPERPOWERS_DIR, or None if not applicable."""
    if "superpowers" not in workspace_tools:
        return None
    tool = get_tool("superpowers")
    if tool is None:
        return None
    return f"envSet(SUPERPOWERS_DIR, {tool.path}/{ticket})"
```

(`Iterable` is already imported in `tkt/_workspace.py`; it currently has no
`typing` import, so add `from typing import Any` to it for the `get_tool: Any`
annotation.)

Change the `_write_eups_table` signature and add the line. Replace:

```python
    def _write_eups_table(self) -> None:
```

with:

```python
    def _write_eups_table(self, environment: Environment) -> None:
```

Inside the `with open(...)` block, after the `for product in self._packages` loop (around line 301), add:

```python
            line = _superpowers_env_line(self._tools, self.ticket, environment.get_tool)
            if line is not None:
                f.write(line + "\n")
```

Update the three call sites (lines 207, 226, 265) from `self._write_eups_table()` to `self._write_eups_table(environment)`.

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_superpowers.py tests/test_tools.py tests/test_sandbox.py -v`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
cd /home/jbosch/LSST/tkt2 && ruff check . && ruff format --check . && mypy tkt/
git add tkt/_workspace.py tests/test_superpowers.py
git commit -m "feat(workspace): set SUPERPOWERS_DIR in the EUPS table for superpowers workspaces"
```

---

## Task 9: tkt — `update` prompts to remove no-longer-default tools

**Files:**
- Modify: `tkt/_cli.py` (`update` command, add `_classify_tools` helper)
- Test: `tests/test_superpowers.py` (extend)

**Interfaces:**
- Consumes: `Tool.remove(directory)` (Task 5), `OpenSpec.remove` (Task 6), `Workspace.directory`, `Workspace.remove_tools`.
- Produces: `_classify_tools(workspace_tools, default_tools, get_tool) -> tuple[list[str], list[str], list[str]]` returning `(missing, stale, nondefault)`. `update` prompts to remove `nondefault` tools and calls `env.get_tool(t).remove(workspace.directory)` on confirmation.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_superpowers.py`:

```python
from tkt._cli import _classify_tools


def test_classify_tools():
    def get_tool(name):
        return {"openspec": object(), "superpowers": object(), "zed": object()}.get(name)

    missing, stale, nondefault = _classify_tools(
        ["openspec", "zed", "removed"], ["superpowers", "zed"], get_tool
    )
    assert missing == ["superpowers"]
    assert stale == ["removed"]
    assert nondefault == ["openspec"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_superpowers.py::test_classify_tools -v`
Expected: FAIL with `ImportError: cannot import name '_classify_tools'`.

- [ ] **Step 3: Add `_classify_tools` and rewire `update`**

In `tkt/_cli.py`, add a module-level helper after `_setup_logging` (line 43):

```python
def _classify_tools(
    workspace_tools: Iterable[str], default_tools: Iterable[str], get_tool: Any
) -> tuple[list[str], list[str], list[str]]:
    """Split the workspace's tools into missing defaults, stale, and non-default.

    Returns (missing, stale, nondefault): missing defaults are default tools
    absent from the workspace; stale tools are no longer configured in the
    environment at all; nondefault tools are configured but no longer defaults
    (candidates for removal on migration).
    """
    workspace_tools = list(workspace_tools)
    missing = [t for t in default_tools if t not in workspace_tools]
    stale = [t for t in workspace_tools if get_tool(t) is None]
    nondefault = [t for t in workspace_tools if get_tool(t) is not None and t not in default_tools]
    return missing, stale, nondefault
```

(`Any` is not currently imported in `tkt/_cli.py`; add `from typing import Any, TextIO`.)

In the `update` command, replace the two lines (180-181):

```python
    missing = [t for t in env.default_tools if t not in workspace.tools]
    stale = [t for t in workspace.tools if env.get_tool(t) is None]
```

with:

```python
    missing, stale, nondefault = _classify_tools(
        workspace.tools, env.default_tools, env.get_tool
    )
```

In the `dry_run` branch (lines 182-188), after the `stale` loop, add:

```python
        for t in nondefault:
            logging.warning(f"Would prompt to remove non-default tool {t}.")
```

After the existing stale-removal block (lines 189-191), add the nondefault removal:

```python
    for t in nondefault:
        if click.confirm(
            f"Remove tool {t}? It is no longer a default (this also cleans up its artifacts)."
        ):
            tool = env.get_tool(t)
            if tool is not None:
                tool.remove(workspace.directory)
            workspace.remove_tools([t])
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_superpowers.py -v`
Expected: PASS (all superpowers tests, including the new one).

- [ ] **Step 5: Lint and commit**

```bash
cd /home/jbosch/LSST/tkt2 && ruff check . && ruff format --check . && mypy tkt/
git add tkt/_cli.py tests/test_superpowers.py
git commit -m "feat(cli): prompt to remove no-longer-default tools on update"
```

---

## Task 10: tkt — config wiring (`local.json`, `AGENTS.md.in`)

**Files:**
- Modify: `local.json` (`default_tools`, `tools`, `sandbox.mounts.rw`)
- Modify: `tkt/AGENTS.md.in` (replace the OpenSpec section)
- Test: json5 parse + grep verification

**Interfaces:**
- Consumes: `Superpowers` module (Task 7) so `tkt.superpowers` imports; `environment.get_tool("superpowers")` and the table `envSet` (Task 8).
- Produces: `local.json` selects superpowers as default; the sandbox rw-mounts the shared repo; `AGENTS.md.in` documents the superpowers layout.

- [ ] **Step 1: Edit `local.json` `default_tools`**

In `local.json`, change:

```json
"default_tools": ["zed", "pyright", "sandbox", "precommit", "openspec", "direnv"]
```

to (openspec → superpowers, order-preserving):

```json
"default_tools": ["zed", "pyright", "sandbox", "precommit", "superpowers", "direnv"]
```

- [ ] **Step 2: Add the `superpowers` tool config**

In `local.json`, under `"tools"`, add (keeping the existing `openspec` entry):

```json
"superpowers": {
  "module": "tkt.superpowers",
  "cls": "Superpowers",
  "path": "/home/jbosch/LSST/superpowers-docs"
}
```

- [ ] **Step 3: Add the shared repo to the sandbox rw mounts**

In `local.json`, under `"sandbox"."mounts"."rw"`, add the path:

```json
"~/LSST/superpowers-docs"
```

- [ ] **Step 4: Verify `local.json` parses and reads correctly**

Run:

```bash
cd /home/jbosch/LSST/tkt2 && python -c "
from tkt.utils import read_json_file
d = read_json_file('local.json')
assert 'superpowers' in d['default_tools']
assert 'openspec' not in d['default_tools']
assert 'openspec' in d['tools']  # still installed
assert d['tools']['superpowers']['path'] == '/home/jbosch/LSST/superpowers-docs'
assert '~/LSST/superpowers-docs' in d['tools']['sandbox']['mounts']['rw']
print('ok')
"
```

Expected: prints `ok`.

- [ ] **Step 5: Edit `tkt/AGENTS.md.in`**

Replace the section (lines 43-51):

```markdown
## Important: Ignore `allowedEditRoots` from OpenSpec

When you run `openspec status` or `openspec instructions apply`, the CLI
reports `allowedEditRoots` in `actionContext` pointing to the workspace root.
**This is misleading in a sandboxed environment.** The workspace root is
mounted read-only.

- Work on OpenSpec documents via its CLI tools, from the main workspace.
- Make _ALL_ other changes in `.agent/**`.
```

with:

```markdown
## Superpowers docs

Design specs and implementation plans for superpowers live under the docs root
set by `SUPERPOWERS_DIR` (a per-ticket namespace in the shared repo
`~/LSST/superpowers-docs`); if unset, use the in-repo `docs/superpowers/`. The
shared repo is mounted read-write. Do superpowers doc work there or in-repo as
appropriate; make _ALL_ code changes in `.agent/**`. Transient superpowers
scratch (`.superpowers/`) lives under `.agent/<repo-name>/` and is git-ignored.
```

- [ ] **Step 6: Verify**

Run:

```bash
cd /home/jbosch/LSST/tkt2 && grep -n "SUPERPOWERS_DIR\|superpowers-docs" tkt/AGENTS.md.in
```

Expected: matches present; no remaining `allowedEditRoots` / OpenSpec lines.

- [ ] **Step 7: Lint and commit**

```bash
cd /home/jbosch/LSST/tkt2 && ruff check . && ruff format --check . && mypy tkt/
git add local.json tkt/AGENTS.md.in
git commit -m "chore(config): make superpowers the default tool and document it for agents"
```

---

## Task 11: CHECKPOINT — scaffold shared repo on host + reload sandbox

**This task pauses execution for the human. Do not continue past it until the
human confirms the sandbox has been reloaded with the new mount.**

**Why:** The shared repo `~/LSST/superpowers-docs` is not bound into the current
sandbox and does not exist on the host yet. It must be created **on the host**
(the sandbox's `/home/jbosch` is a tmpfs with real binds; in-sandbox writes to
that path are phantom and do not persist), and the rw mount added in Task 10
only takes effect after a sandbox reload.

**Files:**
- Create (on the host): `~/LSST/superpowers-docs/` (git repo), with a `README.md`
- Test: verification of the mount inside the reloaded sandbox

**Interfaces:**
- Consumes: the `sandbox.mounts.rw` entry `~/LSST/superpowers-docs` from Task 10.
- Produces: a real, version-controlled shared repo on the host, mounted rw into
  the reloaded sandbox, ready for the `Superpowers` tool and `external_directory`
  to use.

- [ ] **Step 1: PAUSE — hand off to the human**

Stop here. Tell the human: "Checkpoint: the shared repo mount is now in
`local.json`. Please (1) create the shared repo on the host and (2) reload the
sandbox. On the host, run:"

```bash
mkdir -p ~/LSST/superpowers-docs && cd ~/LSST/superpowers-docs && git init -b main
```

- [ ] **Step 2: Human adds a README on the host**

Create `~/LSST/superpowers-docs/README.md` on the host:

```markdown
# superpowers-docs

Shared, version-controlled home for superpowers design specs and implementation
plans across tkt DM workspaces. Each ticket gets a namespace
`<ticket>/{specs,plans}`; the workspace EUPS table sets `SUPERPOWERS_DIR` to the
ticket's namespace so the superpowers skills write here. In single-repo projects
`SUPERPOWERS_DIR` is unset and docs stay in-repo under `docs/superpowers/`.
```

- [ ] **Step 3: Human commits on the host**

```bash
cd ~/LSST/superpowers-docs && git add README.md && git commit -m "chore: init shared superpowers docs repo"
```

- [ ] **Step 4: Human reloads the sandbox**

The human reloads the sandbox so `~/LSST/superpowers-docs` is bound read-write
(and the updated `local.json` takes effect). Confirm with the human before
resuming.

- [ ] **Step 5: Verify the mount in the reloaded sandbox**

Run (must be the reloaded sandbox):

```bash
test -w ~/LSST/superpowers-docs && echo "shared repo writable"
git -C ~/LSST/superpowers-docs status --short
git -C ~/LSST/superpowers-docs log --oneline -1
```

Expected: "shared repo writable"; clean status; one commit (`chore: init shared
superpowers docs repo`). If the mount is missing, stop and ask the human to
reload again.

- [ ] **Step 6: No commit** — this task makes no source changes.

---

## Task 12: End-to-end verification (manual)

**Files:**
- None (verification only)

**Interfaces:**
- Consumes: all prior tasks; the mounted shared repo from Task 11.

- [ ] **Step 1: Full test + lint pass**

Run from `/home/jbosch/LSST/tkt2`:

```bash
ruff check . && ruff format --check . && mypy tkt/ && python -m pytest
```

Expected: all clean; tests pass.

- [ ] **Step 2: Create a DM workspace and confirm the namespace + table line**

From the `tkt` command on PATH, create a scratch DM workspace (or reuse one), then verify:

```bash
ls ~/LSST/superpowers-docs/DM-XXXXX/specs ~/LSST/superpowers-docs/DM-XXXXX/plans
grep -n "SUPERPOWERS_DIR" <workspace>/ups/<product>.table
```

Expected: the namespace dirs exist; the table has `envSet(SUPERPOWERS_DIR, /home/jbosch/LSST/superpowers-docs/DM-XXXXX)`.

- [ ] **Step 3: Confirm the sandbox can write into the shared repo**

Start `tkt sandbox-run` for the workspace and, from inside the sandbox, confirm `SUPERPOWERS_DIR` is set and `~/LSST/superpowers-docs` is writable, and that an in-sandbox brainstorm writes its spec to `$SUPERPOWERS_DIR/specs/`.

- [ ] **Step 4: Confirm migration**

Run `tkt update` on an existing OpenSpec workspace (after the `default_tools` swap): confirm it adds superpowers and prompts to remove openspec; declining keeps both; confirming removes openspec and cleans `openspec/` + `.opencode/skills/openspec-*`.

- [ ] **Step 5: No commit** — this task makes no file changes.

---

## Plan complete

This plan implements `docs/superpowers/specs/2026-08-24-superpowers-workflow-design.md` end to end: fork skills + agent permissions, opencode external_directory, the shared repo scaffold (done on the host at the Task 11 checkpoint), the `Superpowers` tool and table `envSet`, the `Tool.remove`/`OpenSpec.remove` cleanup, the `update` migration prompt, config wiring, and manual verification. Execution pauses at **Task 11** so the human can scaffold the shared repo on the host and reload the sandbox before the mount-dependent tasks. Switch to the sp-implement agent to execute it.
