# fix-openspec-skills

## Purpose

Rewriting OpenSpec skill files (as installed by `openspec init --tools opencode`)
so they work correctly with OpenCode's harness: renaming Claude Code tool
references, dropping the unsupported `allowed-tools` frontmatter restriction, and
injecting question-tool suggestions at key decision points. Provided both as a
standalone `tkt fix-openspec` command and invoked automatically by
`OpenSpec.write`.

## Requirements

### Requirement: Standalone fix command

`tkt` SHALL provide a `fix-openspec` CLI command that takes an optional positional `DIR` (defaulting to the current working directory) and an optional `--dry-run` flag. It SHALL recursively find every `SKILL.md` under `DIR` and apply the OpenCode skill fixes to each. It SHALL NOT require an environment to be configured.

#### Scenario: Fix a directory tree

- **WHEN** a user runs `tkt fix-openspec path/to/project`
- **THEN** every `SKILL.md` under `path/to/project` is rewritten with the OpenCode skill fixes applied
- **AND** the command reports the number of files updated and any warnings

#### Scenario: Default to current directory

- **WHEN** a user runs `tkt fix-openspec` with no `DIR`
- **THEN** the current working directory is scanned for `SKILL.md` files

#### Scenario: Dry run does not write

- **WHEN** a user runs `tkt fix-openspec DIR --dry-run`
- **THEN** the command reports which files would be updated and the warnings
- **AND** it does not modify any file

#### Scenario: No skills found

- **WHEN** a user runs `tkt fix-openspec DIR` and `DIR` contains no `SKILL.md` files
- **THEN** the command exits with a non-zero status and an error message

### Requirement: Tool name renames

The fix SHALL replace Claude Code tool references with the OpenCode equivalents in every skill file: `AskUserQuestion tool` → `question tool`, `AskUserQuestion` → `question`, `TodoWrite tool` → `todowrite tool`, and `TodoWrite` → `todowrite`. The longer forms (with `tool`) SHALL be replaced before the bare names so bare names are not double-replaced.

#### Scenario: Rename all Claude tool references

- **WHEN** a skill file contains `AskUserQuestion tool`, `AskUserQuestion`, `TodoWrite tool`, and `TodoWrite`
- **THEN** the fix rewrites them to `question tool`, `question`, `todowrite tool`, and `todowrite` respectively

#### Scenario: No double replacement of bare names

- **WHEN** a skill file contains the bare name `AskUserQuestion` after a `AskUserQuestion tool` has already been handled
- **THEN** the bare name is not re-replaced by the `AskUserQuestion tool` rule

### Requirement: Remove allowed-tools frontmatter restriction

The fix SHALL remove the `allowed-tools: Bash(openspec:*)`-style line from the frontmatter of every skill file, because OpenCode's skill loader only reads `name` and `description`.

#### Scenario: Allowed-tools line removed

- **WHEN** a skill file's frontmatter contains a line matching `allowed-tools: Bash(...)`
- **THEN** the fix removes that line from the file

### Requirement: Inject question-tool suggestions into openspec-explore

The fix SHALL inject text recommending the `question` tool into the `openspec-explore` skill at specific decision-point anchors. For each injection, if the anchor is missing, the fix SHALL emit a warning (not fail) so the loss is not unnoticed. The fix SHALL be idempotent: the injected text itself is the "already applied" guard.

#### Scenario: Question-tool injection at an existing anchor

- **WHEN** the `openspec-explore` skill contains an expected anchor line
- **THEN** the fix inserts the `question`-tool suggestion immediately after that anchor

#### Scenario: Injection is idempotent

- **WHEN** the fix is run a second time on an already-fixed skill
- **THEN** no further changes are made

#### Scenario: Missing anchor warns

- **WHEN** an expected anchor line is absent from the `openspec-explore` skill (e.g. upstream changed)
- **THEN** the fix emits a warning naming the file and the missing anchor
- **AND** continues processing without failing

### Requirement: OpenSpec tool auto-invokes the fix

`tkt.openspec.OpenSpec.write` SHALL run the fix on the workspace's `.opencode/skills/` directory after running `openspec init --tools opencode`, ensuring the freshly-generated skills are OpenCode-ready. It SHALL do so idempotently and warn (not fail) on missing anchors.

#### Scenario: Skills fixed after init

- **WHEN** `OpenSpec.write` runs `openspec init --tools opencode` in a workspace directory
- **THEN** it runs the skill fix on `<workspace>/.opencode/skills/`
- **AND** the generated skills are rewritten to use OpenCode tool names

#### Scenario: Auto-invoke is idempotent and warn-only

- **WHEN** `OpenSpec.write` is re-invoked (e.g. by `tkt update` or `tkt upgrade-metapackage`) on an already-fixed workspace
- **THEN** the fix makes no changes
- **AND** a missing anchor produces only a warning, never a failure
