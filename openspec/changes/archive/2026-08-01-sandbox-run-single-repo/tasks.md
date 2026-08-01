## 1. Remove vestigial chdir

- [x] 1.1 In `Sandbox._build_bwrap_argv`, remove the `--chdir agent_dir` argument and the
  agent_dir computation that feeds it; leave the working directory to the process run
  inside the sandbox (the ACP server resets it to the main project directory).
- [x] 1.2 Adjust any inner-script path assumptions so `setup -r .` and the command still
  work from the inherited working directory. (`AGENTS.md.in` is intentionally left
  unchanged; see task 5.1.)

## 2. Refactor sandbox for mode parameterization

- [x] 2.1 Add a private variant of the bwrap argv construction that takes a plain repo
  directory instead of a `Workspace`, shared with the existing workspace-mode builder
  for the common base (ro-bind `/`, dev/proc, tmpfs `/tmp` and `$HOME`, namespaces,
  configured mounts and env).
- [x] 2.2 In single-repo mode, bind-mount the repo root read-write (`--bind`) with no
  separate `.git` handling; in workspace mode keep the existing per-package ro-worktree
  + rw-`.git` + `.agent` behavior unchanged.

## 2b. Factor config/tool loading out of Environment

- [x] 2b.1 Add a static `Environment.load_config(f)` that returns `(cls, data)`
  without instantiating, and reimplement `Environment.from_file` on top of it.
- [x] 2b.2 Rename `_read_tools` to `load_tools` (or keep as a classmethod) so the
  CLI can call `cls.load_tools(data)` directly without an instance; update
  `RubinEnvironment.from_json_data` to use it.
- [x] 2b.3 Verify workspace-mode `Environment.from_file` path behaves identically.

## 3. Single-repo mode in the CLI

- [x] 3.1 Add the `--conda-env <name>` option to the `sandbox-run` command.
- [x] 3.2 In `sandbox_run`, detect mode from CWD (presence of `.agent`); in single-repo
  mode, resolve the config file via the existing `--environment`/`TKT_ENVIRONMENT`
  mechanism, call `Environment.load_config` then `cls.load_tools(data)` to build the
  `Sandbox` (bypassing `Workspace.from_existing` and `Environment` instantiation),
  applying the `--conda-env` override.
- [x] 3.3 Wire the new single-repo run path into the CLI and keep the workspace-mode
  path intact.

## 4. Inner setup script

- [x] 4.1 Build the single-repo inner script: optional conda activation (source the conda
  base's `etc/profile.d/conda.sh` derived from `$CONDA_PREFIX` or `which conda`, then
  `conda activate <name>`), followed by `setup -r .` only when an `ups` directory
  exists, then `exec <command>`.
- [x] 4.2 Ensure EUPS setup runs after conda activation.

## 5. Documentation

- [x] 5.1 No change to `tkt/AGENTS.md.in`: in single-repo mode the repo has its
  own AGENTS.md, and `tkt/AGENTS.md.in` is only installed for workspace mode.
- [x] 5.2 Update the CLI help text for `sandbox-run` to describe autodetection and the
  `--conda-env` option.
