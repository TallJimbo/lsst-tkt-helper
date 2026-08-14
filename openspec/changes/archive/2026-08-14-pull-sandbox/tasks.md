## 1. Module scaffolding and CLI

- [x] 1.1 Create a new `tkt/pull.py` module (with the BSD license header) containing the `pull-sandbox` logic, mirroring the structure of `tkt/sandbox.py`; export a `PullSandbox`-style helper and any internal constants (`SYNC_SUFFIX = "-sync"`, WIP commit message, ledger filename).
- [x] 1.2 Add a `pull-sandbox` command to `tkt/_cli.py` with `-s/--skip-uncommitted` and `-o/--only-uncommitted` side-selection, `-f/--finish`, `-a/--abort`, `-n/--dry-run`, and `-v/--verbose` flags; wire it to the existing `Workspace.from_existing` discovery and `_setup_logging`.
- [x] 1.3 Add mutual-exclusion and usage-error validation: `-s`/`-o` are mutually exclusive with each other and with `-f`/`-a`; `-f`/`-a` cannot combine ambiguously.

## 2. Per-package branch classification

- [x] 2.1 Implement per-package state inspection: resolve the agent worktree at `<workspace>/.agent/<pkg>`, the agent branch (`<human-branch>-agent`), and compute `H=human` tip, `A=agent` tip, `merge-base`, `behind = rev-list(H..A)`, `ahead = rev-list(A..H)`, `H_dirty`, and `A_dirty` using GitPython.
- [x] 2.2 Implement the classification into: `skip` (agent contained in human), `fast` (`H` ancestor of `A`, clean agent), `diverged` (both `ahead` and `behind` non-empty), and `uncommitted` (agent dirty with no commits ahead), plus the `mixed`/`both-dirty` error states.

## 3. Preflight guards

- [x] 3.1 Implement the global preflight: across all packages, abort the whole run (before mutating anything) if any package has both human and agent dirty state, matching the "abort immediately" requirement.
- [x] 3.2 Implement mixed-state handling: `-s/--skip-uncommitted` (committed only, mark `deferred_uncommitted`), `-o/--only-uncommitted` (uncommitted only), and an error on a mixed package when no side flag is given asking the user to pick.
- [x] 3.3 Implement the overridable ordering guard: `--only-uncommitted` on a package whose agent branch has commits ahead prompts "are you sure" (with an override) before proceeding.
- [x] 3.4 Implement refusal to start when a `tickets/*-sync` snapshot branch already exists for a repo that would need the divergent path, referencing `--abort`.
- [x] 3.5 Add `-n/--dry-run` support that reports the per-package classification and intended actions without making any git changes.

## 4. Fast path

- [x] 4.1 Implement the fast path: `git merge --ff-only <agent>` in the human worktree for packages where `H` is an ancestor of `A`.
- [x] 4.2 Handle a dirty human worktree on the fast path by relaying git's refusal message when the agent changed files the human has edited locally.

## 5. Divergent interactive rebase (snapshot)

- [x] 5.1 Implement snapshot setup: create `<human-branch>-sync` at the human tip, stash human dirty state with `git stash push -u`, then `git reset --hard <agent>` in the human worktree.
- [x] 5.2 Implement `git rebase -i <human-branch>-sync` in the human worktree so the user can drop variant agent commits in `$EDITOR`.
- [x] 5.3 Implement rollback on success: after the rebase completes, delete the `-sync` branch and (via finish) restore the human stash.

## 6. Uncommitted agent work

- [x] 6.1 Implement the temporary-WIP step: in the agent worktree, `git add -A` and create a WIP commit on the agent branch.
- [x] 6.2 Implement the ancestry-independent transfer of the WIP onto the human branch via `git cherry-pick` of the WIP commit (or `git diff-tree ... | git apply --3way`) in the human worktree, so it works whether or not the human branch is an ancestor of the agent base; leave genuine line conflicts in progress (finalized by `--finish`) rather than doing a whole-branch merge.
- [x] 6.3 Implement the final `git reset --mixed <pre-WIP-human>` that exposes the agent work as unstaged changes (including untracked files).

## 7. Workspace ledger

- [x] 7.1 Implement a workspace-side ledger (e.g. an entry recorded per repo in the workspace) storing `{snapshot_branch, human_stash_ref, sync_kind, pending_uncommitted_finalize, deferred_uncommitted}` per package.
- [x] 7.2 Implement ledger write on start, update as operations progress, and clear on finish/abort.
- [x] 7.3 Make `--finish`/`--abort` with an empty ledger print a clear "nothing to do" message and exit non-zero.

## 8. Finish and abort lifecycle

- [x] 8.1 Implement `--finish`: delete `-sync` branches, pop human stashes (leaving a stash and warning on conflict), and perform the final mixed reset for pending uncommitted transfers.
- [x] 8.2 Implement `--abort`: for each in-progress repo run `git rebase --abort` and/or `git cherry-pick --abort` as appropriate first, then `git reset --hard <sync>`, delete the `-sync` branches, restore stashes, and clear the ledger.
- [x] 8.3 After `--finish`, prompt to run `tkt sandbox-reset` on each affected agent worktree iff it would not be a no-op AND the package has no deferred/pending uncommitted work (otherwise suppress the offer so it cannot destroy work meant for a follow-up `--only-uncommitted`).

## 9. Tests, lint, and docs

- [x] 9.1 Add pytest coverage for classification, fast path, divergent snapshot/rebase, the ancestry-independent uncommitted transfer, `-s`/`-o` side-selection on mixed packages, the overridable ordering guard, preflight aborts/refusals, and finish/abort cleanup (mirroring the approach in `tests/test_sandbox.py`), covering the spec scenarios.
- [x] 9.2 Run `ruff check .`, `ruff format --check .`, and `mypy tkt/` and fix any violations.
- [x] 9.3 Update `AGENTS.md` / command help text to document `tkt pull-sandbox` with its flags and lifecycle.
