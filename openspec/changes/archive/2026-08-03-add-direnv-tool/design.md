## Context

`tkt` manages EUPS metapackages tied to Jira tickets. Workspaces are git
checkouts with an EUPS table in `ups/`. A `Tool` subclass (per
`tkt/_environment.py`) has `from_json_data(cls, data)` and
`write(ticket, directory, packages, workspace, environment)`. Tools are
configured in `local.json`, selected by `Workspace.tools`, and run by
`Workspace._write_tools` on `tkt new`, `tkt update`, and
`tkt upgrade-metapackage`. The sandbox tool already performs EUPS setup in a
subprocess (`setup -r .`); `DirEnv` mirrors that but captures the environment
instead of running an agent.

## Goals / Non-Goals

**Goals:**

- Automatically maintain a `$WORKSPACE/.envrc` that reproduces the full
  conda/EUPS environment `loadLSST` + `setup -r .` would establish in the
  workspace, so a fresh shell entering the directory gets the complete Rubin
  environment.
- Regenerate the `.envrc` on `new`, `update`, and `upgrade-metapackage`.
- Capture from a mostly-pristine subprocess, propagating only configured parent
  envvars, and emit the whole resulting environment (leaving the implicit
  parent-shell delta to direnv).
- Make the bootstrapping shell scripts and propagated envvars configurable in
  `local.json`.
- Add `direnv` to the default tool set.

**Non-Goals:**

- Replicating shell functions or aliases (impossible via direnv).
- Running the setup dynamically on every directory entry — we snapshot, we do
  not source `loadLSST.*` each time.
- Propagating the whole parent environment into the capture — only configured
  envvars are propagated.

## Decisions

### 1. Mostly-pristine subprocess with configurable scripts and propagated envvars

Rather than inheriting the parent's full shell environment, the capture
subprocess runs in a near-pristine environment. `DirEnv` configuration carries
two lists:

- `scripts`: ordered, absolute shell-script paths to source in the subprocess
  (e.g. `/home/jbosch/LSST/install/loadLSST.bash`).
- `env`: names of parent envvars to copy into the pristine subprocess env before
  sourcing the scripts (e.g. `LSST_CONDA_ENV_NAME`).

The base pristine env retains only essential vars needed for the shell and the
scripts to function (e.g. `HOME`, `PATH`, `SHELL`); everything else is dropped
unless listed in `env`. `environment.shell` launches the subprocess, and the user
configures the matching `loadLSST.<shell>` variant.

*Alternatives considered:* inherit the full parent env and diff against it
(rejected: suppresses the conda/EUPS envvars the user wants captured); rely on an
inherited `setup` function from `setups.sh` (rejected: doesn't bring over the
conda/EUPS-itself env, and bash functions aren't exported to a non-interactive
subshell anyway).

### 2. Emit the whole captured environment, not a computed delta

The `.envrc` contains `export` lines for the entire captured environment: the base
vars and configured parent envvars propagated into the subprocess, plus everything
`loadLSST` + `setup -r .` introduced (conda envvars, EUPS-itself envvars
such as `EUPS_PATH`, `EUPS_DIR`, `EUPS_PKGROOT`, `EUPS_SHELL`,
`SETUP_EUPS`, and the workspace package setups). The implicit delta against the
parent shell is left to direnv, which diffuses the evaluated env to the parent;
`DirEnv` does not compute a diff.

### 3. Single subprocess invocation

All setup work happens in one `bash -c` so the capture is deterministic within a
single call: the script records nothing on the host; stdout carries the resulting
environment (e.g. a final `env`). A nonzero exit code propagates as an error.

### 4. `.envrc` is a full snapshot

The `.envrc` contains one `export KEY=value` line per captured variable. If the
captured environment is empty, the tool leaves any existing `.envrc` untouched.
PATH is written as a full override (`export PATH=...`) so it correctly replaces
the parent PATH.

### 5. Config surface

`from_json_data` reads optional `scripts` (default `[]`) and `env` (default
`[]`) keys and rejects any others. Both default to empty lists, so the tool is
inert unless configured. The `local.json` entry supplies the machine-specific
values.

## Risks / Trade-offs

- [Pristine subprocess differs subtly from the user's interactive setup] →
  Mitigation: it sources the same `loadLSST.*` the user would source and runs
  the same `setup -r .`; because it starts pristine, it captures more, not less,
  than the interactive shell.
- [Configurable scripts are machine-specific] → Mitigation: they live in
  `local.json`, which is already the user-specific configuration file; the tool
  places no constraints beyond requiring each script to exist.
- [Capturing PATH as a full override] → Mitigation: write the complete new PATH
  value as `export PATH=...`, correctly replacing the parent without `PATH_add`.
- [Capture runs EUPS setup on the host every write] → Mitigation: acceptable;
  writes are infrequent and the cost is bounded by the configured scripts.
- [Subprocess setup fails or missing scripts] → Mitigation: propagate a nonzero
  exit code as an error and do not write a stale `.envrc`; validate configured
  script paths exist.
