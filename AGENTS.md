# AGENTS.md — tkt2

`tkt` is a Python CLI tool for Rubin Observatory DM development. It creates EUPS metapackages tied to Jira tickets, managing git workspaces with multiple packages on the same branch. It integrates with the Zed editor, Pyright, and a `bwrap`-based sandbox for running LLM agents.

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
- **pyright**: `pyrightconfig.json` is for human IDE use only and is **not** canonical. MyPy lacks a good LSP, so the pyright config exists solely to roughly approximate MyPy's behavior in editors. Do not treat pyright as a source of truth.

## File layout

| File                  | Role                                                                                                                                                                                            |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tkt/__init__.py`     | Public API exports: `cli`, `Environment`, `Workspace`.                                                                                                                                          |
| `tkt/_cli.py`         | Click-based CLI commands: `new`, `update`, `upgrade-metapackage`, `rm`, `agent-run`.                                                                                                            |
| `tkt/_environment.py` | Abstract base classes `Environment` and `Tool`. `Environment` is subclassed per observatory (e.g. `RubinEnvironment`); `Tool` is subclassed per integration (e.g. `Zed`, `Pyright`, `Sandbox`). |
| `tkt/_workspace.py`   | `Workspace` class: manages the git/EUPS workspace lifecycle (create, update, upgrade metapackage, remove).                                                                                      |
| `tkt/rubin.py`        | `RubinEnvironment`: LSST DM-specific `Environment` subclass. Reads `repos.yaml` for package origins.                                                                                            |
| `tkt/sandbox.py`      | `Sandbox` tool: runs an LLM agent inside a `bwrap` sandbox with a read-only view of the human's worktree and a writable git worktree on a separate branch.                                      |
| `tkt/zed.py`          | `Zed` tool: writes Zed editor configuration into the workspace.                                                                                                                                 |
| `tkt/pyright.py`      | `Pyright` tool: writes `pyrightconfig.json` into the workspace.                                                                                                                                 |
| `tkt/precommit.py`    | `PreCommit` tool: installs pre-commit or prek git hooks when configuration files are present in packages.                                                                                       |
| `tkt/utils.py`        | JSON read/write helpers (uses `json5` for reading to allow trailing commas).                                                                                                                    |

## Configuration

- **`local.json`**: User-specific environment configuration (workspace path, `repos.yaml` location, externals, and tool definitions). This file is committed to the repository.
- **`pyproject.toml`**: Ruff and mypy configuration.
- **`pyrightconfig.json`**: Pyright configuration for IDE use (see above).
- **`ups/tkt.table`**: EUPS table file that prepends `bin/` to `PATH` and the package root to `PYTHONPATH`.

## Testing

No tests currently exist. Future tests should use `pytest`.
