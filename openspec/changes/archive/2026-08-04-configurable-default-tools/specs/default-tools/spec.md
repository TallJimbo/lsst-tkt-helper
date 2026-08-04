## ADDED Requirements

### Requirement: Configurable default tools list

The environment configuration JSON SHALL require a top-level `default_tools` key containing an ordered list of tool names that `tkt` should install into new workspaces by default. The `Environment` SHALL expose this list. If the key is absent, loading the configuration SHALL raise an error naming the missing key.

#### Scenario: Default tools configured

- **WHEN** an environment config contains `"default_tools": ["zed", "direnv"]`
- **THEN** the `Environment` exposes the list `["zed", "direnv"]`

#### Scenario: Default tools absent

- **WHEN** an environment config has no `default_tools` key
- **THEN** loading the configuration raises an error naming `default_tools`

### Requirement: New workspace tool selection

`tkt new` SHALL start its tool set from the environment's configured `default_tools`. Each `--add-tool NAME` option SHALL add NAME to the set, and each `--remove-tool NAME` option SHALL remove NAME from the set. When the same name is both added and removed, removal SHALL take precedence.

#### Scenario: Defaults with no options

- **WHEN** a user runs `tkt new TICKET PACKAGES` with `default_tools` set to `["zed", "direnv"]` and passes no add/remove options
- **THEN** the created workspace records tools `["zed", "direnv"]`
- **AND** each configured default tool is written into the workspace

#### Scenario: Add a tool

- **WHEN** a user runs `tkt new TICKET PACKAGES --add-tool pyright` with `default_tools` set to `["zed", "direnv"]`
- **THEN** the created workspace records tools `["zed", "direnv", "pyright"]`

#### Scenario: Remove a tool

- **WHEN** a user runs `tkt new TICKET PACKAGES --remove-tool direnv` with `default_tools` set to `["zed", "direnv"]`
- **THEN** the created workspace records only `["zed"]`

#### Scenario: Add and remove same tool

- **WHEN** a user runs `tkt new TICKET PACKAGES --add-tool pyright --remove-tool pyright` with `default_tools` set to `["zed"]`
- **THEN** the created workspace records only `["zed"]`

#### Scenario: Remove non-default tool

- **WHEN** a user runs `tkt new TICKET PACKAGES --remove-tool nosuch` with `default_tools` set to `["zed"]`
- **THEN** the created workspace records `["zed"]` with no error

### Requirement: Update proposes missing default tools

`tkt update` SHALL compare the workspace's recorded tools against the environment's `default_tools`. For each default tool missing from the workspace, it SHALL present a single interactive prompt listing all proposed additions, and add them only if the user confirms.

#### Scenario: Missing default tool is proposed

- **WHEN** a workspace records tools `["zed"]` and `default_tools` is `["zed", "direnv", "pyright"]`
- **THEN** `tkt update` prompts the user once, listing `direnv` and `pyright` as proposed additions
- **AND** if the user confirms, the workspace records all three and the newly added tools are written

#### Scenario: No missing default tools

- **WHEN** a workspace records every tool in `default_tools`
- **THEN** `tkt update` asks no questions about additions

### Requirement: Update removes unconfigured tools

`tkt update` SHALL remove from the workspace any tool that is no longer configured at all in the environment (absent from the `tools` dict), and SHALL emit a warning naming each removed tool. Tools that are still configured but no longer appear in `default_tools` SHALL be left in place.

#### Scenario: Tool no longer configured is removed

- **WHEN** a workspace records tools `["zed", "stale"]` and the environment config has no `"stale"` entry in `tools`
- **THEN** `tkt update` removes `stale` from the workspace with a warning
- **AND** the remaining tools are written

#### Scenario: Tool still configured but not default is retained

- **WHEN** a workspace records `["zed"]`, `default_tools` is `["zed"]`, and the environment still configures `"direnv"` in `tools`
- **THEN** `tkt update` leaves the workspace's tools unchanged apart from any additions or removals

### Requirement: Dry run does not prompt or apply

When `tkt update` is run with `--dry-run`, it SHALL log the additions it would propose and the removals it would perform instead of prompting the user or modifying the workspace.

#### Scenario: Dry run logs proposed additions

- **WHEN** `tkt update --dry-run` encounters a workspace missing a default tool
- **THEN** it logs that it would ask to add the tool
- **AND** it does not prompt the user
- **AND** it does not modify the workspace's recorded tools

#### Scenario: Dry run logs removal

- **WHEN** `tkt update --dry-run` encounters a tool no longer configured
- **THEN** it logs that it would remove the tool
- **AND** it does not remove the tool

### Requirement: Missing tool error names the tool

When a tool selected for a new workspace — either from `default_tools` or via `--add-tool` — is not present in the environment's configured `tools` dict, the resulting error SHALL name the missing tool so the user can fix the configuration.

#### Scenario: Unconfigured default tool

- **WHEN** `default_tools` contains `"direnv"` but the environment's `tools` dict has no `"direnv"` entry
- **THEN** attempting to write that tool raises an error whose message names `direnv`

#### Scenario: Unconfigured add tool

- **WHEN** a user runs `tkt new TICKET PACKAGES --add-tool nosuch` and the environment's `tools` dict has no `"nosuch"` entry
- **THEN** attempting to write that tool raises an error whose message names `nosuch`
