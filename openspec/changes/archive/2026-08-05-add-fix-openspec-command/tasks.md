## 1. Fix logic in tkt/openspec.py

- [x] 1.1 Add private module-level state/helpers to `tkt/openspec.py`: `_TOOL_RENAMES`, `_ALLOWED_TOOLS_RE`, `_QUESTION_TOOL_INJECTIONS`, plus `_fix_content`, `_apply_injections`, and a directory walker (private functions/classes)
- [x] 1.2 Add an `OpenSpec.fix_skills(directory, *, dry_run=False)` staticmethod that runs the walker, applies the transforms, and returns a result (files changed, warnings), warn-only on missing anchors
- [x] 1.3 Drop the machine-specific hardcoded default directory; no top-level executable entry point

## 2. CLI command

- [x] 2.1 Wire `tkt fix-openspec [DIR] [--dry-run]` to `OpenSpec.fix_skills`, standalone (no environment required), defaulting `DIR` to the current directory
- [x] 2.2 Map "no SKILL.md found" to a non-zero exit (code 2) with an error message

## 3. Wire-up

- [x] 3.1 In `tkt/openspec.py` `OpenSpec.write`, call `OpenSpec.fix_skills` on `<directory>/.opencode/skills/` after the `openspec init` block, unconditionally and warn-only
- [x] 3.2 Add `tkt fix-openspec [DIR] [--dry-run]` to `_cli.py` calling `OpenSpec.fix_skills` (standalone, no environment required, defaulting `DIR` to the current directory)
- [x] 3.3 Update the `AGENTS.md` file-layout description for the `tkt/openspec.py` row to mention the skill fix

## 4. Verification

- [x] 4.1 Run `ruff check .`, `ruff format --check .`, and `mypy tkt/`
- [x] 4.2 Smoke-test `tkt fix-openspec DIR --dry-run` on a directory with a SKILL.md, and confirm exit code 2 on a directory with none
