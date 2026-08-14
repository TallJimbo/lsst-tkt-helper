# pull-sandbox

## Purpose

Transferring LLM-agent work from `.agent/<pkg>` worktrees onto the corresponding
human-workspace branches: committed agent work is fast-forwarded or
snapshotted + interactively rebased, and uncommitted agent work is transferred
as ordinary unstaged changes, with a resumable `--finish`/`--abort` lifecycle
and fast-fail guards against ambiguous states.

## Requirements

### Requirement: Transfer committed agent work to the human branch

For every package with an `.agent/<pkg>` worktree, `tkt pull-sandbox` SHALL
bring the agent work that is committed on the agent branch onto the human
branch, classifying the transfer by the branch relationship between the human
branch `H` and the agent branch `A`:
- if `H..A` is empty (agent is entirely contained in human), SHALL skip the
  package as having nothing to transfer;
- if `H` is an ancestor of `A` (the agent branch contains all of the human
  branch), SHALL fast-forward the human branch to the agent branch;
- otherwise (divergent branches, where the human has commits the agent does
  not), SHALL replay the agent's commits onto the human branch via an
  interactive rebase.

#### Scenario: Agent fully contained in human branch is skipped

- **WHEN** `tkt pull-sandbox` runs and the agent branch is at or behind the
  human branch (no commits in `H..A`)
- **THEN** that package is skipped with a notification and no changes are made

#### Scenario: Fast-forward when human is an ancestor of the agent branch

- **WHEN** `tkt pull-sandbox` runs and the human branch `H` is an ancestor of
  the agent branch `A` with a clean agent worktree
- **THEN** the human branch is fast-forwarded to the agent branch with
  `git merge --ff-only A` and no conflict is possible

#### Scenario: Interactive rebase when branch diverged

- **WHEN** `tkt pull-sandbox` runs and the human branch and agent branch have
  diverged (each has commits the other lacks)
- **THEN** the agent's commits are replayed onto the human branch via an
  interactive rebase that lets the user drop unwanted agent commits in
  `$EDITOR`, with the result landing directly on the human branch

### Requirement: Skip variant agent commits during interactive rebase

When the divergent interactive-rebase path is used, `tkt pull-sandbox` SHALL
present the agent-side commits (those in `H..A`) to the user in `$EDITOR` for an
interactive rebase, so the user can drop agent-side commits that are variants
of, or duplicates of, commits they have intentionally made on the human branch.

#### Scenario: User drops a variant agent commit

- **WHEN** the interactive rebase is presented and the user drops an agent-side
  commit that duplicates their own work
- **THEN** that commit is omitted from the resulting human branch and the
  remaining agent commits are preserved

#### Scenario: Drop all agent commits

- **WHEN** the user drops every agent-side commit during the interactive rebase
- **THEN** the resulting human branch equals the original human branch and the
  snapshot is cleaned up without error

### Requirement: Snapshot human state with a rollback branch

When the interactive-rebase (divergent) path is used, `tkt pull-sandbox` SHALL
save the pre-transfer human branch state to a snapshot branch named
`<human-branch>-sync` before rebasing, so the transfer can be rolled back.

#### Scenario: Snapshot branch created before rebase

- **WHEN** the divergent path runs
- **THEN** a snapshot branch `<human-branch>-sync` pointing at the original
  human branch tip is created and recorded before the rebase begins

### Requirement: Transfer uncommitted agent work as unstaged changes

When the agent worktree has uncommitted work, `tkt pull-sandbox` SHALL transfer
that work onto the human branch and expose it as ordinary unstaged changes,
including untracked files. The transfer SHALL be ancestry-independent: captured
as a temporary WIP commit and applied onto the human branch via a cherry-pick /
3-way apply, so it lands cleanly whether or not the human branch is an ancestor
of the agent's base (e.g. after a earlier interactive-rebase transfer).

#### Scenario: Uncommitted agent work lands unstaged in the human worktree

- **WHEN** the agent worktree is dirty and the agent has no commits ahead of
  the human branch
- **THEN** the human branch gains the agent work as unstaged working-tree
  changes (index reset to the pre-transfer commit via `git reset --mixed`),
  including untracked files

#### Scenario: Uncommitted transfer after a divergent sync-1

- **WHEN** `--only-uncommitted` runs after a sync-1 interactive rebase so the
  human branch contains the agent's committed content but is not an ancestor of
  the agent base
- **THEN** the uncommitted work is applied onto the reconciled human branch via
  the ancestry-independent transfer without an unrelated whole-branch merge,
  exposed as unstaged changes

### Requirement: Leave conflicts in progress for manual resolution

On a transfer conflict, `tkt pull-sandbox` SHALL leave the merge/rebase in
progress in the affected worktree for the user to resolve with their normal git
tools, rather than resolving it automatically, and SHALL record enough state
that a subsequent `--finish` or `--abort` can finalize or cancel it.

#### Scenario: Conflicted rebase is left for the user

- **WHEN** `tkt pull-sandbox` encounters a conflict during a transfer and the
  user opts to resolve it
- **THEN** the operation is left in progress and the user resolves it manually
  with their usual tools

### Requirement: Finish an in-progress sync

`tkt pull-sandbox --finish` SHALL finalize a previously started but incomplete
sync: delete the `-sync` snapshot branches, restore any stashes created for the
sync (leaving a stash in place and warning if the pop conflicts), and, for the
uncommitted path, perform the final `git reset --mixed` to expose the work as
unstaged.

#### Scenario: Snapshot and stash cleaned on finish

- **WHEN** `tkt pull-sandbox --finish` runs and a sync created `-sync` snapshot
  branches and human stashes
- **THEN** the snapshot branches are deleted and the stashes are popped back
  onto the resulting human branch

#### Scenario: Stash pop conflict leaves the stash intact

- **WHEN** popping a stash on `--finish` conflicts with the resulting branch
- **THEN** the stash entry is left in place and a warning is emitted rather
  than any work being destroyed

### Requirement: Abort a sync globally

`tkt pull-sandbox --abort` SHALL cancel the sync across every package:
`git rebase --abort` for any in-progress rebase, reset each human branch back
to its `-sync` snapshot, delete the snapshot branches, restore the stashes, and
clear the ledger.

#### Scenario: Abort restores the human branch

- **WHEN** `tkt pull-sandbox --abort` runs while a divergent rebase is in
  progress
- **THEN** the in-progress rebase is aborted first and each human branch is
  reset back to its original `<human-branch>-sync` snapshot, the snapshot
  branches are deleted, and stashes are restored

### Requirement: Abort the run when both sides are dirty

If any package in the workspace has both a dirty human worktree and a dirty
agent worktree, `tkt pull-sandbox` SHALL abort the whole run immediately before
making any changes.

#### Scenario: Both human and agent dirty aborts the run

- **WHEN** `tkt pull-sandbox` runs and any package has both uncommitted human
  changes and uncommitted agent changes
- **THEN** the entire run aborts immediately without modifying any worktree

### Requirement: Split mixed state with side-selection flags

If a package has both agent commits ahead of the human branch AND a dirty agent
worktree (a mixed state), `tkt pull-sandbox` SHALL NOT transfer both kinds in a
single run. `-s/--skip-uncommitted` SHALL transfer the committed side only and
mark the dirty agent worktree deferred; `-o/--only-uncommitted` SHALL transfer
the uncommitted side only. With no side-selection flag on a mixed package, the
command SHALL error and ask the user to pick one of the two.

#### Scenario: Skip-uncommitted transfers committed and defers the worktree

- **WHEN** `tkt pull-sandbox --skip-uncommitted` runs on a package with agent
  commits ahead of human and a dirty agent worktree
- **THEN** the committed side is transferred, the dirty agent worktree is left
  untouched and marked deferred (recorded in the ledger, suppressing any
  `sandbox-reset` offer), and a follow-up `--only-uncommitted` can transfer it

#### Scenario: Only-uncommitted transfers just the uncommitted work

- **WHEN** `tkt pull-sandbox --only-uncommitted` runs on a package and the
  committed side is already reconciled
- **THEN** only the uncommitted work is transferred onto the human branch and
  exposed as unstaged changes; no committed work is attempted

#### Scenario: No side flag on a mixed package errors

- **WHEN** `tkt pull-sandbox` runs with no side-selection flag on a package
  that has both agent commits ahead and a dirty agent worktree
- **THEN** the command errors asking the user to pass `--skip-uncommitted` or
  `--only-uncommitted` and makes no changes

### Requirement: Overridable ordering guard for only-uncommitted

When `-o/--only-uncommitted` runs on a package whose agent branch still has
commits ahead of the human branch, `tkt pull-sandbox` SHALL warn that this is
unusual and prompt for confirmation before proceeding, and SHALL proceed when
the user confirms (or passes an override). This accommodates a sync-1
resolution that dropped or squashed agent commits, where the agent branch still
appears ahead even though the work is already on the human branch.

#### Scenario: Confirmed override proceeds despite commits ahead

- **WHEN** `--only-uncommitted` runs with the agent branch ahead of human and
  the user confirms the "are you sure" prompt
- **THEN** the uncommitted work is transferred, even though the agent branch
  still has commits the human branch lacks

### Requirement: Refuse to start over leftover snapshot state

`tkt pull-sandbox` SHALL refuse to start a sync if a `-sync` snapshot branch
already exists for a repository that would need the divergent path, directing
the user to run `--abort` or clean up manually.

#### Scenario: Leftover snapshot branch prevents starting

- **WHEN** `tkt pull-sandbox` runs and a `tickets/*-sync` branch already exists
  for a repo that would require the divergent path
- **THEN** the run refuses to start with an error referencing `--abort`

### Requirement: Report empty lifecycle runs

`tkt pull-sandbox --finish` or `--abort` SHALL report a clear "nothing to do"
message and exit non-zero when there is no recorded sync state.

#### Scenario: Finish with no sync state errors

- **WHEN** `tkt pull-sandbox --finish` runs with an empty ledger
- **THEN** it prints a clear "nothing to do" message and exits non-zero

### Requirement: Offer sandbox-reset after finish

After `tkt pull-sandbox --finish` completes, `tkt pull-sandbox` SHALL prompt to
run `tkt sandbox-reset` on the affected agent worktrees when doing so would not
be a no-op (i.e. when the agent branch still differs from the resulting human
branch or the agent worktree is dirty).

#### Scenario: Sandbox-reset offered when agent branch differs

- **WHEN** `--finish` completes and an agent branch still differs from the
  resulting human branch
- **THEN** the user is prompted to run `tkt sandbox-reset` on that agent
  worktree

#### Scenario: Sandbox-reset not offered when it would be a no-op

- **WHEN** `--finish` completes and an agent branch already equals the
  resulting human branch with a clean worktree
- **THEN** no `sandbox-reset` prompt is offered for that package

#### Scenario: Sandbox-reset suppressed while uncommitted work is deferred

- **WHEN** `--finish` (or `--skip-uncommitted`) leaves a package with
  deferred/pending uncommitted work still in the agent worktree
- **THEN** no `sandbox-reset` prompt is offered for that package, so the
  deferred work survives for a follow-up `--only-uncommitted` sync
