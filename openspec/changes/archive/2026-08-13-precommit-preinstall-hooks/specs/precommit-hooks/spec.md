## ADDED Requirements

### Requirement: Hook environments are pre-installed at setup
When the `PreCommit` tool configures hooks for a package (during `tkt new` or `tkt update`), it SHALL install the hook environments declared in the package's config immediately, in addition to registering the git hook shim. This pre-install SHALL use the tool-native flag (`--prepare-hooks` for `prek`, `--install-hooks` for `pre-commit`) and SHALL be idempotent across repeated runs.

#### Scenario: Setup pre-installs hook environments
- **WHEN** a package with a `.pre-commit-config.yaml` (or `prek.toml`) config is set up by `tkt new` or `tkt update`
- **THEN** the hook environments for all hooks in the config are installed into the shared store at setup time, without waiting for a commit

#### Scenario: Repeated setup does not re-fetch installed environments
- **WHEN** `tkt update` runs again for the same package with unchanged hooks
- **THEN** already-installed hook environments are reused and not re-downloaded

### Requirement: Hook environments are available offline to the agent
The shared hook-environment store SHALL be mounted read-write into the sandbox so that an agent's first commit runs the configured hooks without network access.

#### Scenario: Agent first commit runs hooks offline
- **WHEN** an agent in a network-restricted sandbox makes the first commit in an agent worktree
- **THEN** the pre-commit or prek hook environments are found in the shared store and the hooks run without attempting a network install

#### Scenario: Store not mounted means first commit cannot run offline
- **WHEN** the sandbox does not mount the hook-environment store
- **THEN** the agent's first commit attempts to install hook environments and fails due to the network restriction (unchanged from prior behavior)
