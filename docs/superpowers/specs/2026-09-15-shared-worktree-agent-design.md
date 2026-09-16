# Shared-worktree agent mode — design

Date: 2026-09-15
Status: approved in chat (Gate 1)

## Problem

`tkt` workspaces normally isolate the LLM agent: `Sandbox.write()` creates
`.agent/<pkg>` git worktrees on `<branch>-agent` branches, the bwrap mounts
keep the human's worktree read-only, and `tkt pull-sandbox` transfers agent
work onto the human branches afterward. Some users want the agent to work
*directly* on the same worktrees and branches as the human — single-repo
sandbox semantics, but for a multi-package tkt workspace — skipping the
`.agent` worktrees and the pull step entirely.

## Design

The mode is a per-workspace boolean, `shared_worktree`, chosen at
`tkt new` time and persisted in `tkt.json`.

### Persistence

- `Workspace` gains a `shared_worktree: bool` constructor kwarg / property,
  written to `tkt.json` as `"shared_worktree"`. `from_directory` reads
  `data.get("shared_worktree", False)` so pre-existing workspaces are
  unaffected.
- `Workspace.new(..., shared_worktree: bool = False)` threads it through.
- `tkt new` gains `--shared-worktree / --no-shared-worktree` (default
  `None` = defer to the environment default).
- `Environment` gains a **concrete** method
  `default_shared_worktree(self) -> bool` returning `False` (concrete so
  third-party subclasses don't break). `RubinEnvironment.from_json_data`
  reads `data.get("shared_worktree", False)` from `local.json`.

### Sandbox.write()

When `workspace.shared_worktree` is `True`:

- **No `.agent/` directory at all**: no per-package worktrees (no `-agent`
  branches), no `.agent/ups` copy, and `Sandbox.write()` creates nothing
  else under the workspace besides the rendered `AGENTS.md`.
- The EUPS setup instead uses the human's `<workspace>/ups/` (see below).

### Superpowers docs worktree

`Superpowers.write()` places the docs worktree at `<workspace>/superpowers-docs`
(workspace root) in shared mode, instead of `<workspace>/.agent/superpowers-docs`
in worktree mode. Same ticket branch, same behavior otherwise.
`Superpowers.remove()` deregisters either location (handles legacy `.agent`
placements).

### EUPS setup (`setup -r`)

In worktree mode the sandbox inner script (`_build_inner_script`) and the
`WarmSandbox` driver (`mcp_server.py`) run `setup -r .agent` (the copied
metapackage). In shared mode they instead run
`setup -r <workspace directory>` — the human's own
`ups/<workspace_eups_product>.table` (e.g. `ups/tkt_workspace.table`), which
is read-only-mounted but perfectly setup-able.

### Mounts (`_workspace_mounts`)

Shared mode bind-mounts the **entire workspace read-write as a single
mount** (human decision; an earlier design kept the workspace root read-only
and rw-bound only the package dirs and `superpowers-docs`):

- workspace root (covering package dirs, `superpowers-docs/`, `ups/`,
  `tkt.json`, generated `AGENTS.md`): one rw `--bind`;
- no `.agent/` bind (the directory is not created in shared mode);
- externals: read-only (unchanged).

`run()` and `warm_holder_argv()` inherit this via `_workspace_mounts`.

### sandbox-run / mcp-server mode autodetect

Workspace mode is detected by `cwd/.agent` being a directory **or**
`cwd/tkt.json` being a file (single-repo mode otherwise). Extracted as
`_workspace_mode(cwd)` in `_cli.py` for testability.

### pull-sandbox / sandbox-reset guards

Both commands (including `pull-sandbox --finish/--abort`) call a shared
`_require_agent_worktrees(workspace)` helper in `_cli.py` that raises
`click.UsageError` explaining the workspace is in shared-worktree mode.
`tkt rm` / `tkt rm-package` need no changes (their `.agent/<pkg>` cleanup
and dirty-work warnings are already no-ops when absent).

### AGENTS.md templating

`tkt/AGENTS.md.in` becomes a real template rendered by
`sandbox.render_agents_md(template, *, shared, vc_port)`:

- `<!-- BEGIN shared -->` … `<!-- END shared -->` and
  `<!-- BEGIN worktree -->` … `<!-- END worktree -->` blocks are kept when
  the mode matches (markers stripped) and dropped otherwise; mismatched or
  nested blocks raise `ValueError`.
- `{{vc_port}}` is substituted (fixes the previously hardcoded `8081`).
- Mode-dependent sections: repository layout bullets, "Finding
  Repositories", superpowers "make ALL code changes in `.agent/**`"
  sentence + scratch path, workflow steps 1–2 working dir.
- Shared-only guidance: work happens on the branch shared with the human;
  never rewrite history the human may have built on (no `--amend`/rebase of
  shared commits); check `git status` first since the human edits the same
  worktree in parallel.

## Decisions

- Mode stored in `tkt.json`; `local.json` provides only the `tkt new`
  default (human decision).
- pull/reset hard-error in shared mode (human decision).
- ~~Workspace root stays read-only even in shared mode~~ superseded: the
  whole workspace is rw-mounted in shared mode (human decision); the agent
  shares the human's files, so protecting `tkt.json` / `AGENTS.md` from it
  would be inconsistent.
- One templated `AGENTS.md.in` with mode-conditional blocks instead of a
  duplicated shared template (human decision); markers are HTML comments so
  the raw template stays valid markdown.
- Name: `shared_worktree` / `--shared-worktree`.

## Testing

`tests/test_sandbox.py` extensions: `render_agents_md` (block selection,
marker stripping, `ValueError` on mismatched/nested blocks, `{{vc_port}}`,
real template renders clean in both modes); shared-mode `write()` creates no
`.agent` directory at all; shared-mode mount list (rw packages, rw
`superpowers-docs`, no `.agent` bind); inner-script setup line (`setup -r
.agent` vs `setup -r <workspace>`); guard helper raises; `_workspace_mode`
detection; `tkt.json` round-trip of `shared_worktree` (missing key ⇒ False);
`RubinEnvironment` config default. `tests/test_superpowers.py`: docs worktree
at workspace root in shared mode; `remove` handles both locations.
`tests/test_mcp_server.py`: warm-holder setup lines per mode.
