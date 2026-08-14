## Context

`tkt` creates EUPS metapackage workspaces for Jira tickets. Each package in the
workspace is a git clone with two linked worktrees: the human worktree
`<workspace>/<pkg>/` on the ticket branch (e.g. `tickets/DM-X`) and the agent
worktree `<workspace>/.agent/<pkg>/` on `<ticket-branch>-agent`, used by an LLM
agent inside a `bwrap` sandbox (see `tkt/sandbox.py`). Both worktrees share the
same git object store per package.

The only existing way to move agent work back is `tkt sandbox-reset`, which
discards it (after backing it up). There is no way to bring agent work onto the
human branch. `tkt pull-sandbox` fills that gap: it transfers committed and/or
uncommitted agent work onto the human branch, offering a safe rollback net and
the ability to skip agent-side commits that duplicate the human's own edits.

Design philosophy (from AGENTS.md): prefer simple solutions; the tool's users
are developers who can handle git states and failure modes. In particular,
conflict resolution is deliberately **not** wrapped/automated -- `pull-sandbox`
only starts transfer operations or aborts them, leaving any in-progress
merge/rebase for the user to finish with their usual tools.

## Goals / Non-Goals

**Goals:**
- New `tkt pull-sandbox` command operating over every package in the workspace.
- Bring committed agent work onto the human branch: fast-forward when safe,
  interactive rebase (able to drop agent commits) when diverged.
- Bring uncommitted agent work onto the human branch as ordinary unstaged
  changes, via an ancestry-independent transfer.
- Split a mixed package (commits + dirty agent) into two single-purpose syncs
  with `--skip-uncommitted` and `--only-uncommitted`.
- A global, resumable lifecycle: `--finish` cleanup and `--abort` cancellation.
- Fast-fail guards against ambiguous states (both sides dirty; leftover
  snapshot state).

**Non-Goals:**
- No automated/looped conflict resolution; the tool does not resolve rebases.
- No detection of `-agent-saved-<ts>` backup branches created by prior resets.
- No pushing to remotes or interaction with upstream/PR tooling.
- No change to `sandbox-reset` or other existing commands.

## Decisions

### Decision 1: Command shape and flags

`tkt pull-sandbox` runs the transfer by default. Side-selection flags split a
mixed package (agent commits ahead AND dirty agent worktree) into two
single-purpose syncs:
- `-s/--skip-uncommitted` -- transfer committed work only; defer the agent's
  dirty worktree (mark `deferred_uncommitted` in the ledger).
- `-o/--only-uncommitted` -- transfer uncommitted work only; the committed side
  is assumed already reconciled (see Decision 6/7).

Resumable lifecycle flags: `-f/--finish` (finalize an in-progress sync: delete
snapshots, pop stashes, run the final mixed reset for uncommitted work, then
offer `sandbox-reset`), `-a/--abort` (cancel globally: rebase/cherry-pick abort,
reset back to snapshot, delete snapshot branches, restore stashes, clear
ledger). Also `-n/--dry-run` and `-v/--verbose` for parity with other commands.
`-s`/`-o` are mutually exclusive with each other and with `-f`/`-a`.

Note: the earlier draft's "Offer `sandbox-reset` on finish" is suppressed while
a package still has deferred/pending uncommitted work (see Decision 5), so the
offered reset cannot destroy the work meant for a follow-up sync.

Rationale: `--finish` (not `--continue`) avoids confusion with git's own
`git rebase --continue`, which the user runs manually mid-rebase; tkt's
`--finish` is purely bookkeeping/cleanup. Alternate considered: `--continue`.

### Decision 2: Branch-state classification per package

For each package, with `H` = human branch tip and `A` = agent branch tip:

```
behind = rev-list(H..A) non-empty   # agent has commits human lacks
ahead  = rev-list(A..H) non-empty   # human has commits agent lacks

behind empty          → agent ⊆ human → skip/notify (nothing to transfer)
ahead empty AND
  H ancestor of A     → FAST path: git merge --ff-only A   (no conflicts possible)
behind non-empty AND
  ahead non-empty     → DIVERGED: interactive rebase (snapshot + rebase -i)
```

Rationale: this subsumes the earlier "merge --no-ff default, rebase when human
ahead" idea. The `--no-ff` merge commit was really just asserting `H ∈ A`; we
replace it with an explicit ancestor check and a plain fast-forward, aborting
into the diverged path when the assertion fails.

### Decision 3: Fast path

When `H` is an ancestor of `A` (and the agent worktree is clean), the transfer
is `git merge --ff-only A` in the human worktree. No conflicts are possible.
A dirty human worktree is respected: git refuses fast-forward only if the agent
changed files the human has edited locally; otherwise the fast-forward proceeds
and local edits are preserved.

### Decision 4: Diverged path -- interactive rebase with snapshot (#'sync')

To replay agent commits onto the human branch while letting the user drop
variant agent commits in `$EDITOR`, without needing further action afterward:

1. Snapshot the human state: `git branch <human>-sync <H>` (where `<human>` is
   the ticket branch, e.g. `tickets/DM-X`). Record it in the ledger.
2. Stash human dirty state (if any) with `git stash push -u` (not `--all`, to
   avoid sweeping ignored/build artifacts). Record the stash ref in the ledger.
3. In the human worktree: `git reset --hard <A>` -- the human branch now
   contains the agent commits.
4. `git rebase -i <human>-sync` -- replays `sync..HEAD` (the agent commits)
   onto the original human history. The user drops variant commits via
   `$EDITOR`; git's patch-id detection also auto-skips commits already present
   in the human history.

Because the human branch is the one being rebased, the result lands on the
human branch; no further action is needed after the rebase completes.

Rationale: making the *human* branch the rebased branch (via the temporary
snapshot + `reset --hard`) is what lets the user interactively drop agent-side
commits while the outcome is directly the human branch. Alternatives
considered: rebase the agent branch onto human then fast-forward human (leaves
the human branch not-the-result, requiring a follow-up); a plain `merge --no-ff`
(no way to skip individual agent commits).

### Decision 5: Abort and finish for the diverged path

**Abort** (canonical sequence -- note the mid-operation subtleties):
```
git rebase --abort        # if a rebase-merge/-rebase is in progress
git cherry-pick --abort   # if a cherry-pick is in progress
git reset --hard <sync>   # back to original human H
git branch -D <sync>      # drop snapshot
restore human stash
```

A bare `reset --hard <sync>` while a `rebase-merge` or `CHERRY_PICK_HEAD` state
is in progress does **not** clear git's "still in the middle of an operation"
state, so `--abort` must run the corresponding `--abort` subcommand first. This
is why `--abort` always issues `rebase --abort` and `cherry-pick --abort`
whenever either is detected to be in progress.

**Finish** (after the user completes the rebase/cherry-pick, possibly via
several `git rebase --continue`):
- delete `-sync` snapshot branch;
- pop the human stash back onto the resulting human branch; if the pop
  conflicts, leave the stash entry and warn rather than destroying anything;
- (for the uncommitted path) run the final `git reset --mixed` to expose work
  as unstaged;
- prompt to run `sandbox-reset` on the agent worktree iff it would not be a
  no-op **and** the package has no deferred/pending uncommitted work (a reset
  would otherwise destroy work meant for a follow-up `--only-uncommitted`
  sync).

### Decision 6: Uncommitted agent work (ancestry-independent)

Applies when transferring the agent's dirty worktree, which `--only-uncommitted`
(`-o`) selects explicitly or which the default run handles when the agent has
no commits ahead of the human branch:

1. Temporary commit: in `.agent/<pkg>`, `git add -A` + `git commit -m "tkt: WIP"`
   (a temp commit on the agent branch).
2. Bring the WIP content onto the human branch by **`git cherry-pick`** of the
   WIP commit in the human worktree (or equivalently
   `git diff-tree --full-index -p A <WIP> | git apply --3way`). Git computes the
   `A -> <WIP>` delta and applies it onto the human tip via 3-way, using the
   shared object store. This is **ancestry-independent**: it works whether the
   human branch is an ancestor of the agent base or has diverged (e.g. after a
   sync-1 interactive rebase, where human `H'` contains `A` in content but is
   not an ancestor of it).
3. `git reset --mixed <pre-WIP human>` -- the WIP content stays in the working
   tree but the index resets to the pre-WIP commit, so the agent's edits land
   as ordinary **unstaged** changes (untracked files included).

Rationale: capturing dirty state as a temporary commit is robust for untracked
files and binaries, unlike a raw patch transfer. The earlier draft used
`git merge --ff-only` and fell back to a whole-branch merge in the diverged
case (human ahead + dirty agent). Cherry-pick/`--3way` replaces that fallback
with a delta transfer that is ergonomic in both cases: in the common pure
uncommitted case (human is an ancestor) it applies cleanly like a fast-forward;
after a diverged sync-1 it applies the uncommitted delta onto the reconciled
human without an unrelated whole-branch merge. Only genuine line conflicts
remain in-progress (finalized by `--finish`). Exposing via `reset --mixed` (not
`--soft`) leaves the work unstaged, as chosen by the user.

### Decision 7: Global preflight guards

Before mutating anything, `pull-sandbox` preflights all packages and aborts the
whole run (mutating nothing) if:
- any package has **both** human dirty state and agent dirty state; or
- for a package where a transfer would need the divergent path, a
  `tickets/*-sync` snapshot branch already exists (leftover state) -- tell the
  user to `--abort` or clean manually.

A **mixed** package (agent commits ahead of human AND a dirty agent worktree)
is no longer automatically refused. Instead the user selects which side to
transfer, keeping committed and uncommitted paths separate:
- `-s/--skip-uncommitted` transfers the committed side and marks the uncommitted
  work deferred (recorded in the ledger);
- `-o/--only-uncommitted` transfers only the uncommitted side, assuming the
  committed side is already reconciled;
- without a side flag, a mixed package errors asking the user to pass one of the
  two (fail-fast, no guessing).

**Ordering guard (overridable):** `--only-uncommitted` on a package whose agent
branch still has commits ahead of the human branch is unusual -- it normally
means sync-1 has not run. But a sync-1 resolution may have *dropped* or
*squashed* agent commits, so the agent branch can still appear ahead even
though the work is already on the human branch. Rather than hard-refusing, the
tool prompts "are you sure you want to apply uncommitted changes based on an
agent branch that is commits ahead of the human branch?" and proceeds on
confirmation (or `--yes`-style override), because in the drop/squash case this
is exactly what the user wants.

Rationale: keeping the committed and uncommitted paths separate makes failure
modes obvious, consistent with the "abort fast, keep it simple" philosophy --
but the escape hatch (two single-purpose syncs) is surfaced via explicit flags
and an overridable confirm rather than an unconditional refusal.

### Decision 8: Workspace ledger for resumability

`--finish`/`--abort` are workspace-global and must know what a prior run
created. A small workspace-side ledger file records, per repo/package:
`{snapshot_branch, human_stash_ref, sync_kind, pending_uncommitted_finalize,
deferred_uncommitted}`. `pending_uncommitted_finalize` marks a transfer whose
final `reset --mixed` is still owed; `deferred_uncommitted` marks work skipped
by `--skip-uncommitted` that still lives in the agent worktree (supressing the
`sandbox-reset` offer and signaling that `--only-uncommitted` is the intended
follow-up). The ledger is written when operations start, updated as they
progress, and cleared on `--finish`/`--abort`. Without it we cannot distinguish
a `-sync` branch belonging to `pull-sandbox` from one the user made by hand,
nor locate the human stashes.

### Decision 9: Failure fast on lifecycle with empty ledger

`--finish`/`--abort` with an empty ledger print a clear "nothing to do" message
and exit non-zero rather than guessing.

## Risks / Trade-offs

- [Mid-rebase `--abort` returning to the wrong commit] -> Always run
  `git rebase --abort` before `reset --hard <sync>` (Decision 5).
- [Stash pop conflicts on finish] -> Leave the stash entry and warn; never
  destroy it.
- [Leftover `-sync` branches after a crash] -> Preflight refuses to start when
  a snapshot branch already exists; user runs `--abort` or cleans manually.
- [Mixed committed+uncommitted agent work is frequent in practice] -> Split
  into two single-purpose syncs via `-s/--skip-uncommitted` then
  `-o/--only-uncommitted`; with no side flag a mixed package errors asking the
  user to pick (fail-fast, no guessing).
- [`--only-uncommitted` run before a committed sync, or after a sync-1 that
  dropped/squashed agent commits] -> Overridable "are you sure" confirm prompt
  (Decision 7) so the legitimate drop/squash case is not blocked.
- [Ignored files swept into a human stash] -> Use `git stash push -u` (not
  `--all`) for the human worktree.
- [Multiple packages each left in-progress] -> Managed independently via the
  ledger; `--finish`/`--abort` clean up globally.

## Migration Plan

New command only; no changes to existing behavior or configuration. Rollback is
trivial (remove the new command/module). No data migration.

## Open Questions

None -- all design decisions and edge cases have been resolved with the user.
