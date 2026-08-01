## Context

`tkt sandbox-run` runs an LLM agent (an OpenCode ACP server by default) inside a
`bwrap` sandbox. Today it is tightly coupled to `Workspace`, which represents a
multi-package EUPS metapackage workspace created by `tkt new`: it requires a
`tkt.json` with a ticket, metapackage, packages, and externals. The sandbox then
creates a per-package git worktree in `.agent/<repo>/` on an `-agent` branch and
leaves the human's main worktree read-only.

Many projects do not have this shape — notably `tkt2` itself: a single git
repository that shares the host's conda environment and has its own `ups/tkt.table`
but no `tkt.json`. We want to run the same sandbox on such a repository, with the
agent writing directly to the main (repo-default) worktree.

### Current data flow

```
CLI sandbox-run
  → Environment.from_file(local.json)      # RubinEnvironment
  → Workspace.from_existing                # needs tkt.json
  → env.get_tool("sandbox")                # Sandbox.from_json_data (command/mounts/env)
  → Sandbox.run(workspace)
      → _build_bwrap_argv(workspace)       # workspace-specific mounts
      → inner: "setup -r ." + exec command, --chdir .agent
```

The single-repo mode does not use `Workspace` at all, and only needs the `sandbox`
tool config (command, mounts, env) plus the repo directory.

## Goals / Non-Goals

**Goals:**

- Autodetect mode from the current working directory (presence/absence of `.agent`).
- Single-repo mode: agent writes to the main worktree (whole repo rw).
- Use the currently active conda env; support a `--conda-env <name>` override.
- Run EUPS `setup -r .` after conda activation, only when `ups/` exists.
- Remove the vestigial `--chdir` to the agent directory.

**Non-Goals:**

- Changing the existing multi-package workspace behavior.
- Support for Jira tickets, metapackages, or EUPS externals in single-repo mode.
- Running on repositories without git.
- Full `Environment`/`Tool` loading machinery for single-repo mode (we only need
  the sandbox config).

## Decisions

### D1: Mode autodetection via `.agent/` presence

The working directory (CWD) is inspected for a `.agent` subdirectory. If present,
the current workspace behavior runs unchanged; otherwise single-repo mode is used
and CWD is treated as the repo root.

**Rationale:** `.agent` is created by `Sandbox.write` only in workspace mode, so
it is a reliable discriminator. It also matches the user's stated preference and
how the ACP server is already used.

**Alternative considered:** checking for `tkt.json`. Rejected because a repo could
contain an unrelated `tkt.json`, and because `.agent` directly signals the sandbox
layout the user cares about.

### D2: Factor config/tool loading out of `Environment`, call without an instance

Single-repo mode does not load a `Workspace` (no ticket/metapackage) and does not
need a full `Environment` instance (a plain repo has no `RubinEnvironment`
configuration). Instead, factor the two reusable pieces out of the construction path
into static/class methods on `Environment` that the CLI can call directly:

```python
class Environment(ABC):
    @staticmethod
    def load_config(f: TextIO) -> tuple[type["Environment"], dict[str, Any]]:
        """Load config JSON and resolve the Environment class, without
        instantiating an Environment."""
        data = json.load(f)
        mod = importlib.import_module(data["module"])
        cls = getattr(mod, data["cls"])
        return cls, data

    @classmethod
    def load_tools(cls, data: dict[str, Any]) -> dict[str, Tool]:
        # body of the existing _read_tools: construct each Tool from data["tools"]
        ...
```

- `Environment.from_file` becomes `load_config` followed by `cls.from_json_data(data)` —
  workspace-mode behavior unchanged.
- `RubinEnvironment.from_json_data` still uses `load_tools` — unchanged.
- Single-repo mode calls `cls, data = Environment.load_config(f)` then
  `sandbox = cls.load_tools(data)["sandbox"]`, without instantiating an
  `Environment`.

The config file is resolved via the existing `TKT_ENVIRONMENT`/`--environment`
mechanism (which may point at `local.json`), not hard-coded. The `--conda-env`
override is applied separately at run time.

**Rationale:** keeps `Environment` instances from carrying conditional/partial state,
avoids a `tools_only` flag threaded through the abstract interface, and reuses the
single existing config/tool-loading path rather than opening a new one. This
matches the user's preference to reuse `Environment`'s machinery.

**Alternatives considered:**
- A `tools_only` flag on `Environment.from_json_data`/`from_file`. Rejected —
  adds conditional state and threads a flag through every subclass's abstract
  signature.
- Reading `local.json` directly in the CLI and calling `Sandbox.from_json_data`
  inline. Rejected — opens a second, parallel config-reading path.
- A new minimal `Environment` subclass. Rejected as unnecessary indirection for the
  single-repo case.

### D3: Whole-repo read-write mount in single-repo mode

In single-repo mode the repo root is bound read-write (`--bind`), with no
separate `.git` handling, because the agent's writable working directory *is* the
main worktree. This differs from workspace mode, where the main worktree is
read-only and only `.git` (plus `.agent`) is writable so the agent works in a
separate worktree.

**Rationale:** the whole point of single-repo mode is that the agent edits the
human's actual working tree, so both the working files and `.git` must be
writable. There is no separate mount for `.git` in this mode (per the user's D3
decision).

### D4: Generalized inner setup script

The inner script becomes:

```
[conda activate <env>]      # only if --conda-env given
[setup -r .]                # only if ups/ exists
exec <command>
```

Conda activation, when requested, sources the conda base's `etc/profile.d/conda.sh`
then runs `conda activate <name>`, and runs **before** EUPS setup so `setup -r .`
sees the correct environment.

**Rationale:** EUPS must run after conda so that `setup`'s `setupRequired`/PATH
operations observe the activated environment. Skipping `setup` when no `ups/`
exists lets pure-conda repos work without error.

### D5: Remove the vestigial `--chdir`

The `--chdir .agent` is removed as part of this change: the ACP server resets the
working directory to the main project directory itself, so the sandbox does not
need to force it. Removing it also makes single-repo mode's CWD (the repo root)
the natural working directory.

## Risks / Trade-offs

- **[Autodetect a `.agent` in a repo that should be single-repo]** → Both `.agent`
  and `tkt.json` presence would indicate workspace mode; users can remove `.agent`
  or rely on the documented CWD-based heuristic. The heuristic is simple and the
  two layouts are unlikely to be confused in practice.
- **[Whole-repo rw mount lets the agent clobber the human's edits]** → This is
  intentional for single-repo mode (the user opted into it); the human and agent
  share a worktree, which is the point.
- **[conda base discovery for the override]** → derive the base from the inherited
  `$CONDA_PREFIX` or `which conda`; documented in the inner-script construction.
- **[Conda env not found on the host]** → the inner `conda activate` fails loudly,
  which is acceptable for a dev tool.
- **[setup -r . on a repo with an ups/ but no EUPS installed]** → already how
  workspace mode behaves; EUPS users are assumed to have it available.

## Migration Plan

No data migration. The change is additive and internal to `tkt`.
