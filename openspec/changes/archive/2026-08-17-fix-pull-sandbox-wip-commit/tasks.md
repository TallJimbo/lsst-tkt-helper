## 1. Skip pre-commit hooks on the temporary WIP commit

- [x] 1.1 In `tkt/pull.py` `Pull._uncommitted_transfer`, add `no_verify=True` to the
      temporary agent WIP commit call (`st.agent_repo.git.commit("-m", _WIP_COMMIT_MESSAGE, no_verify=True)`).

## 2. Remove the WIP commit after an immediate-success transfer

- [x] 2.1 After the human-side `git reset --mixed pre_wip_human` in
      `Pull._uncommitted_transfer`, add a `git reset --mixed pre_wip_agent` on the
      agent repo so the WIP commit is removed and the agent worktree returns to its
      uncommitted/untracked state.

## 3. Remove the WIP commit on `--finish`

- [x] 3.1 In `Pull._finalize_package`, capture whether the human-side cherry-pick is in
      progress at the start of the `pending_uncommitted_finalize` block (before any
      rebase/cherry-pick abort).
- [x] 3.2 After resetting the human branch, reset the agent repo `--mixed` to
      `state.agent_pre_wip`, but only when the cherry-pick was not in progress
      (i.e. the transfer actually completed). Confirm this branch-relevant change does
      not disturb the rebase/stash handling above it.

## 4. Tests

- [x] 4.1 Extend `test_uncommitted_transfer_as_staged_restore`: capture the agent branch
      tip before the run, then assert after the run that the agent branch head is back
      to that pre-WIP tip and `tkt: WIP` no longer appears in the agent `git log`
      (agent worktree is dirty again).
- [x] 4.2 Add a test: a failing pre-commit hook installed in the agent repo does not
      block `Pull.run`; the uncommitted work still lands as unstaged changes on the
      human branch.
- [x] 4.3 Extend `test_uncommitted_conflict_resolved_then_finish`: assert the agent
      branch has no lingering WIP commit after `Pull.finish`.
- [x] 4.4 Add a test: `--finish` run while the human-side cherry-pick is still in
      progress keeps the agent WIP commit (the work is preserved).

## 5. Verification

- [x] 5.1 Run `python -m pytest` and confirm the pull-sandbox tests pass.
- [x] 5.2 Run `ruff check .`, `ruff format --check .`, and `mypy tkt/` and confirm they
      are clean.
