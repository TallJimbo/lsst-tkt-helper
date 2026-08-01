# sandbox-run-single-repo

## Purpose

Running the `tkt` sandbox on an individual git repository that is not a
multi-package EUPS ticket workspace, with the agent writing directly to the main
worktree. (TBD: expand.)

## Requirements

### Requirement: Autodetect mode from working directory

`tkt sandbox-run` SHALL inspect the current working directory to choose its mode
of operation. If a `.agent` subdirectory is present, it SHALL operate in
workspace mode (the existing behavior, unchanged). Otherwise it SHALL operate in
single-repo mode and treat the working directory as the root of a single git
repository.

#### Scenario: Workspace mode when `.agent` present

- **WHEN** the current working directory contains a `.agent` subdirectory
- **THEN** `tkt sandbox-run` uses the existing workspace-mode behavior

#### Scenario: Single-repo mode when `.agent` absent

- **WHEN** the current working directory does not contain a `.agent` subdirectory
- **THEN** `tkt sandbox-run` treats the working directory as the root of a single
  git repository and runs in single-repo mode

### Requirement: Agent writes to main worktree in single-repo mode

In single-repo mode, the agent SHALL be able to write to the main (repo-default)
git worktree. The whole repository SHALL be mounted read-write in the sandbox,
including the `.git` directory, with no separate `.agent` worktrees and no
`-agent` branch.

#### Scenario: Repo mounted read-write

- **WHEN** single-repo mode runs the sandbox
- **THEN** the repository root is bind-mounted read-write, so edits by the agent
  land in the human's main worktree

#### Scenario: No agent worktree created

- **WHEN** single-repo mode runs the sandbox
- **THEN** no `.agent` directory or `-agent` branches are created

### Requirement: Use currently active conda environment

By default, single-repo mode SHALL use the currently active conda environment,
which is inherited by the sandbox from the host (the sandbox does not sanitize
PATH, so the active environment is already in effect inside the sandbox).

#### Scenario: Inherited environment

- **WHEN** `--conda-env` is not provided
- **THEN** the sandbox runs with the currently active conda environment, with no
  explicit activation step

### Requirement: Override conda environment

A `--conda-env <name>` option SHALL activate the named conda environment inside
the sandbox before anything else runs.

#### Scenario: Explicit override

- **WHEN** `--conda-env lsst-scipipe-13.0.0` is provided
- **THEN** the sandbox activates `lsst-scipipe-13.0.0` before running EUPS setup
  or the command

### Requirement: Conditional EUPS setup after conda activation

In single-repo mode, the sandbox SHALL run EUPS `setup -r .` from the repo root
only when the repo contains an `ups` directory, and SHALL run it after any conda
activation.

#### Scenario: Repo has an ups directory

- **WHEN** the repo contains an `ups` directory and a `--conda-env` is set
- **THEN** the sandbox activates the conda environment and then runs
  `setup -r .` from the repo root before executing the command

#### Scenario: Repo has no ups directory

- **WHEN** the repo does not contain an `ups` directory
- **THEN** the sandbox skips EUPS setup and runs the command directly

### Requirement: Remove vestigial chdir

The sandbox SHALL NOT set a working directory via `--chdir`. It SHALL leave the
working directory to the process that runs inside the sandbox (e.g., the ACP
server resets it to the main project directory).

#### Scenario: Sandbox does not force a working directory

- **WHEN** the sandbox is launched
- **THEN** no `--chdir` argument is passed to `bwrap`
