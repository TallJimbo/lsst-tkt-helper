## Why

The OpenSpec skills installed per-project by `openspec init --tools opencode` are written for Claude Code and reference tools that don't exist in OpenCode's harness (`AskUserQuestion` → `question`, `TodoWrite` → `todowrite`) plus a frontmatter key (`allowed-tools`) that OpenCode ignores. A working fix script currently lives outside `tkt` at `~/.config/opencode/investigation/scripts/fix-openspec-skills.py`; it is not reproducible or tied to workspace creation.

## What Changes

- Add the pure, idempotent fix logic (tool renames, `allowed-tools` stripping, `openspec-explore` question-tool injection, warn-only on missing anchors) to the existing `tkt/openspec.py`, exposed as a `staticmethod` on the `OpenSpec` Tool with helpers as private functions/classes in the same module.
- Add a new standalone CLI command `tkt fix-openspec [DIR] [--dry-run]` that runs the fix on a directory tree (default: current directory), with no environment required.
- Have `tkt.openspec.OpenSpec.write` auto-invoke the fix on the freshly-generated `.opencode/skills/` after running `openspec init --tools opencode`.
- Remove the machine-specific hardcoded default directory from the standalone script; the logic moves into `tkt` and is no longer a standalone script.

## Capabilities

### New Capabilities

- `fix-openspec-skills`: Rewrites per-project OpenSpec skill files so they adapt to OpenCode's harness instead of Claude Code's, covering tool-name renames, removal of the `allowed-tools` frontmatter restriction, and injection of `question`-tool suggestions into `openspec-explore` at anchored decision points (warn-only if an anchor disappears).

### Modified Capabilities

None. (There is no existing `openspec-tool` spec; the auto-invoke behavior is folded into the new `fix-openspec-skills` capability.)

## Impact

- Extended `tkt/openspec.py` (new `OpenSpec.fix_skills` staticmethod plus private helper functions/classes; `write` auto-invokes the fix).
- New CLI command in `tkt/_cli.py`.
- `AGENTS.md` (note the new logic in the `tkt/openspec.py` row), and the standalone script at `~/.config/opencode/investigation/scripts/fix-openspec-skills.py` becomes redundant.
- No new runtime dependencies; uses only stdlib (`re`, `pathlib`, `sys`) and existing project conventions.
