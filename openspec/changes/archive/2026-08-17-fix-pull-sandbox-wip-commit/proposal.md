## Why

The uncommitted-transfer path of `tkt pull-sandbox` makes a temporary WIP commit
in the agent worktree that can fail because it runs the package's pre-commit
hooks; and, when the transfer succeeds, that temporary commit is left sitting on
the agent branch afterward, polluting it.

## What Changes

- **Skip pre-commit hooks on the temporary WIP commit.** The temporary commit
  that captures the agent's uncommitted work (in the `--only-uncommitted` /
  uncommitted path) is made with `git commit --no-verify`, so package pre-commit /
  prek hooks cannot fail it. This is the only hook-triggering step in the
  uncommitted-transfer flow: plain `git cherry-pick` and `git rebase` do not run
  pre-commit hooks, and the transferred result on the human side is left as
  uncommitted (unstaged) work, so no human commit is created.
- **Drop the temporary WIP commit from the agent branch after transfer.** Once
  the agent's uncommitted work has landed on the human branch (as unstaged
  changes), the agent branch is reset back to its pre-WIP tip with
  `git reset --mixed`, removing the temporary commit and restoring the agent
  worktree to its original uncommitted/untracked state (the human has a copy, so
  nothing is lost). Applies to both the immediate-success path and the `--finish`
  path.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `pull-sandbox`: existing requirements are extended so that (a) the temporary
  WIP commit used to transfer uncommitted work bypasses pre-commit hooks, and
  (b) the temporary WIP commit is removed from the agent branch after the
  uncommitted work is transferred, with the agent worktree restored to its
  uncommitted state (except when a transfer is abandoned mid-flight and the WIP
  is the only surviving copy of the work).

## Impact

- `tkt/pull.py`:
  - `Pull._uncommitted_transfer` — pass `no_verify=True` to the temporary agent
    WIP commit; after a successful (non-conflicted) transfer, `--mixed` reset the
    agent branch back to its pre-WIP tip.
  - `Pull._finalize_package` — when finalizing an uncommitted transfer on
    `--finish`, `--mixed` reset the agent branch back to its pre-WIP tip, but only
    when the transfer actually completed (i.e. the human-side cherry-pick was
    already continued); keep the WIP as a safety net when `--finish` has to
    abandon an in-progress cherry-pick.
- `tests/test_pull.py`: extend the uncommitted-transfer and finish tests to
  assert the agent branch no longer retains the WIP commit; add a test that a
  failing pre-commit hook does not block the uncommitted transfer.
