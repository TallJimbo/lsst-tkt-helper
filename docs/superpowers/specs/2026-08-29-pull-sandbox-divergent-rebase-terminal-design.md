# pull-sandbox divergent rebase: attach the terminal (design handover)

Date: 2026-08-29

## Problem

`tkt pull-sandbox` on a diverged package raised:

```
tkt.pull.PullError: Package 'images': the divergent rebase could not be started.
Your branch was restored to its previous state; nothing was transferred.
```

This came from the divergent committed path in `Pull._commit_transfer`
(`tkt/pull.py`), which snapshot the human branch, reset it to the agent tip,
then ran an **interactive** `git rebase -i <snapshot>` so the user can review
and drop "variant" agent commits in the todo editor. The recent change
`d9b053e` correctly made a failed-to-start rebase loud (restore + `PullError`)
instead of silently dropping the human's commits, which is why the failure
became visible rather than destructive.

The user then wanted the root cause fixed, not just reported.

## Root cause (confirmed by reproduction)

`tkt` ran the rebase through **GitPython**:

```python
st.human_repo.git.rebase("-i", snapshot)
```

GitPython spawns git with **stdout/stderr captured as pipes**. Git forwards its
own (piped) stdout down to the sequence editor — git does **not** reconnect the
editor to `/dev/tty`. With `core.editor = emacsclient -t` (or any TTY-requiring
editor), the editor cannot get a terminal name, fails to launch, and git aborts
**before creating any rebase state**. `_is_rebase_in_progress()` is then false,
so `_restore_commit_transfer` fires and the `PullError` is raised.

Manual `git rebase -i` works from the same terminal because git's stdout is the
real terminal, so the editor inherits a TTY.

Reproduction confirmed all of this:
- Direct (pty) rebase with a TTY-requiring editor: succeeds.
- Same rebase through GitPython (`repo.git.rebase("-i", ...)`): fails with
  `error: there was a problem with the editor '...'`, no rebase state.
- Same GitPython call with `GIT_SEQUENCE_EDITOR=true` (non-TTY editor):
  succeeds — proving the editor/TTY is the only blocker.

## Design decision (D1): preserve interactivity, attach the terminal

Two fixes were considered:

- **A. Preserve interactivity (chosen).** Run the rebase so git inherits the
  real terminal fds. The user keeps the interactive "drop variant commits"
  todo editor; a conflict still pauses and flows through the existing
  `--finish`/`--abort` path. Inherently needs an interactive terminal.
- **B. Non-interactive replay.** Auto-accepting editor / `--onto` / `cherry-pick`
  of the agent range. Works headless but silently drops the in-rebase todo
  review the user relies on.

The user chose **A**.

## Design decision (D2): invoke git with inherited fds, not GitPython capture

Replace the GitPython rebase call with `subprocess.run` using inherited
stdin/stdout/stderr (`None`), so git and its editor attach to the caller's
terminal. The existing control flow (detect in-progress rebase vs failed-to-start
vs completed) is unchanged.

```python
import subprocess

# ... st.human_repo.git.reset("--hard", st.A) ...
result = subprocess.run(
    ["git", "-C", st.human_dir, "rebase", "-i", snapshot],
)
if _is_rebase_in_progress(st.human_repo):
    logger.info(
        f"{pkg}: rebase is in progress. Resolve any conflicts and run "
        "`git rebase --continue`, then `tkt pull-sandbox --finish`."
    )
elif result.returncode != 0:
    cls._restore_commit_transfer(st, ledger)
    raise PullError(
        f"Package {pkg!r}: the divergent rebase could not be started. "
        "Your branch was restored to its previous state; nothing was transferred."
    )
else:
    cls._finalize_commit_transfer(st, ledger)
```

Notes:
- `subprocess.run` with no `stdin`/`stdout`/`stderr` arguments inherits the
  parent's fds, giving the editor the terminal.
- `git -C <dir>` runs rebase in the package directory (the codebase otherwise
  uses GitPython, but GitPython has no clean per-call way to attach a TTY; a
  raw `subprocess` call for this single interactive command is justified).
- The failure-detection semantics are unchanged: nonzero return code **with**
  rebase state = paused on conflict; nonzero **without** state = failed to
  start (restore + `PullError`); zero = completed (finalize).

## Design decision (D3): detection logic unchanged

No change to `_is_rebase_in_progress`, `_restore_commit_transfer`,
`_finalize_commit_transfer`, or the ledger state machine. Only the way git is
invoked changes.

## Testing

A genuine regression test runs `Pull.run` (with the divergent history and a
TTY-requiring sequence editor) under a pseudo-terminal via `pty.fork()`, and
asserts the rebase completes successfully. Under the old GitPython code this
fails (editor can't get a TTY -> `PullError`); under the fix it succeeds.
Existing divergent-path tests (`test_diverged_interactive_rebase`,
`test_diverged_rebase_failed_to_start_restores_branch`,
`test_diverged_conflict_is_left_in_progress_and_recorded`) must continue to
pass.

## Scope

Single-file change to `tkt/pull.py` (`_commit_transfer`) plus a regression test
in `tests/test_pull.py`. No interface changes outside `_commit_transfer`.
