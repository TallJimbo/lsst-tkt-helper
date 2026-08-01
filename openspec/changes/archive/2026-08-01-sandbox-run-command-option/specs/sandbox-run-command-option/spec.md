## ADDED Requirements

### Requirement: Override final command via --command

`tkt sandbox-run` SHALL accept a `--command <string>` option that overrides the
configured final command for that invocation. The string SHALL be split into an
argv list the same way a string `command` in the sandbox tool configuration is
split (shlex). The override SHALL apply in both workspace mode and single-repo
mode.

#### Scenario: Override command in workspace mode

- **WHEN** `tkt sandbox-run --command "/home/jbosch/.opencode/bin/opencode"` is
  run from a directory containing a `.agent` subdirectory (workspace mode)
- **THEN** the sandbox runs `exec /home/jbosch/.opencode/bin/opencode` as the
  final command, replacing the configured command, after any EUPS setup

#### Scenario: Override command in single-repo mode

- **WHEN** `tkt sandbox-run --command "/home/jbosch/.opencode/bin/opencode"` is
  run from a directory without a `.agent` subdirectory (single-repo mode)
- **THEN** the sandbox runs `exec /home/jbosch/.opencode/bin/opencode` as the
  final command, replacing the configured command, after any conda activation and
  EUPS setup

#### Scenario: Default command unchanged

- **WHEN** `tkt sandbox-run` is run without `--command` or `--shell`
- **THEN** the sandbox emits the configured command as before

### Requirement: --command and --shell are mutually exclusive

Providing both `--command` and `--shell` SHALL be rejected as an error.

#### Scenario: Both options given

- **WHEN** `tkt sandbox-run --shell --command "opencode"` is run
- **THEN** `tkt` reports a usage error and does not launch the sandbox

#### Scenario: --shell alone

- **WHEN** `tkt sandbox-run --shell` is run
- **THEN** the sandbox emits `exec /bin/bash --login -i` as before
