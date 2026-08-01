## Why

`tkt sandbox-run` always emits the configured sandbox command (in practice
`opencode acp`, the non-interactive ACP server). There is no way to run opencode
interactively inside the sandbox without editing `local.json`. This change adds a
`--command` option so the final command can be overridden per invocation, e.g. to
launch opencode's TUI in a terminal.

## What Changes

- Add a `--command STRING` option to `tkt sandbox-run` that overrides the
  configured final command for the invocation. The string is shlex-split into an
  argv list, mirroring the existing config behavior.
- The override works in **both** workspace mode and single-repo mode.
- `--command` and `--shell` are mutually exclusive; providing both is an error.
- When `--command` is not given, behavior is unchanged (uses the configured
  command, or `--shell`'s login shell when `--shell` is given).

## Capabilities

### New Capabilities

- `sandbox-run-command-option`: Overriding the final command emitted by
  `tkt sandbox-run` via a `--command` option, in both workspace and single-repo
  modes.

### Modified Capabilities

<!-- No existing capability requirements change. -->

## Impact

- `tkt/_cli.py`: add the `--command` option to `sandbox_run` and thread it into
  both the workspace-mode `run()` and single-repo-mode `run_single_repo()` calls.
- `tkt/sandbox.py`: add `command` parameter to `run()`, `run_single_repo()`,
  `_build_bwrap_argv()`, `_build_single_repo_argv()`, and
  `_build_inner_script()`; in `_build_inner_script()`, prefer `shell`, then
  `command`, then the configured `self._command`.
- `tkt/_cli.py`: enforce mutual exclusivity of `--command` and `--shell`.
- No config files change; the option is purely per-invocation.
- Docs: none beyond the new option help text.
