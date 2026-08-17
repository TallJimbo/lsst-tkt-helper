## MODIFIED Requirements

### Requirement: Transfer uncommitted agent work as unstaged changes

When the agent worktree has uncommitted work, `tkt pull-sandbox` SHALL transfer
that work onto the human branch and expose it as ordinary unstaged changes,
including untracked files. The transfer SHALL be ancestry-independent: captured
as a temporary WIP commit and applied onto the human branch via a cherry-pick /
3-way apply, so it lands cleanly whether or not the human branch is an ancestor
of the agent's base (e.g. after a earlier interactive-rebase transfer).

The temporary WIP commit created to capture the agent's uncommitted work SHALL be
made with pre-commit hooks bypassed (`git commit --no-verify`), so the package's
pre-commit/prek hooks cannot fail the transfer. Once the work has landed on the
human branch as unstaged changes, the temporary WIP commit SHALL be removed from
the agent branch by resetting the agent branch back to its pre-WIP tip with
`git reset --mixed`, restoring the agent worktree to its original
uncommitted/untracked state (a copy of the work now exists on the human branch).

#### Scenario: Uncommitted agent work lands unstaged in the human worktree

- **WHEN** the agent worktree is dirty and the agent has no commits ahead of
  the human branch
- **THEN** the human branch gains the agent work as unstaged working-tree
  changes (index reset to the pre-transfer commit via `git reset --mixed`),
  including untracked files, and the agent branch's temporary WIP commit is
  removed (the agent worktree returns to its uncommitted state)

#### Scenario: Uncommitted transfer after a divergent sync-1

- **WHEN** `--only-uncommitted` runs after a sync-1 interactive rebase so the
  human branch contains the agent's committed content but is not an ancestor of
  the agent base
- **THEN** the uncommitted work is applied onto the reconciled human branch via
  the ancestry-independent transfer without an unrelated whole-branch merge,
  exposed as unstaged changes

#### Scenario: Pre-commit hook does not block the uncommitted transfer

- **WHEN** the agent package has a pre-commit/prek hook that would fail and the
  agent worktree has uncommitted work being transferred
- **THEN** the temporary WIP commit is created with hooks bypassed so the
  transfer proceeds and lands the work as unstaged changes on the human branch

### Requirement: Finish an in-progress sync

`tkt pull-sandbox --finish` SHALL finalize a previously started but incomplete
sync: delete the `-sync` snapshot branches, restore any stashes created for the
sync (leaving a stash in place and warning if the pop conflicts), and, for the
uncommitted path, perform the final `git reset --mixed` to expose the work as
unstaged.

For an uncommitted transfer that has actually completed (the human-side
cherry-pick was continued by the user), `--finish` SHALL also remove the
temporary WIP commit from the agent branch with `git reset --mixed`, restoring
the agent worktree to its uncommitted state. When `--finish` is run while the
human-side cherry-pick is still in progress (and `--finish` therefore abandons
it), the temporary WIP commit SHALL be retained on the agent branch as the only
surviving copy of the work.

#### Scenario: Snapshot and stash cleaned on finish

- **WHEN** `tkt pull-sandbox --finish` runs and a sync created `-sync` snapshot
  branches and human stashes
- **THEN** the snapshot branches are deleted and the stashes are popped back
  onto the resulting human branch

#### Scenario: Stash pop conflict leaves the stash intact

- **WHEN** popping a stash on `--finish` conflicts with the resulting branch
- **THEN** the stash entry is left in place and a warning is emitted rather
  than any work being destroyed

#### Scenario: Completed uncommitted transfer drops the WIP commit on finish

- **WHEN** `--finish` finalizes an uncommitted transfer whose human-side
  cherry-pick was resolved and continued by the user
- **THEN** the work is exposed as unstaged changes on the human branch and the
  agent branch's temporary WIP commit is removed (the agent worktree returns to
  its uncommitted state)

#### Scenario: Abandoned uncommitted transfer keeps the WIP commit on finish

- **WHEN** `--finish` runs while the human-side cherry-pick is still in progress
  and therefore abandons that cherry-pick
- **THEN** the temporary WIP commit is kept on the agent branch so the agent's
  work is not lost
