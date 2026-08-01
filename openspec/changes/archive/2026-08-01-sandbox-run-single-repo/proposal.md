## Why

`tkt sandbox-run` currently only works on a multi-package EUPS workspace that was
created with `tkt new` (it requires a `tkt.json` describing a ticket, metapackage,
packages, and externals). Many projects do not fit that shape — they are a single
git repository that shares the host's conda environment and optionally carries its
own EUPS `ups/` directory for setup (e.g. `tkt2` itself). There is no way to run
the sandbox on such a repository today.

## What Changes

- `tkt sandbox-run` now **autodetects** the mode from the current working
  directory:
  - If `.agent/` exists in the working directory → **workspace mode** (unchanged
    current behavior).
  - Otherwise → **single-repo mode**, treating the working directory as the root
    of a single git repository.
- In single-repo mode, the agent writes directly to the main (repo-default) git
  worktree: the whole repository is bind-mounted read-write, with no separate
  `.agent` worktrees and no `-agent` branch.
- The sandbox inner setup script is generalized:
  - Uses the currently active conda environment by default.
  - A new `--conda-env <name>` CLI option overrides this by activating the named
    environment inside the sandbox before anything else.
  - EUPS `setup -r .` runs automatically **after** conda activation, and only
    when the working directory contains an `ups/` directory (autodetect).
- Remove the vestigial `--chdir` to the agent directory from the sandbox: the
  ACP server resets the working directory to the main project directory on its
  own, so the sandbox does not need to set it.

## Capabilities

### New Capabilities

- `sandbox-run-single-repo`: running the tkt sandbox on an individual git
  repository that is not a multi-package EUPS ticket workspace.

### Modified Capabilities

None. There are no existing specs.

## Impact

- `tkt/sandbox.py` — mode autodetection, generalized mount construction, and
  generalized inner setup script.
- `tkt/_cli.py` — the `sandbox-run` command: autodetect mode, new
  `--conda-env` option, and bypassing `Workspace`/`Environment` loading in
  single-repo mode.
- `tkt/_environment.py` — factor config-handling and tool-loading into
  static/class methods the CLI can call without instantiating an `Environment`.
- `tkt/_workspace.py` — likely no change; single-repo mode does not use
  `Workspace`.
- `tkt/AGENTS.md.in` — no change; in single-repo mode the repo has its own
  AGENTS.md, and this template is only installed for workspace mode.
- No new dependencies.
