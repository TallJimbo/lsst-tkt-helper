# sandbox-reset

## Context

`tkt` creates, for each package, an agent worktree at `<workspace>/.agent/<pkg>`
on a per-package branch `<human-branch>-agent` (see `Sandbox.write` /
`_add_agent_worktree` in `tkt/sandbox.py`). The human workspace checkout for the
same package lives at `<workspace>/<pkg>` on `<human-branch>` (e.g.
`tickets/<ticket>` for Rubin). The agent branch and the human branch coexist in
the same git repo; the agent branch is derived from the human branch by appending
`-agent`.

The only existing way to discard an agent's work is `tkt rm`, which removes the
entire workspace. There is no way to reset `.agent` worktrees in place. This
change adds `Sandbox.reset(workspace)` and a `tkt sandbox-reset` command that
restores each `.agent/<pkg>` worktree to its human branch, saving work first.

Constraints from the codebase:
- `Workspace.from_existing(ticket, directory, environment)` resolves a workspace
  from `tkt.json`; `workspace.packages` is a `dict[str, str]` of
  package → human branch (`tkt/_workspace.py`).
- `tkt sandbox-run` (`tkt/_cli.py`) is the template for resolving the
  environment and the `sandbox` tool.
- The stash is stored in the shared git dir of each package repo, so it is
  visible across that repo's worktrees (human + agent).

## Goals / Non-Goals

**Goals:**
- A first-class `tkt sandbox-reset` command that resets every `.agent/<pkg>`
  worktree to its human branch.
- Never silently lose agent work: uncommitted changes go to the stash,
  unmerged commits go to a uniquely-named backup branch.
- Reset in place; the worktree, agent branch name, and bwrap mounts stay intact.

**Non-Goals:**
- Re-adding or recreating worktrees (that's `tkt update` / `tkt new`).
- Merging, pushing, or otherwise integrating saved work back into the human branch.
- Any cleanup/GC of accumulated stash entries or backup branches.
- A dry-run flag (explicitly out of scope per the requester).

## Decisions

### Decision: Reset lives on the `Sandbox` tool as `reset(workspace)`

Add `Sandbox.reset(workspace)` in `tkt/sandbox.py`, next to `run` /
`_add_agent_worktree`. This keeps all worktree/branch logic in one module. The CLI
command `tkt sandbox-reset` mirrors `sandbox-run`:

```python
env = Environment.from_file(environment)
workspace = Workspace.from_existing(ticket=ticket, directory=directory, environment=env)
tool = env.get_tool("sandbox")           # validate isinstance(Sandbox)
tool.reset(workspace)
```

**Alternatives considered:** a standalone script (rejected — the user wants a
first-class command); a `reset` subcommand of `tkt update` (rejected — reset is
an independent operation, not part of update).

### Decision: Derive the human branch from `workspace.packages`

For each package `p` in `workspace.packages`, the human branch is
`workspace.packages[p]`, and the agent worktree to reset is
`<workspace.directory>/.agent/<p>`. This uses the authoritative `tkt.json` data
rather than stripping the `-agent` suffix from the agent branch name. The human
branch is the reset target; the agent branch name is only needed to construct the
backup branch name (via the `-agent` suffix convention).

**Alternatives considered:** stripping `-agent` from the active branch of each
`.agent/<pkg>` worktree (works, but relies on the naming convention and doesn't
use the source of truth). Prefer `workspace.packages`.

### Decision: Stash uncommitted work with `--all`

For a dirty worktree, run `git stash push --all -m "tkt reset backup: <pkg>"`.
`--all` (not `-u`) also captures **ignored** files, which `git clean -fdx`
would otherwise delete. Since each package is its own git repo, the stash stacks are
independent per package. The stash is a stack, so repeated resets accumulate
entries rather than clobbering each other.

Detection: the worktree is dirty if there are staged/unstaged changes
(`git diff --quiet` and `git diff --cached --quiet` both non-zero) or untracked
files (`git ls-files --others --exclude-standard` non-empty) or ignored files.
GitPython: `repo.is_dirty(untracked_files=True)` plus a check for ignored files.

### Decision: Backup unmerged commits on a timestamped branch

If `git rev-list <human-branch>..<agent-HEAD>` is non-empty (the agent has
commits not reachable from the human branch), create
`git branch <human>-agent-saved-<timestamp> <agent-HEAD>` where `timestamp` is
`datetime.now().strftime("%Y%m%dT%H%M%S")` (second precision). The timestamp
makes each reset's backup branch uniquely named, so the most recent reset does not
clobber earlier ones. Local-branch clutter is acceptable to the requester.

**Alternatives considered:** a single force-updated `-saved` branch (simpler, but
clobbers earlier saves — rejected); an increment counter (requires scanning existing
branches to find the next number — rejected in favor of the self-describing
timestamp).

### Decision: Order of operations per worktree

1. Stash uncommitted work (if dirty).
2. Create backup branch (if there are unmerged commits).
3. `git reset --hard <human-branch>`.
4. `git clean -fdx`.

Saving before resetting ensures nothing is lost. `clean -fdx` is mostly
defensive after a `--all` stash, but covers files that appear between detection and
clean.

## Risks / Trade-offs

- [Timestamp collision] → Two resets of the same package within the same second
  would produce the same branch name and `git branch` would fail on the second
  (non-force). Acceptable given manual invocation; if it becomes a problem, add a
  collision suffix or use higher precision.
- [Stash `--all` captures build artifacts] → The stash is meant to be a safety net,
  not a permanent home; the developer pops what they want. No action.
- [Backup branches/stash accumulate] → Known and accepted by the requester (short-
  lived clones, local clutter is fine). No GC.
- [Untracked files already gone from stash pop after reset] → Popping a stash that
  was created against the old agent HEAD onto the reset branch may conflict; the
  developer can pop onto a branch or use `git stash apply --index` against the
  backup branch. Documented behavior; not in scope.

## Migration Plan

N/A — new additive command; existing `sandbox-run`, `new`, `update`, `rm`
behavior is unchanged. No rollback needed beyond not using the new command.

## Open Questions

- None. Timestamp format is `%Y%m%dT%H%M%S` (second precision, `T` as the
  date/time separator).
