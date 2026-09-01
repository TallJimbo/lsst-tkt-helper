# AGENTS.md — tkt2

`tkt` is a Python CLI tool for Rubin Observatory DM development. It creates EUPS metapackages tied to Jira tickets, managing git workspaces with multiple packages on the same branch. It integrates with the Zed editor, Pyright, and a `bwrap`-based sandbox for running LLM agents.

Prefer simple solutions to thorough handling of edge cases; the users of this tool are also developers, and they can handle exception tracebacks and other failure modes.

## Development setup

- **Python**: 3.13
- **Dependencies**: `click`, `GitPython`, `pyyaml`, `json5`
- **License**: BSD-3-Clause; all `.py` files include a license header — preserve it for new files.
- **Distribution**: `tkt` is **not** distributed via pip or any package index. It is intended to be used directly from a git clone. There is no `setup.py`, `setup.cfg` (for packaging), or `MANIFEST.in`. Non-`.py` files (e.g. `tkt/AGENTS.md.in`, `pyrightconfig.json`) live alongside the Python source and are located at runtime via `os.path.dirname(__file__)`. Do **not** add packaging configuration.

## Code style & linting

Run these commands before committing:

```sh
ruff check .
ruff format --check .
mypy tkt/
```

- **ruff**: line-length 110, doc-length 79, numpy docstring convention. See `pyproject.toml` for the full configuration.
- **mypy**: type checking. See `pyproject.toml` for configuration.
  `pyproject.toml` for the full configuration.
- **pyright**: `pyrightconfig.json` is for human IDE use only and is **not**
  for linting.

A common gotcha: `ruff format` will move orphaned closing triple-quotes on
single-line docstrings back to the first line, breaking its own
doc-line-length check. Either shorten the single line so it fits, or put the
last word on the second line.

## File layout

- **`tkt/__init__.py`** — Public API exports: `cli`, `Environment`,
  `Workspace`.
- **`tkt/_cli.py`** — Click-based CLI commands: `new`, `update`,
  `upgrade-metapackage`, `rm`, `agent-run` (as `sandbox-run`), `sandbox-reset`,
  `pull-sandbox`.
- **`tkt/_environment.py`** — Abstract base classes `Environment` and `Tool`.
  `Environment` is subclassed per observatory (e.g. `RubinEnvironment`); `Tool`
  is subclassed per integration (e.g. `Zed`, `Pyright`, `Sandbox`).
- **`tkt/_workspace.py`** — `Workspace` class: manages the git/EUPS workspace
  lifecycle (create, update, upgrade metapackage, remove).
- **`tkt/rubin.py`** — `RubinEnvironment`: LSST DM-specific `Environment`
  subclass. Reads `repos.yaml` for package origins.
- **`tkt/sandbox.py`** — `Sandbox` tool: runs an LLM agent inside a `bwrap`
  sandbox with a read-only view of the human's worktree and a writable git
  worktree on a separate branch.
- **`tkt/pull.py`** — `Pull` helper: implements `tkt pull-sandbox`, transferring
  committed and/or uncommitted agent work from `.agent/<pkg>` worktrees onto
  human-workspace branches, with a resumable `--finish`/`--abort` lifecycle and a
  per-workspace ledger.
- **`tkt/zed.py`** — `Zed` tool: writes Zed editor configuration into the
  workspace.
- **`tkt/pyright.py`** — `Pyright` tool: writes `pyrightconfig.json` into the
  workspace.
- **`tkt/precommit.py`** — `PreCommit` tool: installs pre-commit or prek git
  hooks when configuration files are present in packages.
- **`tkt/openspec.py`** — `OpenSpec` tool: runs `openspec init` into the
  workspace and rewrites the generated `.opencode/skills/` files for OpenCode's
  harness (`fix_skills`, exposed via `tkt fix-openspec`).
- **`tkt/utils.py`** — JSON read/write helpers (uses `json5` for reading to
  allow trailing commas).
- **`harnesses/opencode/agents/`** — Custom OpenCode workflow agents
  `sp-design`, `sp-plan`, `sp-build`, `sp-debug`, `sp-review`;
  `~/.config/opencode/agents/` is a symlink to it (via `tkt
  install-opencode-agent`).
- **`harnesses/zed/`** — Zed harness: `rules.md` (role-scoped dispatch table,
  symlinked to `~/.config/zed/AGENTS.md`) and `skills/<name>/` (Zed-only skills,
  symlinked to `~/.agents/skills/<name>`); see `harnesses/README.md` for
  content-placement rules.
- **`superpowers/`** — Git submodule (TallJimbo's fork of `obra/superpowers`)
  providing the `skills/` the `sp-*` agents use; pinned by this repo.
- **`investigations/`** — Uncomitted notes, logs, and general investigation
  scratch space. Includes the git source repos for both Zed and OpenCode.

## Configuration

- **`local.json`**: User-specific environment configuration (workspace path, `repos.yaml` location, externals, and tool definitions). This file is committed to the repository.
- **`pyproject.toml`**: Ruff and mypy configuration.
- **`pyrightconfig.json`**: Pyright configuration for IDE use (see above).
- **`ups/tkt.table`**: EUPS table file that prepends `bin/` to `PATH` and the package root to `PYTHONPATH`.

## OpenCode integration

The `sp-*` workflow agents live in this repo at `harnesses/opencode/agents` and
are exposed to OpenCode through a directory symlink, `~/.config/opencode/agents`
-> `harnesses/opencode/agents`, created by `tkt install-opencode-agent`. The
skills they use come from the `superpowers/` submodule;
`~/.config/opencode/opencode.jsonc` points its `skills.paths` at
`superpowers/skills`. Neither lives at `~/.config/opencode/superpowers` anymore.

## Zed integration

The Zed native-agent harness lives in `harnesses/zed/`: `rules.md` is the
role-scoped dispatch table (the Zed global AGENTS.md) and `skills/` holds the
Zed-only skills. They are exposed via `tkt install-zed-agent`, which symlinks
`harnesses/zed/skills/<name>` to `~/.agents/skills/<name>`, each shared
superpowers skill (`superpowers/skills/<name>`) to `~/.agents/skills/<name>`,
and `harnesses/zed/rules.md` to `~/.config/zed/AGENTS.md`. See
`harnesses/README.md` for the content-placement rules.

## Testing

Uses `pytest` (`tests/`) for git-worktree behaviors; see `tests/test_sandbox.py`
and `tests/test_pull.py`. Run with `python -m pytest`.
