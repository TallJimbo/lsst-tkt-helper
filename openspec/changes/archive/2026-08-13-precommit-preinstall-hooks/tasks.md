## 1. Pre-install hook environments in PreCommit tool

- [x] 1.1 In `tkt/precommit.py` `_run_for_package`, append the tool-native
      pre-install flag to the `install` argv: `--prepare-hooks` when `use_prek`
      is true, `--install-hooks` otherwise (i.e. pre-commit). Keep using the
      same `executable` and existing `cwd`/`capture_output`/warning-on-nonzero
      behavior.

## 2. Share the hook-environment store into the sandbox

- [x] 2.1 Confirm the default store path for the active tool (prek:
      `~/.cache/prek`; pre-commit: `~/.cache/pre-commit`).
- [x] 2.2 Add the active hook-environment store to the `sandbox` tool's
      `mounts.rw` in `local.json` so the agent's first commit reuses the
      pre-built environments offline.

## 3. Verification

- [x] 3.1 Run `ruff check .` and `ruff format --check .` on the modified
      `tkt/precommit.py`.
- [x] 3.2 Run `mypy tkt/`.
- [x] 3.3 Run `tkt update` (or a fresh `tkt new` in a temp workspace) against an
      existing package and confirm hook environments are installed at setup
      (store populated) without a commit.
- [x] 3.4 Launch `tkt sandbox-run` and confirm an agent worktree's first commit
      runs the configured hooks without network (offline).
