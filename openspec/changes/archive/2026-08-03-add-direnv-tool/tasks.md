## 1. DirEnv implementation

- [x] 1.1 Create `tkt/direnv.py` with the BSD-3-Clause header and `__all__ = ("DirEnv",)`, defining a `DirEnv(Tool)` class whose `from_json_data` reads optional `scripts` and `env` lists (each defaulting to empty) and rejects any other keys
- [x] 1.2 Implement `DirEnv.write(ticket, directory, packages, workspace, environment)` to build the mostly-pristine subprocess env: copy the configured `env` var names from the parent plus essential base vars (`HOME`, `PATH`, `SHELL`), discarding the rest of the parent env
- [x] 1.3 Build the subprocess command using `environment.shell`: source each configured `scripts` file in order, then `cd <directory> && setup -r .`, then emit the resulting environment to stdout in a parseable `KEY=VALUE` form (e.g. a final `env`)
- [x] 1.4 Validate that each configured script path exists before running; if missing, raise an error without writing a `.envrc`
- [x] 1.5 Run the subprocess, capturing stdout; on a nonzero exit code raise an error
- [x] 1.6 Emit the entire captured environment as `export KEY=<quoted>` lines rather than computing a diff against pristine; the implicit parent-shell delta is left to direnv
- [x] 1.7 Write the lines to `$WORKSPACE/.envrc`; if the captured environment is empty, leave any existing `.envrc` untouched
- [x] 1.8 Register the `DirEnv` class in `local.json` under `tools.direnv` (module `tkt.direnv`, class `DirEnv`, with `scripts` and `env` lists pointing at the machine's `loadLSST.*` and `LSST_CONDA_ENV_NAME`)

## 2. Integration

- [x] 2.1 Add `"direnv"` to the default `--tool` tuple in `tkt/_cli.py` (`new` command defaults)
- [x] 2.2 Run `ruff check .`, `ruff format --check .`, and `mypy tkt/`; fix any issues
- [x] 2.3 Verify behavior with `tkt new`/`tkt update` in a scratch ticket, confirming a `.envrc` is produced reflecting the full conda/EUPS environment

## 3. Spec validation

- [x] 3.1 Run `openspec validate add-direnv-tool` from the change directory and fix any reported issues
- [x] 3.2 Run `openspec status --change add-direnv-tool` and confirm all artifacts are done and the change is apply-ready
