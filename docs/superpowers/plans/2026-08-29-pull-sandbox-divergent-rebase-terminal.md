# pull-sandbox divergent rebase: attach the terminal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `tkt pull-sandbox` failing on diverged packages with "the divergent rebase could not be started" by running the interactive rebase with the terminal attached instead of through GitPython's output-capturing call.

**Architecture:** `Pull._commit_transfer` currently runs `st.human_repo.git.rebase("-i", snapshot)` via GitPython, which captures git's stdout as a pipe and breaks TTY-requiring sequence editors (`emacsclient -t`), so git aborts the rebase before creating any state. Replace that single call with `subprocess.run(["git", "-C", <dir>, "rebase", "-i", snapshot])` using inherited stdin/stdout/stderr, keeping the existing detection control flow (paused-on-conflict vs failed-to-start vs completed) unchanged.

**Tech Stack:** Python 3.13, GitPython (rest of the file), `subprocess` (new), `pytest` + `pty` (regression test).

**Spec:** `docs/superpowers/specs/2026-08-29-pull-sandbox-divergent-rebase-terminal-design.md`

## Global Constraints

- All `.py` files include the existing BSD-3-Clause license header — preserve it; do not alter existing headers.
- Python 3.13; dependencies are `click`, `GitPython`, `pyyaml`, `json5`. No new third-party deps.
- Ruff: line-length 110, doc-length 79, numpy docstring convention. Run `ruff check .` and `ruff format --check .` before committing.
- mypy: run `mypy tkt/` before committing.
- Tests: `python -m pytest`.
- Do not add packaging configuration; do not scaffold a project.
- Prefer simple solutions; this tool's users can handle tracebacks.

---

### Task 1: Run the divergent rebase with the terminal attached

**Files:**
- Modify: `tkt/pull.py` (add `import subprocess`; rewrite the rebase invocation in `Pull._commit_transfer`, currently around lines 407-431)
- Modify: `tests/test_pull.py` (add `import pty`; add one regression test)
- Test: `tests/test_pull.py`

**Interfaces:**
- Consumes: existing `Pull._commit_transfer(workspace, pkg, st, *, ledger)`, `_Status.human_dir`, `_Status.human_repo`, `_is_rebase_in_progress(repo)`, `_restore_commit_transfer`, `_finalize_commit_transfer`, `PullError`. Existing `tests/test_pull.py` helpers `_human`, `_agent`, `_make_agent_commit`, and the `workspace` fixture.
- Produces: a working interactive rebase in the divergent path. No new public interface.

- [ ] **Step 1: Add `import pty` to the test file**

In `tests/test_pull.py`, extend the stdlib imports block (currently `import json`, `import os`, `from pathlib import Path`) to include `pty`:

```python
import json
import os
import pty
from pathlib import Path
```

- [ ] **Step 2: Write the failing regression test**

Append to `tests/test_pull.py`:

```python
def test_diverged_rebase_with_tty_editor_succeeds(workspace, tmp_path, monkeypatch):
    """A TTY-requiring sequence editor works because the rebase attaches the terminal.

    Regression test for the divergent path failing with "could not be started"
    when git was invoked through GitPython, which captures git's stdout as a
    pipe and so cannot launch a TTY-requiring editor (e.g. ``emacsclient -t``).
    """
    human = _human(workspace)
    (Path(f"{workspace.directory}/pkg/extra.txt")).write_text("extra\n")
    human.git.add("extra.txt")
    human.git.commit("-m", "human extra")
    _make_agent_commit(workspace, "file1\nhuman change\nagent commit\n")
    human = _human(workspace)
    pre = human.head.commit

    # A sequence editor that refuses to run unless stdout is a terminal. This
    # mirrors `emacsclient -t`, which fails when git's stdout is a pipe.
    editor = tmp_path / "tty-editor.sh"
    editor.write_text("#!/bin/sh\n[ -t 1 ] || { echo 'editor: stdout is not a tty' >&2; exit 1; }\nexit 0\n")
    os.chmod(editor, 0o755)
    monkeypatch.setenv("GIT_SEQUENCE_EDITOR", str(editor))
    monkeypatch.setenv("GIT_EDITOR", str(editor))

    # Run Pull.run in a child whose stdout/stderr are a pty slave, so the
    # inherited-fd git rebase (and its editor) sees a real terminal.
    pid, master = pty.fork()
    if pid == 0:
        try:
            Pull.run(workspace)
        except BaseException:
            os._exit(1)
        os._exit(0)
    # Drain the pty (blocks until the child closes the slave on exit), then reap.
    while True:
        try:
            data = os.read(master, 1024)
        except OSError:
            break
        if not data:
            break
    _, status = os.waitpid(pid, 0)
    os.close(master)

    assert status == 0, "pull-sandbox under a TTY should have completed the rebase"
    human = _human(workspace)
    assert human.head.commit != pre
    assert "extra.txt" in human.git.ls_files()
    assert "tickets/X-sync" not in human.heads
    assert not os.path.exists(f"{workspace.directory}/.pull-sandbox.json")
```

- [ ] **Step 3: Run the new test and verify it FAILS**

Run: `python -m pytest tests/test_pull.py::test_diverged_rebase_with_tty_editor_succeeds -v`

Expected: FAIL (the child exits nonzero and `assert status == 0` fails), because under the current GitPython invocation the editor sees a pipe, the rebase fails to start, and `PullError` is raised inside the child.

- [ ] **Step 4: Add `import subprocess` to `tkt/pull.py`**

In `tkt/pull.py`, change the imports block (currently `import logging` / `import os` / `from collections.abc import Callable` / `from dataclasses import ...`) to insert `import subprocess` between `import os` and the `from collections.abc` import:

```python
import logging
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
```

- [ ] **Step 5: Replace the GitPython rebase call in `_commit_transfer`**

In `tkt/pull.py`, replace this block inside `Pull._commit_transfer`:

```python
        st.human_repo.git.reset("--hard", st.A)
        try:
            st.human_repo.git.rebase("-i", snapshot)
            rebase_failed = False
        except git.exc.GitCommandError:
            # git exits nonzero both on a conflict (leaving a paused rebase,
            # which we detect below) and on a rebase that fails to start (e.g.
            # the sequence editor errors out, leaving no state at all). We must
            # distinguish the two: the latter must not be treated as success,
            # because the human's commits live only on the snapshot branch.
            rebase_failed = True
        if _is_rebase_in_progress(st.human_repo):
            logger.info(
                f"{pkg}: rebase is in progress. Resolve any conflicts and run "
                "`git rebase --continue`, then `tkt pull-sandbox --finish`."
            )
        elif rebase_failed:
            # The rebase failed to start rather than pausing on a conflict;
            # restore the human branch so its commits are not dropped, then
            # report the failure.
            cls._restore_commit_transfer(st, ledger)
            raise PullError(
                f"Package {pkg!r}: the divergent rebase could not be started. "
                "Your branch was restored to its previous state; nothing was transferred."
            )
        else:
            cls._finalize_commit_transfer(st, ledger)
```

with:

```python
        st.human_repo.git.reset("--hard", st.A)
        # Run git directly with inherited stdin/stdout/stderr so the interactive
        # sequence editor attaches to the caller's terminal. GitPython captures
        # git's stdout as a pipe, which breaks TTY-requiring editors (e.g.
        # `emacsclient -t`), causing the rebase to "fail to start" with no state.
        # A nonzero exit is still ambiguous (paused-on-conflict vs failed-to-
        # start); we disambiguate via rebase state below, as before.
        result = subprocess.run(["git", "-C", st.human_dir, "rebase", "-i", snapshot])
        if _is_rebase_in_progress(st.human_repo):
            logger.info(
                f"{pkg}: rebase is in progress. Resolve any conflicts and run "
                "`git rebase --continue`, then `tkt pull-sandbox --finish`."
            )
        elif result.returncode != 0:
            # The rebase failed to start rather than pausing on a conflict;
            # restore the human branch so its commits are not dropped, then
            # report the failure.
            cls._restore_commit_transfer(st, ledger)
            raise PullError(
                f"Package {pkg!r}: the divergent rebase could not be started. "
                "Your branch was restored to its previous state; nothing was transferred."
            )
        else:
            cls._finalize_commit_transfer(st, ledger)
```

- [ ] **Step 6: Run the new test and verify it PASSES**

Run: `python -m pytest tests/test_pull.py::test_diverged_rebase_with_tty_editor_succeeds -v`

Expected: PASS (the child exits 0; the rebase completed under the pty).

- [ ] **Step 7: Run the full pull test suite**

Run: `python -m pytest tests/test_pull.py -v`

Expected: all tests PASS, including `test_diverged_interactive_rebase`, `test_diverged_rebase_failed_to_start_restores_branch`, and `test_diverged_conflict_is_left_in_progress_and_recorded`.

- [ ] **Step 8: Run lint and type checks**

Run: `ruff check . && ruff format --check . && mypy tkt/`

Expected: all three pass with no output beyond possible formatting diffs (apply `ruff format` if it reports diffs on the changed files).

- [ ] **Step 9: Commit**

```bash
git add tkt/pull.py tests/test_pull.py
git commit -m "fix: attach terminal to divergent rebase in pull-sandbox"
```
