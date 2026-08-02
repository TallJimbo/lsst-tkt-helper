# sandbox-reset

## Purpose

Restoring every `.agent/<pkg>` worktree to the state of the corresponding
human-workspace branch, discarding the agent's work after first saving uncommitted
changes to the git stash and unmerged agent commits to a timestamped backup
branch.

## Requirements

### Requirement: Reset each agent worktree to its human branch

`tkt sandbox-reset` SHALL, for every package that has an `.agent/<pkg>`
worktree, reset that worktree's active branch to the state of the corresponding
human-workspace branch (`git reset --hard <human-branch>`) and clean the worktree
of remaining untracked and ignored files (`git clean -fdx`). Packages without an
`.agent/<pkg>` worktree SHALL be skipped.

#### Scenario: Reset a single agent worktree

- **WHEN** `tkt sandbox-reset` runs and a package has an `.agent/<pkg>`
  worktree on branch `tickets/X-agent`
- **THEN** `tickets/X-agent` is reset to the commit of the human branch
  `tickets/X` and the worktree is cleaned of untracked and ignored files

#### Scenario: Package without an agent worktree is skipped

- **WHEN** `tkt sandbox-reset` runs and a package has no `.agent/<pkg>`
  worktree
- **THEN** that package is skipped without error

### Requirement: Save uncommitted work to the stash

Before resetting, if an agent worktree has uncommitted work (staged, unstaged,
untracked, or ignored files), `tkt sandbox-reset` SHALL save it with
`git stash push --all` and a descriptive message naming the package, so nothing is
lost.

#### Scenario: Stash uncommitted changes

- **WHEN** an agent worktree has uncommitted changes including untracked and
  ignored files
- **THEN** the changes are pushed to the stash with `--all` and a message naming
  the package, then the worktree is reset and cleaned

#### Scenario: Clean worktree is not stashed

- **WHEN** an agent worktree has no uncommitted changes
- **THEN** no stash entry is created

### Requirement: Save unmerged agent commits to a timestamped branch

Before resetting, if the agent worktree's active branch has commits not reachable
from the human-workspace branch (i.e. `git rev-list <human>..<agent>` is
non-empty), `tkt sandbox-reset` SHALL create a backup branch named
`<agent-branch>-saved-<%Y%m%dT%H%M%S>` pointing at the current agent HEAD,
using the time the reset runs and a format of `%Y%m%dT%H%M%S` (second
precision).

#### Scenario: Save unmerged commits

- **WHEN** an agent branch has commits not reachable from the human branch
- **THEN** a backup branch `<human>-agent-saved-<timestamp>` is created at the
  agent HEAD before it is reset

#### Scenario: No backup when nothing to save

- **WHEN** the agent branch is at or behind the human branch (no unique commits)
- **THEN** no backup branch is created

#### Scenario: Unique names across resets

- **WHEN** `tkt sandbox-reset` is run more than once
- **THEN** each run's backup branch has a distinct timestamp and does not
  overwrite a previous run's backup branch
