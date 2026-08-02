# sandbox-reset

## Why

The agent's `.agent/<pkg>` worktrees (on per-package `<human>-agent` branches)
accumulate divergent commits, uncommitted changes, and untracked build artifacts.
There is currently no way to restore them to the state of the corresponding
human-workspace branch without deleting the whole workspace via `tkt rm`. A
developer who wants to throw away an agent's work and start fresh has no first-class
command for it.

## What Changes

- Add a new `Sandbox.reset(workspace)` method that, for every package with an
  `.agent/<pkg>` worktree, saves and then discards the agent's work.
- Add a new `tkt sandbox-reset` CLI command that resolves the workspace the same
  way `tkt sandbox-run` does (from `-d/--directory` or `--ticket`), loads the
  `sandbox` tool, and calls `reset`.
- For each `.agent/<pkg>`:
  - Save uncommitted work (staged, unstaged, untracked, and ignored files) to
    the git stash with `git stash push --all` and a descriptive message.
  - If the agent branch has commits not reachable from the human branch, save them
    to a uniquely-named timestamped backup branch
    `<human>-agent-saved-<%Y%m%dT%H%M%>` before resetting.
  - `git reset --hard` the agent branch to the human branch.
  - `git clean -fdx` to remove any remaining untracked/ignored files.

## Capabilities

### New Capabilities

- `sandbox-reset`: Restoring every `.agent` worktree to the state of the
  corresponding human-workspace branch, saving uncommitted work to the stash and
  unmerged agent commits to a timestamped backup branch first.

### Modified Capabilities

<!-- None: existing specs (sandbox-run-command-option, sandbox-run-single-repo)
     are unchanged. -->

## Impact

- `tkt/sandbox.py`: new `reset()` method on `Sandbox`.
- `tkt/_cli.py`: new `sandbox-reset` command using `Environment`,
  `Workspace.from_existing`, and `env.get_tool("sandbox")`.
- `tkt/__init__.py`: `Sandbox.reset` is part of the public API, so it is
  exported like the other `Sandbox` methods.
- No changes to EUPS setup, sandbox bwrap invocation, or workspace lifecycle.
- The stash and backup branches persist after the command so the developer can
  recover saved work; local-branch clutter is acceptable.
