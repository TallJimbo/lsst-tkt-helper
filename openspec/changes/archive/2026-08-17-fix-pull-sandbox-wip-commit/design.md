## Context

`tkt pull-sandbox` transfers LLM-agent work from `.agent/<pkg>` worktrees onto
the human-workspace branches. The uncommitted-transfer path
(`Pull._uncommitted_transfer` in `tkt/pull.py`) captures a dirty agent worktree
as a temporary "tkt: WIP" commit, cherry-picks it onto the human branch, then
`git reset --mixed`s the human branch back to expose the work as unstaged
changes.

Two defects arise from that temporary commit:

1. The WIP commit is created with `git commit` and therefore runs the package's
   pre-commit/prek hooks, which can fail (lint errors, network-restricted
   sandbox).
2. The WIP commit is left on the agent branch at its tip after a successful
   transfer (and after `--finish` resolves a conflicted transfer), polluting the
   agent branch's history.

The `--abort` path already handles cleanup correctly: it `git reset --mixed`s
the agent branch back to the pre-WIP tip, removing the WIP and returning the
work to an uncommitted state. This change extends that cleanup to the
immediate-success and `--finish` paths, and skips hooks on the WIP commit.

## Goals / Non-Goals

**Goals:**
- The temporary WIP commit must never be blocked by pre-commit/prek hooks.
- After an uncommitted transfer completes (immediate or via `--finish`), the
  temporary WIP commit is removed from the agent branch, and the agent worktree
  returns to its original uncommitted/untracked state.
- No agent work is lost: the human branch always holds a copy of the transferred
  work, and the agent worktree keeps its uncommitted copy.

**Non-Goals:**
- Changing the divergent/committed path (rebase) behavior.
- Changing `--finish`'s existing decision to abandon an in-progress human-side
  cherry-pick.
- Running hooks on the final human-side result (the result is exposed as
  unstaged work, so there is no human commit to hook).

## Decisions

### Decision 1: Skip hooks on the WIP commit with `--no-verify`

The WIP commit is created with `st.agent_repo.git.commit("-m", _WIP_COMMIT_MESSAGE)`
in `_uncommitted_transfer`. Pass `no_verify=True`, which GitPython translates to
`git commit --no-verify`, bypassing the `pre-commit` hook stage.

Rationale: git commits routinely need a way to bypass hooks for mechanical
commits; `--no-verify` is the standard, self-documenting mechanism. It only skips
the `pre-commit`/`commit-msg` hook stages, and the WIP is temporary.

Why not other steps: verified experimentally that plain `git cherry-pick` and
`git rebase` do **not** run pre-commit hooks (git 2.55), so the WIP commit
creation is the only place in the uncommitted flow that can trip hooks. The
divergent path uses `rebase`, which also does not run pre-commit hooks. The
transferred result on the human side is uncommitted, so no human commit exists to
hook.

### Decision 2: Remove the WIP commit by `git reset --mixed` after the transfer

After the work has landed on the human branch (immediate success), reset the
agent branch back to its pre-WIP tip:

```python
st.agent_repo.git.reset("--mixed", pre_wip_agent)
```

`git reset --mixed` moves the branch tip off the WIP commit and clears the index,
but keeps the working tree intact, so the WIP's changes reappear as ordinary
uncommitted/untracked changes in the agent worktree. Nothing is lost on either
side; the human branch carries the transferred copy and the agent keeps its
original dirty copy. This is exactly the mechanism `--abort` already uses
(`abort()` line 585).

Why `--mixed` and not `--hard`: `--hard` would delete the agent's working-tree
copy of the changes, which depends on the human copy being a correct, complete
transfer. `--mixed` preserves the agent's source copy, matching the user's chosen
behavior (restore dirty state) and the existing `--abort` semantics.

### Decision 3: Guard the `--finish` WIP removal on transfer completion

`Pull._finalize_package` handles `--finish` for a conflicted uncommitted
transfer. Its `pending_uncommitted_finalize` block first aborts any in-progress
rebase/cherry-pick (a deliberate bail-out: if the user gives up resolving, the
agent's WIP commit is the only surviving copy of the work — see the comment
referencing "Decision 5"), then `--mixed` resets the human branch.

Because that block can abandon an in-progress cherry-pick, removing the agent WIP
there unconditionally would destroy the work. So capture whether the cherry-pick
was in progress *before* the abort, and only reset the agent branch to its
pre-WIP tip when it was not:

```python
cp_in_progress = _is_cherry_pick_in_progress(st.human_repo)
if _is_rebase_in_progress(st.human_repo):
    st.human_repo.git.rebase("--abort")
if cp_in_progress:
    st.human_repo.git.cherry_pick("--abort")
st.human_repo.git.reset("--mixed", state.pending_uncommitted_finalize)
if state.agent_pre_wip and not cp_in_progress:
    st.agent_repo.git.reset("--mixed", state.agent_pre_wip)
```

Rationale: when the user resolved and ran `git cherry-pick --continue`, the human
branch now holds the work (later un-staged by the mixed reset), so dropping the
WIP is safe. When `--finish` runs mid-cherry-pick and abandons it, the WIP must
survive.

### Decision 4: No new ledger fields

The immediate-success path returns without writing a ledger entry. The `--finish`
path already has `state.agent_pre_wip` recorded for exactly this purpose (it is
set when the conflicted transfer is recorded, and `--abort` already consumes it).
So no `_State`/ledger schema change is needed.

## Risks / Trade-offs

- [Dropping the WIP on `--finish` could lose work if the guard is wrong] → The
  guard keys on whether the human-side cherry-pick was in progress before the
  bail-out; when it was, the WIP is retained. A dedicated scenario captures this
  case.
- [The immediate-success `--mixed` reset leaves the agent worktree dirty again] →
  This is intentional (restore dirty state) and matches `--abort`. The sandbox
  reset offer is not emitted on the immediate-success path, so the dirty agent
  state persists for the next agent run.

## Open Questions

- None.
