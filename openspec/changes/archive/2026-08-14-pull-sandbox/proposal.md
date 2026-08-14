## Why

After an LLM agent works in a workspace's `.agent/<pkg>` worktrees, there is no
convenient way to transfer that work onto the human branch. Today the only
escape is `tkt sandbox-reset`, which discards agent work (after backing it up).
We want a `tkt pull-sandbox` command that brings agent work -- committed or
uncommitted -- onto the human branch, with a safe rollback net and the ability
to skip agent-side commits that duplicate the human's own intentional
modifications.

## What Changes

- Add a new `tkt pull-sandbox` command that transfers agent work from
  `.agent/<pkg>` worktrees onto the corresponding human-workspace branches,
  iterating over every package in the workspace.
- Committed agent work is classified by the branch relationship between the
  human branch `H` and agent branch `A`:
  - agent entirely contained in human (`A` reachable from `H`): nothing to do;
  - human is an ancestor of agent: plain fast-forward (no conflicts possible);
  - diverged (human has commits the agent does not): an **interactive rebase**
    that replays the agent's commits onto the human branch, letting the user
    drop agent-side commits in `$EDITOR`.
- Uncommitted agent work (dirty `.agent/<pkg>` worktree) is brought over as
  ordinary unstaged changes via an ancestry-independent transfer: a temporary
  WIP commit is created, applied onto the human branch (cherry-pick / 3-way),
  then exposed with `git reset --mixed`.
- A package that mixes committed agent commits with a dirty agent worktree is
  split into two single-purpose syncs: `-s/--skip-uncommitted` (committed only,
  deferring the dirty worktree) then `-o/--only-uncommitted` (uncommitted
  only). `--only-uncommitted` before the committed side is reconciled triggers
  an overridable "are you sure" prompt, since a sync-1 resolution may have
  dropped or squashed agent commits.
- Conflict handling leaves operations **in progress** for the user to resolve
  with their normal git tools, rather than an auto-resolving wrapper; the
  command only starts operations or aborts them.
- Resumable lifecycle via `--finish` / `--abort` (cleanup of snapshot/stash
  bookkeeping, and cancellation across all packages); the `sandbox-reset` offer
  on finish is suppressed while a package still has deferred/pending uncommitted
  work.
- A global preflight aborts the run immediately (before mutating anything) if
  any package has both human and agent dirty state.

## Capabilities

### New Capabilities
- `pull-sandbox`: Transfers work from `.agent/<pkg>` worktrees to human-workspace branches, covering committed and uncommitted agent work, interactive variant skipping, conflict handling, and resumable finish/abort cleanup.

### Modified Capabilities
<!-- None: this is a new command with no changes to existing specs -->
- (none)

## Impact

- **Code**: new `tkt pull-sandbox` CLI command in `tkt/_cli.py` with
  `--skip-uncommitted`/`--only-uncommitted` side-selection and
  `--finish`/`--abort` lifecycle flags; new logic (likely a new module, e.g.
  `tkt/pull.py`) for branch classification, the interactive rebase, the
  ancestry-independent uncommitted transfer, and the global ledger;
  workspace-ledger bookkeeping for finish/abort.
- **Reuse**: existing `Workspace` discovery, `Sandbox.reset` and the agent-branch naming convention (`<human>-agent`) from `tkt/sandbox.py`.
- **Interactions**: complements `tkt sandbox-reset`; `--finish` offers to run `sandbox-reset` on agent worktrees when that would not be a no-op.
- **No breaking changes** to existing commands or configuration.
