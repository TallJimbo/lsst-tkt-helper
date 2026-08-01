## Context

`tkt sandbox-run` builds a `bwrap` argv in two modes (workspace and single-repo).
The final command emitted inside the sandbox comes from a single point,
`Sandbox._build_inner_script()`, which currently emits `exec /bin/bash --login -i`
when `--shell` is given, otherwise `exec {shlex.join(self._command)}` where
`self._command` is read from `local.json` (in practice `opencode acp`). The
configured command cannot be overridden per invocation without editing config.

The user wants to run opencode's TUI (`opencode`, the default subcommand)
interactively inside the sandbox terminal, rather than the ACP server, without
editing `local.json`. They confirmed the option should apply in both modes, that
`--command` and `--shell` are mutually exclusive, and that they run from a real
TTY (so no extra pty plumbing is needed — `bwrap` inherits the controlling
terminal).

## Goals / Non-Goals

**Goals:**
- Add a `--command STRING` option to `tkt sandbox-run` that overrides the final
  command in both workspace and single-repo mode.
- Preserve existing behavior when the option is not given.
- Enforce mutual exclusivity with `--shell`.

**Non-Goals:**
- No config-file format changes; the option is purely per-invocation.
- No change to TTY/pty handling; not in scope.
- Do not subsume `--shell` into `--command` (kept as a distinct flag).

## Decisions

### Override the final command, not the bwrap invocation

`--command` only changes the last line of `_build_inner_script` (the `exec ...`
after EUPS setup), leaving all mounts, namespaces, and environment setup
untouched. This mirrors how `--shell` already works.

### Use a shlex-split string, mirroring config semantics

The sandbox config already accepts `command` as either a string (shlex-split in
`from_json_data`) or an argv list. The CLI option naturally takes a string; we
shlex-split it to keep the two representations consistent. Alternative of passing
pre-parsed argv via multiple `--command` occurrences was rejected as more verbose
and inconsistent with config.

### Thread `command` through both modes symmetrically

`--command` applies in both workspace (`run`) and single-repo
(`run_single_repo`) modes, so both methods and both `_build_*_argv` helpers gain
a `command` parameter that flows into `_build_inner_script`. Precedence in
`_build_inner_script`: `shell` > `command` > configured `self._command`.

### Enforce mutual exclusivity in the CLI

Use a `click.UsageError` when both `--command` and `--shell` are set. This is
simplest and consistent with existing `UsageError` checks in `sandbox_run`.

## Risks / Trade-offs

- [Command string parsing ambiguity] → shlex-split matches the existing config
  convention; documents that `--command` behaves exactly like a string
  `command` in config.
- [Precedence confusion between shell/command/config] → single explicit
  precedence (`shell` > `command` > configured) plus a hard error when both flags
  are set; no silent fall-through.
- [Invalid command still launches a broken sandbox] → same failure mode as today
  with the configured command; users see the error from inside the sandbox.
  Acceptable for a developer-facing tool per project philosophy.

## Migration Plan

N/A — new opt-in flag; default behavior unchanged. Rollback is trivial (remove
the flag). No data or config migration.

## Open Questions

None.
