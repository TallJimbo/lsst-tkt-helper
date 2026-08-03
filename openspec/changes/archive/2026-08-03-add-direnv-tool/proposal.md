## Why

When a developer enters a tkt workspace directory in their shell, the Rubin
conda/EUPS environment is not automatically available: they must remember to run
`setup -r .` (and keep it in sync with the workspace's EUPS table) every time.
Adding a `DirEnv` tool that captures the EUPS-set environment into a `direnv`
`.envrc` file lets the shell provision the environment on directory entry,
mirroring the setup that `tkt` already generates for the sandbox.

## What Changes

- Add a new `DirEnv` `Tool` subclass in `tkt/direnv.py`.
- On `tkt new`, `tkt update`, and `tkt upgrade-metapackage`, after the EUPS
  table is written, run a subprocess shell in the workspace directory with a
  **mostly-pristine** environment: only a configured list of parent envvars is
  propagated; then a configured list of shell scripts (e.g.
  `/home/jbosch/LSST/install/loadLSST.bash`) is sourced; then `setup -r .`
  runs.
- Emit the entire captured environment to `$WORKSPACE/.envrc` as `export`
  lines; the implicit delta against the parent shell is left to direnv.
- Add `direnv` to the default tool list of `tkt new` and register the tool in
  `local.json` with its `scripts` and `env` lists.

## Capabilities

### New Capabilities

- `direnv-tool`: writes a `direnv` `.envrc` file into a tkt workspace
  directory capturing the environment that `setup -r .` produces.

### Modified Capabilities

<!-- No existing specs are affected; the new tool is additive. -->

## Impact

- **New module**: `tkt/direnv.py` with the `DirEnv` class (BSD-3-Clause header).
- **`tkt/_cli.py`**: add `"direnv"` to the default `--tool` tuple of `tkt new`.
- **`tkt/_environment.py`**: no change required (the `Tool` ABC already fits).
- **`local.json`**: add a `direnv` entry under `tools` with `scripts` and `env`
  keys.
- **Runtime dependency**: a POSIX shell and an installed Rubin install tree
  providing `loadLSST.*`; no new third-party Python packages.
- No packaging changes (consistent with the rest of `tkt`).
