# Remove the Plannotator Wake Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the Plannotator wake layer (the `plannotator-wake.js` OpenCode plugin and all tkt wiring) and make the pull-based `status`/`result` feedback flow the documented expected path everywhere.

**Architecture:** Pure removal. The host session-services bridge (session socket, MCP session tools, registry) is untouched; feedback delivery becomes pull-only — the human prods the agent after reviewing, and the agent calls the `plannotator` tool's `action='status'` / `action='result'`. Installed copies of the plugin are removed on `tkt install-opencode-agent` via the existing `LEGACY_PLUGIN_NAMES` machinery.

**Tech Stack:** Python 3.13, pytest; a git submodule (`superpowers/`, TallJimbo fork) holding one skill wording change.

**Spec:** `docs/superpowers/specs/2026-10-20-remove-plannotator-wake-plugin-design.md` (includes the verified ACP root cause — read it first).

## Global Constraints

- Run `ruff check .`, `ruff format --check .`, `mypy tkt/`, `python -m pytest` before every commit (all green).
- ruff: line-length 110, doc-length 79, numpydoc convention.
- No packaging configuration — do not add any.
- Preserve the BSD-3-Clause license header on every `.py` file.
- Commit messages: one-line summary ≤72 chars, then at most 5-6 bullet points; no ticket numbers or planning context (this rule exempts the docs commit in Task 1, which commits the spec/plan themselves).
- Work on a fresh feature branch `remove-plannotator-wake` in the tkt2 repo.
- The `superpowers/` submodule is committed on its own `main` (existing repo pattern); the tkt2 pin-bump commit must reference a submodule commit that already exists.
- Historical docs under `docs/superpowers/specs|plans/` are never edited.
- Exact-text edits below were captured at plan-writing time; if a file has drifted, match semantically, never blindly.

---

### Task 1: Feature branch + commit spec and plan

**Files:**

- Commit (already written): `docs/superpowers/specs/2026-10-20-remove-plannotator-wake-plugin-design.md`, `docs/superpowers/plans/2026-10-20-remove-plannotator-wake-plugin.md`

**Interfaces:**

- Consumes: nothing.
- Produces: branch `remove-plannotator-wake` carrying the docs, as required by the repo Git Workflow.

- [ ] **Step 1: Create the branch** (from current `main`, clean tree)

```bash
git switch -c remove-plannotator-wake
```

- [ ] **Step 2: Commit the spec and plan**

```bash
git add docs/superpowers/specs/2026-10-20-remove-plannotator-wake-plugin-design.md \
        docs/superpowers/plans/2026-10-20-remove-plannotator-wake-plugin.md
git commit -m "Superpowers docs: spec and plan for removing the Plannotator wake plugin"
```

- [ ] **Step 3: Verify**

Run: `git log --oneline -1 && git status --short`
Expected: the docs commit on top, no untracked/modified tracked files.

---

### Task 2: Remove the Python wake wiring (install.py + sandbox.py + tests)

`install.py` and `sandbox.py` change together: `sandbox.py` imports
`wake_plugin_source/dest` from `install.py`, so removing the helpers in one
commit and the import in the next would leave a broken import between tasks.
TDD cycle: rewrite `test_install.py` first (new legacy-removal test fails),
then implement in both source files, then the full suite.

**Files:**

- Modify: `tests/test_install.py` (imports, `_make_repo`, `_plugin_dst`, plugin tests, dry-run test)
- Modify: `tests/test_sandbox.py` (delete `_wake_restricted_argv`, `_fake_wake_paths`, four wake tests: lines ~513-577)
- Modify: `tkt/install.py` (`PLUGIN_NAMES`, `LEGACY_PLUGIN_NAMES`, delete `wake_plugin_source`/`wake_plugin_dest`, docstring)
- Modify: `tkt/sandbox.py` (`import filecmp`, `from .install import ...`, `_warn_if_wake_plugin_stale`, its call site, `opencode_config_content` docstring)

**Interfaces:**

- Consumes: nothing.
- Produces: `tkt.install.PLUGIN_NAMES == ("zed-bell",)`; `tkt.install.LEGACY_PLUGIN_NAMES == ("zed-bell.js", "plannotator-wake.js")`; no `wake_plugin_source`/`wake_plugin_dest` symbols anywhere in `tkt/` or `tests/`.

- [ ] **Step 1: Rewrite the wake-specific parts of `tests/test_install.py`**

(a) Replace the import block

```python
from tkt.install import (
    install_opencode_agent,
    install_zed_agent,
    plugin_dest,
    plugin_source,
    wake_plugin_dest,
    wake_plugin_source,
)
```

with (only the two entry points remain used once the wake-helper test is
deleted — `plugin_source`/`plugin_dest` were used solely by it, and ruff flags
the rest as unused):

```python
from tkt.install import install_opencode_agent, install_zed_agent
```

(b) In `_make_repo`, delete the wake source fixture — remove these two lines
(keep the `plugin_dir` makedirs and everything else):

```python
    with open(os.path.join(plugin_dir, "plannotator-wake.js"), "w") as f:
        f.write("// wake plugin v1\n")
```

(c) Replace the `_plugin_dst`/`_wake_dst` helpers (drop the wake-defaulted
name and `_wake_dst`; every remaining call passes a name):

```python
def _plugin_dst(home: str, name: str) -> str:
    return os.path.join(home, ".config", "opencode", "plugin", name)
```

(d) Replace `test_install_opencode_agent_copies_plugins` (wake assertions out,
zed-bell only):

```python
def test_install_opencode_agent_copies_plugins(tmp_path):
    """Verify the zed-bell directory plugin is copied (real files, not
    symlinks) into the config dir, with both entrypoint files byte-exact.
    """
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home)
    bell = _plugin_dst(home, "zed-bell")
    assert os.path.isdir(bell)
    assert not os.path.islink(bell)
    for name, contents in (
        ("index.js", "// zed bell server half v1\n"),
        ("tui.js", "// zed bell tui half v1\n"),
    ):
        entry = os.path.join(bell, name)
        assert os.path.isfile(entry)
        assert not os.path.islink(entry)
        with open(entry) as f:
            assert f.read() == contents
```

(e) Delete these four tests outright: `test_install_opencode_agent_copies_wake_plugin`,
`test_wake_plugin_helpers_still_resolve`,
`test_install_opencode_agent_wake_plugin_replaces_stale`,
`test_install_opencode_agent_wake_plugin_replaces_symlink`.

(f) In `test_install_opencode_agent_plugins_idempotent`, drop the wake file
from `files` and the wake path from the log-loop — replace its body:

```python
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home)
    bell = _plugin_dst(home, "zed-bell")
    files = [os.path.join(bell, "index.js"), os.path.join(bell, "tui.js")]
    before = [os.stat(f).st_mtime_ns for f in files]
    with caplog.at_level(logging.INFO):
        install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home)
    assert [os.stat(f).st_mtime_ns for f in files] == before
    assert any("up to date" in record.getMessage() and bell in record.getMessage() for record in caplog.records)
```

(If that last assert line exceeds 110 chars, wrap it the way ruff format
prefers — same content.)

(g) Add the new legacy-removal test directly after
`test_install_opencode_agent_removes_legacy_zed_bell_file`:

```python
def test_install_opencode_agent_removes_legacy_wake_plugin(tmp_path, caplog):
    """Verify the wake plugin's host copy (removed feature) is deleted on
    install, so the OpenCode plugin autoload cannot keep loading it.
    """
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    wake = _plugin_dst(home, "plannotator-wake.js")
    os.makedirs(os.path.dirname(wake))
    with open(wake, "w") as f:
        f.write("// wake plugin v1\n")
    with caplog.at_level(logging.WARNING):
        install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home)
    assert not os.path.lexists(wake)
    assert any("legacy" in record.getMessage().lower() for record in caplog.records)
    # The surviving plugin still installs.
    assert os.path.isfile(os.path.join(_plugin_dst(home, "zed-bell"), "tui.js"))
```

(h) Replace `test_install_opencode_agent_plugins_dry_run_writes_nothing` so
dry run also leaves a seeded legacy wake copy in place:

```python
def test_install_opencode_agent_plugins_dry_run_writes_nothing(tmp_path):
    """Verify dry run does not create the plugin dir, copy the plugins, or
    remove legacy single files.
    """
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    plugin_dir = os.path.dirname(_plugin_dst(home, "zed-bell.js"))
    os.makedirs(plugin_dir)
    legacy = _plugin_dst(home, "zed-bell.js")
    with open(legacy, "w") as f:
        f.write("// legacy single-file bell\n")
    wake = _plugin_dst(home, "plannotator-wake.js")
    with open(wake, "w") as f:
        f.write("// wake plugin v1\n")
    install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home, dry_run=True)
    assert not os.path.lexists(_plugin_dst(home, "zed-bell"))
    assert os.path.isfile(legacy)
    assert os.path.isfile(wake)
```

- [ ] **Step 2: Run the install tests to see the new failure**

Run: `python -m pytest tests/test_install.py -x -q`
Expected: `test_install_opencode_agent_removes_legacy_wake_plugin` FAILS
(the file is still present — wake is not in `LEGACY_PLUGIN_NAMES` yet).
Note (post-execution correction): the other opencode-agent tests also error
at this intermediate point — Step 1(b) already deleted the fixture that
`PLUGIN_NAMES` still references (FileNotFoundError). That is still a genuine
RED; the full pass of the non-target tests is asserted at Step 6 instead.

- [ ] **Step 3: Delete the wake tests from `tests/test_sandbox.py`**

Delete, as one contiguous block, the helper `_wake_restricted_argv`, the
helper `_fake_wake_paths`, and the four tests
`test_wake_plugin_warning_when_host_copy_missing`,
`test_wake_plugin_warning_when_host_copy_stale`,
`test_wake_plugin_no_warning_when_host_copy_matches`,
`test_wake_plugin_no_check_for_full_network_run` (lines ~513-577, ending just
before `test_inner_script_has_no_vc_socat`). Keep everything else.

- [ ] **Step 4: Implement in `tkt/install.py`**

(a) Replace the `PLUGIN_NAMES` value line

```python
PLUGIN_NAMES: tuple[str, ...] = ("plannotator-wake.js", "zed-bell")
```

with

```python
PLUGIN_NAMES: tuple[str, ...] = ("zed-bell",)
```

(b) Replace the `LEGACY_PLUGIN_NAMES` value line

```python
LEGACY_PLUGIN_NAMES: tuple[str, ...] = ("zed-bell.js",)
```

with

```python
LEGACY_PLUGIN_NAMES: tuple[str, ...] = ("zed-bell.js", "plannotator-wake.js")
```

(c) Delete the two now-unused helpers `wake_plugin_source` and
`wake_plugin_dest` entirely (the two `def` blocks and their docstrings,
between `plugin_dest` and `_ensure_link`). Keep `plugin_source`,
`plugin_dest`, and everything else untouched.

(d) In `install_opencode_agent`'s docstring, replace
`autoloaded plugins (wake + zed-bell) into` with `autoloaded plugins (zed-bell) into`.

- [ ] **Step 5: Implement in `tkt/sandbox.py`**

(a) Delete the line `import filecmp` (module imports; it has no other user).

(b) Delete the line `from .install import wake_plugin_dest, wake_plugin_source`.

(c) Delete the whole `_warn_if_wake_plugin_stale` function (docstring
included, lines ~116-133).

(d) In `_build_bwrap_argv`'s restricted-session-bridge block, delete the wake
comment and the call — remove exactly these lines (the two `argv +=` calls
above them and the `# Namespaces and cleanup.` comment below stay):

```python
            # The wake plugin reaches the sandbox only via the host config
            # dir's plugin autoload (a copied real file); warn when that
            # copy is missing or stale so feedback delivery does not fail
            # silently.  Non-fatal: the session MCP tools still work without
            # the wake layer.
            _warn_if_wake_plugin_stale()
```

(e) In the `opencode_config_content` docstring, replace

```python
    through this channel: the ``plugin`` array in env config is NOT
    honored by the installed OpenCode (its entries are npm package
    specs), so the wake plugin is delivered as a real copied file in
    ``~/.config/opencode/plugin/`` instead (see tkt.install).
```

with

```python
    through this channel: the ``plugin`` array in env config is NOT
    honored by the installed OpenCode (its entries are npm package
    specs), so autoloaded plugins are delivered as real copied files in
    ``~/.config/opencode/plugin/`` instead (see tkt.install).
```

- [ ] **Step 6: Run the full gate**

Run: `python -m pytest` — expected: all pass (wake tests gone or green).
Run: `ruff check . && ruff format --check . && mypy tkt/` — expected: clean.

- [ ] **Step 7: Commit**

```bash
git add tkt/install.py tkt/sandbox.py tests/test_install.py tests/test_sandbox.py
git commit -m "Drop the Plannotator wake-plugin wiring from install and sandbox

- plannotator-wake.js moves to LEGACY_PLUGIN_NAMES so installs delete stale copies
- Remove wake_plugin_source/dest helpers and the stale-copy launch warning
- Replace wake-specific install tests with a legacy-removal dry-run pair"
```

---

### Task 3: Delete the plugin source, its JS test, and the dangling comment

**Files:**

- Delete: `harnesses/opencode/plugin/plannotator-wake.js`
- Delete: `tests/test_wake_plugin_client.py`
- Modify: `harnesses/opencode/plugin/zed-bell/tui.js` (header comment, line ~16)

**Interfaces:**

- Consumes: Task 2 (nothing imports the plugin or its helpers anymore).
- Produces: no `plannotator-wake.js` in `harnesses/`; `harnesses/opencode/plugin/` holds only `zed-bell/`.

- [ ] **Step 1: Delete both files**

```bash
git rm harnesses/opencode/plugin/plannotator-wake.js tests/test_wake_plugin_client.py
```

- [ ] **Step 2: Fix the dangling comment in `harnesses/opencode/plugin/zed-bell/tui.js`**

Replace

```js
// and unwrap defensively so either delivery shape rings. No ACP guard: there
// is no TUI under ACP. Keep the never-throw posture of plannotator-wake.js:
```

with

```js
// and unwrap defensively so either delivery shape rings. No ACP guard: there
// is no TUI under ACP. Keep the never-throw posture:
```

- [ ] **Step 3: Run the full gate**

Run: `python -m pytest && ruff check . && ruff format --check . && mypy tkt/`
Expected: all green (no Python changed; the deleted JS test was the only
consumer of the plugin file).

- [ ] **Step 4: Commit**

```bash
git add -A harnesses/opencode/plugin tests/test_wake_plugin_client.py
git commit -m "Delete the Plannotator wake plugin and its client tests

- Server-side prompt injection cannot wake ACP-driven sessions (see spec root cause)
- Pull-based status/result is now the only feedback path
- Drop the tui.js header's reference to the removed plugin"
```

---

### Task 4: Reword the SDD skill in the superpowers submodule + pin bump

The feedback-wait instruction lives in the submodule (TallJimbo fork,
committed on its `main` per existing history). The submodule commit must exist
before the tkt2 pin bump references it.

**Files:**

- Modify: `superpowers/skills/subagent-driven-development/SKILL.md` (~line 498)
- Commit in submodule + pin bump in tkt2 (gitlink)

**Interfaces:**

- Consumes: nothing.
- Produces: a submodule commit on `superpowers` `main` containing the reworded
  sentence; tkt2 `main`-branch work records the new pin.

- [ ] **Step 1: Reword the sentence in `superpowers/skills/subagent-driven-development/SKILL.md`**

Replace

```markdown
turn after each launch and wait for the `Plannotator:` message; never poll it.
```

(with its preceding line, the full sentence is: `One session at a time: end your
turn after each launch and wait for the ``Plannotator:`` message; never poll it.`)
— replace that sentence with:

```markdown
turn after each launch; when your human partner prods you with the outcome,
pull it with the `plannotator` tool's `result` action — never busy-poll on a
timer.
```

- [ ] **Step 2: Commit in the submodule (on its current `main`)**

```bash
git -C superpowers add skills/subagent-driven-development/SKILL.md
git -C superpowers commit -m "SDD: pull Plannotator result when prodded (wake layer removed)"
```

Run: `git -C superpowers log --oneline -1` — note the new SHA for Step 4.

- [ ] **Step 3: Sanity check no other submodule file was touched**

Run: `git -C superpowers status --short` — expected: empty.

- [ ] **Step 4: Pin-bump commit in tkt2**

```bash
git add superpowers
git commit -m "Bump superpowers: pull-based Plannotator feedback in SDD skill"
```

---

### Task 5: Make the pull path the documented expected path (tkt2 instructions/docs)

Docstring/comment/wording only — no behavior change, so no new tests; the gate
is the suite plus an exhaustive grep.

**Files:**

- Modify: `tkt/AGENTS.md.in` (~lines 48-60)
- Modify: `tkt/_session_tools.py` (`plannotator_inbox` docstring, ~line 241)
- Modify: `tkt/_sessionmanagers.py` (`PlannotatorManager.status` docstring, ~lines 573, 584-586)
- Modify: `tests/test_session_managers.py` (two wake-layer comments, ~lines 621, 637)
- Modify: `AGENTS.md` (root; OpenCode integration paragraph, ~lines 151-158)
- Modify: `tkt/_cli.py` (`install-opencode-agent` Click help: "(wake + zed-bell)" → drop the wake mention; found by the Task 2 review)

**Interfaces:**

- Consumes: Task 3 (plugin gone, so no instruction may promise pushed feedback).
- Produces: no `wake`/`Plannotator:`-prefix feedback-delivery claims anywhere in `tkt/`, `tests/`, `harnesses/`, root `AGENTS.md`.

- [ ] **Step 1: `tkt/AGENTS.md.in`** — replace

```markdown
directly. Hand the human any URL the tool returns **verbatim** and end your
turn — feedback arrives later as `Plannotator:`-prefixed messages or when you
ask the `plannotator` tool for the session `result`. If you offer the Plannotator
```

with

```markdown
directly. Hand the human any URL the tool returns **verbatim** and end your
turn — feedback is not pushed to you. When the human prods you (or when you
need the outcome), call the `plannotator` tool's `status` action to list
session ids and `result` to fetch each outcome. If you offer the Plannotator
```

- [ ] **Step 2: `tkt/_session_tools.py`** — in the `plannotator` `open`
docstring (fix-round addition, review-found), replace

```python
        session ``url`` — hand it to the human **verbatim** and end your turn
        (feedback arrives later, or via ``action="result"`` after the human
        says it is in).  Never run the ``plannotator`` CLI yourself.
```

with

```python
        session ``url`` — hand it to the human **verbatim** and end your turn
        (feedback is not pushed to you — pull it with ``action="result"``
        once the human says it is in).  Never run the ``plannotator`` CLI
        yourself.
```

then, in the `plannotator_inbox` docstring, replace

```python
        a cursor — call it again.  Nothing here wakes the human's browser at
        the session; replies arrive to you via ``wait``/``read`` or as
        injected ``Plannotator:`` messages.
```

with

```python
        a cursor — call it again.  Nothing here wakes the human's browser at
        the session; replies arrive to you via ``wait``/``read``.
```

- [ ] **Step 3: `tkt/_sessionmanagers.py`** — in `PlannotatorManager.status`:

replace

```python
        """List this workspace's sessions for the wake layer.
```

with

```python
        """List this workspace's sessions (agents pull ``result`` per id).
```

and replace

```python
         legitimately have no id, the wake layer covers agent-started
         sessions.  Live entries sort first, then newest-first by
```

with

```python
         legitimately have no id: ``result``/``close`` routing covers
         registered (agent-started) sessions.  Live entries sort first,
         then newest-first by
```

- [ ] **Step 4: `tests/test_session_managers.py`** — update the two comments:

replace

```python
    list it — with its id — so the wake layer can call result().
```

with

```python
    list it — with its id — so the agent can call result().
```

and replace

```python
    unchanged: it legitimately has no id (the wake layer covers agent-started
    sessions only).
```

with

```python
    unchanged: it legitimately has no id (``result`` routing covers
    registered, agent-started sessions only).
```

- [ ] **Step 5: root `AGENTS.md`** — replace the plugin-copy sentence

```markdown
`tkt install-opencode-agent` additionally copies the autoloaded plugins
(`harnesses/opencode/plugin/plannotator-wake.js` and the
`harnesses/opencode/plugin/zed-bell/` directory plugin, whose `index.js`
server half is a no-op and whose `tui.js` half rings the terminal bell in the
TUI process, where stdout is the pty) into
`~/.config/opencode/plugin/` as real files or directories (the installed
OpenCode's plugin autoload skips symlinks); restricted sandbox launches warn
(non-fatally) when the wake copy is missing or stale.
```

with

```markdown
`tkt install-opencode-agent` additionally copies the autoloaded
`harnesses/opencode/plugin/zed-bell/` directory plugin (whose `index.js`
server half is a no-op and whose `tui.js` half rings the terminal bell in the
TUI process, where stdout is the pty) into `~/.config/opencode/plugin/` as a
real directory (the installed OpenCode's plugin autoload skips symlinks), and
removes tkt-owned plugin copies from older installs — the former wake plugin
(its server-side prompt injection cannot wake ACP-driven sessions) among them;
see `LEGACY_PLUGIN_NAMES` in `tkt/install.py`.
```

- [ ] **Step 6: `tkt/_cli.py` help text** — in the `install-opencode-agent`
command's help/docstring, replace `autoloaded plugins (wake + zed-bell) into`
with `autoloaded plugins (zed-bell) into` (exact current wording may differ
slightly; drop only the wake mention).

- [ ] **Step 7: Verify the removal is complete**

Run: `grep -rn -i "wake" tkt/ tests/ harnesses/ AGENTS.md --include="*.py" --include="*.md" --include="*.in" --include="*.js"`
Expected: no hits referencing the wake layer. (Benign expected hit:
`"Nothing here wakes the human's browser"` in `tkt/_session_tools.py` —
ordinary English about the browser, not the removed plugin.)
Run: `grep -rn "Plannotator:" tkt/ tests/ harnesses/` — expected: zero hits
(the plugin was the only `Plannotator:`-prefix deliverer).
Run: `python -m pytest && ruff check . && ruff format --check . && mypy tkt/`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add AGENTS.md tkt/AGENTS.md.in tkt/_session_tools.py tkt/_sessionmanagers.py tkt/_cli.py tests/test_session_managers.py
git commit -m "Docs: pull-based Plannotator feedback is the expected path

- AGENTS.md.in and session-tool docstrings drop the pushed-message promise
- status() docstring reframed for agent result routing
- Root AGENTS.md records the wake plugin's removal in LEGACY_PLUGIN_NAMES"
```

---

## Final verification (after Task 5)

- [ ] `python -m pytest` — all green.
- [ ] `ruff check . && ruff format --check . && mypy tkt/` — clean.
- [ ] `grep -rn "plannotator-wake" tkt/ tests/ harnesses/` — only allowed
      mentions: `LEGACY_PLUGIN_NAMES` in `tkt/install.py` (plus its comment),
      the explanatory sentence in root `AGENTS.md`, and the
      legacy-removal/dry-run test names/strings in `tests/test_install.py`
      (they seed the host copy being deleted — intentional).
- [ ] `git -C superpowers log --oneline -1` shows the SDD reword commit, and
      `git log --oneline` in tkt2 shows the pin bump referencing it.
- [ ] Per repo Git Workflow: offer to squash the branch into a single commit
      and rebase onto main (the submodule commit stays separate).


