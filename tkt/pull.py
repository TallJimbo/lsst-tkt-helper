# Copyright 2020-2026 Jim Bosch
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

from __future__ import annotations

__all__ = ("Pull", "PullError")

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field

import git

from ._workspace import Workspace
from .sandbox import AGENT_SUBDIR

# Suffix appended to the human ticket branch to form the rollback snapshot
# branch used by the divergent committed path.
SYNC_SUFFIX = "-sync"

# Commit message used for the temporary WIP commit that captures uncommitted
# agent work before it is transferred.
_WIP_COMMIT_MESSAGE = "tkt: WIP"

# Ledger file (workspace-relative) recording in-progress sync state so that
# `--finish`/`--abort` can finalize or cancel a partial transfer.
_LEDGER_FILENAME = ".pull-sandbox.json"


class PullError(Exception):
    """Raised when a ``pull-sandbox`` operation cannot proceed safely."""


@dataclass
class _State:
    """Per-package ledger record written to :data:`_LEDGER_FILENAME`."""

    snapshot_branch: str | None = None
    human_stash_ref: str | None = None
    # One of "fast", "diverged", "uncommitted" describing the committed (or,
    # for "uncommitted", uncommitted) transfer that was started.
    sync_kind: str | None = None
    # Human tip immediately before an in-progress uncommitted transfer; the
    # final `git reset --mixed` on --finish resets to this to unstage the work.
    pending_uncommitted_finalize: str | None = None
    # True when --skip-uncommitted deferred the agent's dirty worktree so a
    # follow-up --only-uncommitted can transfer it. Suppresses the
    # sandbox-reset offer on --finish.
    deferred_uncommitted: bool = False
    # Agent branch tip immediately before the WIP commit of an in-progress
    # uncommitted transfer; ``--abort`` resets the agent worktree back to this
    # so the work returns to an uncommitted state.
    agent_pre_wip: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "snapshot_branch": self.snapshot_branch,
            "human_stash_ref": self.human_stash_ref,
            "sync_kind": self.sync_kind,
            "pending_uncommitted_finalize": self.pending_uncommitted_finalize,
            "deferred_uncommitted": self.deferred_uncommitted,
            "agent_pre_wip": self.agent_pre_wip,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> _State:
        return cls(
            snapshot_branch=_as_opt_str(data.get("snapshot_branch")),
            human_stash_ref=_as_opt_str(data.get("human_stash_ref")),
            sync_kind=_as_opt_str(data.get("sync_kind")),
            pending_uncommitted_finalize=_as_opt_str(data.get("pending_uncommitted_finalize")),
            deferred_uncommitted=bool(data.get("deferred_uncommitted")),
            agent_pre_wip=_as_opt_str(data.get("agent_pre_wip")),
        )


@dataclass
class _Status:
    """Computed git state for one package."""

    human_repo: git.Repo
    human_dir: str
    human_branch: str
    H: str
    agent_dir: str | None = None
    agent_repo: git.Repo | None = None
    agent_branch: str | None = None
    A: str | None = None
    ahead: list[str] = field(default_factory=list)
    behind: list[str] = field(default_factory=list)
    human_dirty: bool = False
    agent_dirty: bool = False

    @property
    def snapshot_branch(self) -> str | None:
        """Name of this package's rollback snapshot branch, if any."""
        return f"{self.human_branch}{SYNC_SUFFIX}"

    def classify(self) -> str:
        """Return the transfer classification for this package."""
        if self.agent_dir is None:
            return "skip"
        if not self.behind:
            # Agent has nothing the human lacks.
            if self.agent_dirty:
                return "uncommitted"
            return "skip"
        # Agent has commits the human lacks.
        if self.agent_dirty:
            return "mixed"
        if self.ahead:
            return "diverged"
        return "fast"


def _as_opt_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _ledger_path(workspace: Workspace) -> str:
    return os.path.join(workspace.directory, _LEDGER_FILENAME)


def _load_ledger(workspace: Workspace) -> dict[str, _State]:
    path = _ledger_path(workspace)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        import json

        data = json.load(f)
    return {pkg: _State.from_dict(entry) for pkg, entry in data.items()}


def _save_ledger(workspace: Workspace, states: dict[str, _State]) -> None:
    import json

    path = _ledger_path(workspace)
    data = {pkg: state.to_dict() for pkg, state in states.items()}
    with open(path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)


def _clear_ledger(workspace: Workspace) -> None:
    path = _ledger_path(workspace)
    if os.path.exists(path):
        os.remove(path)


def _status(workspace: Workspace, package: str) -> _Status:
    """Compute the per-package git state used for classification."""
    human_dir = os.path.join(workspace.directory, package)
    human_repo = git.Repo(human_dir)
    human_branch = workspace.packages[package]
    H = human_repo.head.commit.hexsha
    agent_dir = os.path.join(workspace.directory, AGENT_SUBDIR, package)
    if not os.path.isdir(agent_dir):
        return _Status(human_repo=human_repo, human_dir=human_dir, human_branch=human_branch, H=H)
    agent_repo = git.Repo(agent_dir)
    agent_branch = agent_repo.active_branch.name
    A = agent_repo.head.commit.hexsha
    behind = [c for c in agent_repo.git.rev_list(f"{H}..{A}").split() if c]
    ahead = [c for c in agent_repo.git.rev_list(f"{A}..{H}").split() if c]
    return _Status(
        human_repo=human_repo,
        human_dir=human_dir,
        human_branch=human_branch,
        H=H,
        agent_dir=agent_dir,
        agent_repo=agent_repo,
        agent_branch=agent_branch,
        A=A,
        behind=behind,
        ahead=ahead,
        human_dirty=human_repo.is_dirty(untracked_files=True),
        agent_dirty=agent_repo.is_dirty(untracked_files=True),
    )


def _is_rebase_in_progress(repo: git.Repo) -> bool:
    return os.path.isdir(os.path.join(repo.git_dir, "rebase-merge")) or os.path.isdir(
        os.path.join(repo.git_dir, "rebase-apply")
    )


def _is_cherry_pick_in_progress(repo: git.Repo) -> bool:
    return os.path.exists(os.path.join(repo.git_dir, "CHERRY_PICK_HEAD"))


def _needs_divergent_snapshot(st: _Status, *, skip_uncommitted: bool, only_uncommitted: bool) -> bool:
    """Whether this package's run will use the divergent snapshot path."""
    kind = st.classify()
    if kind == "diverged":
        return not only_uncommitted
    if kind == "mixed":
        # The committed side uses the divergent path only when the branches
        # have diverged; a plain fast-forward needs no snapshot.
        return skip_uncommitted and bool(st.ahead)
    return False


class Pull:
    """Transfer work from ``.agent/<pkg>`` worktrees onto the human branch.

    This is not a configured `~tkt._environment.Tool`; it is invoked directly
    by the ``pull-sandbox`` CLI command.
    """

    @classmethod
    def run(
        cls,
        workspace: Workspace,
        *,
        skip_uncommitted: bool = False,
        only_uncommitted: bool = False,
        dry_run: bool = False,
        confirm: Callable[[str], bool] | None = None,
    ) -> None:
        """Transfer committed and/or uncommitted agent work.

        Parameters
        ----------
        workspace
            The workspace to operate on.
        skip_uncommitted
            Transfer committed work only, deferring any dirty agent worktree
            to a follow-up ``--only-uncommitted`` run.
        only_uncommitted
            Transfer uncommitted work only, assuming the committed side is
            already reconciled.
        dry_run
            Report the intended actions without modifying any worktree.
        confirm
            Callable used to confirm the ``--only-uncommitted`` ordering guard.
            Defaults to an interactive ``input``-based prompt.
        """
        if skip_uncommitted and only_uncommitted:
            raise PullError("--skip-uncommitted and --only-uncommitted are mutually exclusive.")
        if confirm is None:
            confirm = _confirm_prompt
        statuses = {pkg: _status(workspace, pkg) for pkg in workspace.packages}
        cls._preflight(
            workspace,
            statuses,
            skip_uncommitted=skip_uncommitted,
            only_uncommitted=only_uncommitted,
        )
        if dry_run:
            cls._report_dry_run(
                statuses, skip_uncommitted=skip_uncommitted, only_uncommitted=only_uncommitted
            )
            return
        ledger = _load_ledger(workspace)
        try:
            for pkg, st in statuses.items():
                cls._sync_package(
                    workspace,
                    pkg,
                    st,
                    skip_uncommitted=skip_uncommitted,
                    only_uncommitted=only_uncommitted,
                    confirm=confirm,
                    ledger=ledger,
                )
        except BaseException:
            if ledger:
                _save_ledger(workspace, ledger)
            else:
                _clear_ledger(workspace)
            raise
        else:
            if ledger:
                _save_ledger(workspace, ledger)
            else:
                _clear_ledger(workspace)

    @classmethod
    def _preflight(
        cls,
        workspace: Workspace,
        statuses: dict[str, _Status],
        *,
        skip_uncommitted: bool,
        only_uncommitted: bool,
    ) -> None:
        """Global guards run before any mutation."""
        for pkg, st in statuses.items():
            if st.agent_dir is not None and st.human_dirty and st.agent_dirty:
                raise PullError(
                    f"Package {pkg!r} has both a dirty human worktree and a dirty agent worktree; "
                    "aborting the whole run before making any changes. "
                    "Clean up one side and retry."
                )
        for pkg, st in statuses.items():
            if _needs_divergent_snapshot(
                st, skip_uncommitted=skip_uncommitted, only_uncommitted=only_uncommitted
            ):
                snapshot = st.human_branch + SYNC_SUFFIX
                if snapshot in st.human_repo.heads:
                    raise PullError(
                        f"Package {pkg!r}: snapshot branch {snapshot!r} already exists; "
                        "a previous sync was not completed. Run `tkt pull-sandbox --abort` "
                        "or clean it up manually and retry."
                    )

    @classmethod
    def _sync_package(
        cls,
        workspace: Workspace,
        pkg: str,
        st: _Status,
        *,
        skip_uncommitted: bool,
        only_uncommitted: bool,
        confirm: Callable[[str], bool],
        ledger: dict[str, _State],
    ) -> None:
        kind = st.classify()
        if kind == "skip":
            logging.info(f"{pkg}: nothing to transfer.")
            return
        if kind == "fast":
            if only_uncommitted:
                logging.info(f"{pkg}: no uncommitted work to transfer.")
                return
            cls._fast_path(st)
            return
        if kind == "uncommitted":
            if skip_uncommitted:
                ledger[pkg] = _State(deferred_uncommitted=True)
                logging.info(f"{pkg}: deferring uncommitted work (--skip-uncommitted).")
                return
            cls._uncommitted_transfer(workspace, pkg, st, ledger=ledger)
            return
        if kind == "mixed":
            if skip_uncommitted:
                # Committed side only; defer the dirty agent worktree.
                deferred = _State(deferred_uncommitted=True)
                if st.ahead:
                    cls._commit_transfer(workspace, pkg, st, ledger=deferred)
                else:
                    cls._fast_path(st)
                ledger[pkg] = deferred
                return
            if only_uncommitted:
                if st.behind and not confirm(
                    f"Agent branch {st.agent_branch} is ahead of the human branch, but "
                    "--only-uncommitted will not transfer it. Proceed anyway?"
                ):
                    logging.info(f"{pkg}: cancelled by user.")
                    return
                cls._uncommitted_transfer(workspace, pkg, st, ledger=ledger)
                return
            raise PullError(
                f"Package {pkg!r} has both commits ahead of the human branch and a dirty agent "
                "worktree. Pass --skip-uncommitted (committed only) or --only-uncommitted "
                "(uncommitted only) to process one side at a time."
            )
        if kind == "diverged":
            if only_uncommitted:
                logging.info(f"{pkg}: no uncommitted work to transfer.")
                return
            state = _State()
            cls._commit_transfer(workspace, pkg, st, ledger=state)
            if state.sync_kind is not None:
                # Rebase is in progress; record it for --finish/--abort.
                ledger[pkg] = state
            return

    @classmethod
    def _commit_transfer(cls, workspace: Workspace, pkg: str, st: _Status, *, ledger: _State) -> None:
        """Run the divergent committed path (snapshot + interactive rebase)."""
        assert st.agent_dir is not None
        assert st.agent_repo is not None
        assert st.A is not None
        snapshot = st.human_branch + SYNC_SUFFIX
        logger = logging.getLogger(__name__)
        logger.info(f"{pkg}: divergent; snapshotting {st.human_branch} and rebasing agent commits.")
        st.human_repo.git.branch(snapshot, st.H)
        ledger.snapshot_branch = snapshot
        ledger.sync_kind = "diverged"
        if st.human_dirty:
            st.human_repo.git.stash("push", "-u", "-m", f"tkt pull-sandbox: {pkg}")
            ledger.human_stash_ref = st.human_repo.git.rev_parse("-q", "stash@{0}").strip()
        st.human_repo.git.reset("--hard", st.A)
        try:
            st.human_repo.git.rebase("-i", snapshot)
            rebase_failed = False
        except git.exc.GitCommandError:
            # git exits nonzero both on a conflict (leaving a paused rebase,
            # which we detect below) and on a rebase that fails to start (e.g.
            # the sequence editor errors out, leaving no state at all). We must
            # distinguish the two: the latter must not be treated as success,
            # because the human's commits live only on the snapshot branch.
            rebase_failed = True
        if _is_rebase_in_progress(st.human_repo):
            logger.info(
                f"{pkg}: rebase is in progress. Resolve any conflicts and run "
                "`git rebase --continue`, then `tkt pull-sandbox --finish`."
            )
        elif rebase_failed:
            # The rebase failed to start rather than pausing on a conflict;
            # restore the human branch so its commits are not dropped, then
            # report the failure.
            cls._restore_commit_transfer(st, ledger)
            raise PullError(
                f"Package {pkg!r}: the divergent rebase could not be started. "
                "Your branch was restored to its previous state; nothing was transferred."
            )
        else:
            cls._finalize_commit_transfer(st, ledger)

    @classmethod
    def _finalize_commit_transfer(cls, st: _Status, ledger: _State) -> None:
        """Undo the snapshot bookkeeping after a committed path completes."""
        snapshot = st.snapshot_branch
        assert snapshot is not None
        if snapshot in st.human_repo.heads:
            st.human_repo.git.branch("-D", snapshot)
        ledger.snapshot_branch = None
        if ledger.human_stash_ref:
            st.human_repo.git.stash("apply", ledger.human_stash_ref)
            st.human_repo.git.stash("drop", ledger.human_stash_ref)
        ledger.human_stash_ref = None
        ledger.sync_kind = None

    @classmethod
    def _restore_commit_transfer(cls, st: _Status, ledger: _State) -> None:
        """Undo an aborted divergent transfer, restoring the human branch.

        Used when the interactive rebase fails to start (no in-progress state),
        so the human's commits -- which live only on the snapshot branch -- are
        not dropped. Unlike :meth:`_finalize_commit_transfer`, this resets the
        human branch back to the snapshot instead of promoting the rebased tip.
        """
        snapshot = st.snapshot_branch
        assert snapshot is not None
        st.human_repo.git.reset("--hard", snapshot)
        if snapshot in st.human_repo.heads:
            st.human_repo.git.branch("-D", snapshot)
        ledger.snapshot_branch = None
        if ledger.human_stash_ref:
            try:
                st.human_repo.git.stash("apply", ledger.human_stash_ref)
                st.human_repo.git.stash("drop", ledger.human_stash_ref)
            except git.exc.GitCommandError as exc:
                logging.warning(
                    f"failed to restore human stash {ledger.human_stash_ref} "
                    f"({exc}); leaving it in place - it was not destroyed."
                )
        ledger.human_stash_ref = None
        ledger.sync_kind = None

    @classmethod
    def _fast_path(cls, st: _Status) -> None:
        """Fast-forward the human branch to the agent branch."""
        assert st.agent_repo is not None
        assert st.A is not None
        st.human_repo.git.merge("--ff-only", st.A)
        logging.getLogger(__name__).info(f"Fast-forwarded {st.human_branch} to {st.A}.")

    @classmethod
    def _uncommitted_transfer(
        cls, workspace: Workspace, pkg: str, st: _Status, *, ledger: dict[str, _State]
    ) -> None:
        """Transfer the agent's uncommitted work as unstaged changes."""
        assert st.agent_repo is not None
        assert st.agent_dir is not None
        logger = logging.getLogger(__name__)
        # 1. Capture dirty agent worktree and untracked files as a WIP commit.
        #    --no-verify skips the package's pre-commit hooks on this temporary
        #    commit; they must not be able to fail the transfer.
        pre_wip_human = st.H
        pre_wip_agent = st.A
        assert pre_wip_agent is not None
        st.agent_repo.git.add("-A")
        st.agent_repo.git.commit("-m", _WIP_COMMIT_MESSAGE, no_verify=True)
        wip = st.agent_repo.head.commit.hexsha
        # 2. Apply the WIP delta onto the human branch via cherry-pick.
        try:
            st.human_repo.git.cherry_pick(wip)
        except git.exc.GitCommandError:
            # git exits nonzero on a conflict; it leaves the cherry-pick in
            # progress, which we detect and record below.
            pass
        if _is_cherry_pick_in_progress(st.human_repo):
            state = _State(
                sync_kind="uncommitted",
                pending_uncommitted_finalize=pre_wip_human,
                agent_pre_wip=pre_wip_agent,
            )
            ledger[pkg] = state
            logger.info(
                f"{pkg}: cherry-pick is in progress. Resolve any conflicts and run "
                "`git cherry-pick --continue`, then `tkt pull-sandbox --finish`."
            )
            return
        # 3. Expose the applied work as unstaged changes.
        st.human_repo.git.reset("--mixed", pre_wip_human)
        # 4. Drop the temporary WIP commit from the agent branch; the agent
        #    worktree returns to its original uncommitted/untracked state (the
        #    human branch now holds a copy of the work).
        st.agent_repo.git.reset("--mixed", pre_wip_agent)
        logger.info(f"{pkg}: transferred uncommitted work as unstaged changes.")

    @classmethod
    def finish(cls, workspace: Workspace, *, dry_run: bool = False) -> None:
        """Finalize an incomplete sync across all packages."""
        ledger = _load_ledger(workspace)
        if not ledger:
            raise PullError("nothing to do: no pull-sandbox sync is in progress.")
        if dry_run:
            for pkg in ledger:
                logging.info(f"{pkg}: would finalize in-progress sync.")
            return
        for pkg, state in ledger.items():
            try:
                cls._finalize_package(workspace, pkg, state)
            finally:
                cls._offer_sandbox_reset(workspace, pkg, state)
        _clear_ledger(workspace)

    @classmethod
    def _finalize_package(cls, workspace: Workspace, pkg: str, state: _State) -> None:
        st = _status(workspace, pkg)
        if state.snapshot_branch:
            # --finish assumes the user completed the rebase. Refuse if it is
            # still in progress, so the rollback snapshot is not destroyed.
            if _is_rebase_in_progress(st.human_repo):
                raise PullError(
                    f"{pkg}: the divergent rebase is still in progress. "
                    "Run `git rebase --continue` (after resolving conflicts), then "
                    "`tkt pull-sandbox --finish` again, or `tkt pull-sandbox --abort`."
                )
            if state.snapshot_branch in st.human_repo.heads:
                st.human_repo.git.branch("-D", state.snapshot_branch)
        if state.human_stash_ref:
            try:
                st.human_repo.git.stash("apply", state.human_stash_ref)
                st.human_repo.git.stash("drop", state.human_stash_ref)
            except git.exc.GitCommandError as exc:
                logging.warning(
                    f"{pkg}: failed to pop human stash {state.human_stash_ref} "
                    f"({exc}); leaving it in place - it was not destroyed."
                )
        if state.pending_uncommitted_finalize:
            # Clear any lingering in-progress operation first: a mixed reset
            # does not clear git's "in the middle of an operation" state, so
            # aborting a still-in-progress cherry-pick/rebase first keeps the
            # final reset clean (Decision 5). The underlying work survives in
            # the agent's WIP commit.
            cp_in_progress = _is_cherry_pick_in_progress(st.human_repo)
            if _is_rebase_in_progress(st.human_repo):
                st.human_repo.git.rebase("--abort")
            if cp_in_progress:
                st.human_repo.git.cherry_pick("--abort")
            st.human_repo.git.reset("--mixed", state.pending_uncommitted_finalize)
            # Drop the temporary WIP commit from the agent branch, but only if
            # the transfer actually completed (the human-side cherry-pick was
            # continued). If --finish had to abandon an in-progress cherry-pick
            # above, the agent's WIP commit is the only surviving copy of the
            # work, and must be kept.
            if state.agent_pre_wip and not cp_in_progress and st.agent_repo is not None:
                st.agent_repo.git.reset("--mixed", state.agent_pre_wip)

    @classmethod
    def _offer_sandbox_reset(cls, workspace: Workspace, pkg: str, state: _State) -> None:
        """Suggest ``tkt sandbox-reset`` when it would not be a no-op.

        Suppressed while the agent worktree still holds deferred/pending
        uncommitted work, so the suggestion cannot lead the user to destroy
        work meant for a follow-up ``--only-uncommitted`` sync.
        """
        if state.deferred_uncommitted or state.pending_uncommitted_finalize:
            return
        st = _status(workspace, pkg)
        if st.agent_dir is None:
            return
        if not st.agent_dirty and st.A == st.human_repo.head.commit.hexsha:
            return
        logging.info(
            f"{pkg}: agent worktree still differs from the human branch; "
            "run `tkt sandbox-reset` to reconcile it."
        )

    @classmethod
    def abort(cls, workspace: Workspace, *, dry_run: bool = False) -> None:
        """Cancel an incomplete sync across all packages."""
        ledger = _load_ledger(workspace)
        if not ledger:
            raise PullError("nothing to do: no pull-sandbox sync is in progress.")
        if dry_run:
            for pkg in ledger:
                logging.info(f"{pkg}: would abort in-progress sync.")
            return
        for pkg, state in ledger.items():
            st = _status(workspace, pkg)
            if _is_rebase_in_progress(st.human_repo):
                st.human_repo.git.rebase("--abort")
            if _is_cherry_pick_in_progress(st.human_repo):
                st.human_repo.git.cherry_pick("--abort")
            if state.snapshot_branch:
                if state.snapshot_branch in st.human_repo.heads:
                    st.human_repo.git.reset("--hard", state.snapshot_branch)
                    st.human_repo.git.branch("-D", state.snapshot_branch)
            if state.human_stash_ref:
                try:
                    st.human_repo.git.stash("apply", state.human_stash_ref)
                    st.human_repo.git.stash("drop", state.human_stash_ref)
                except git.exc.GitCommandError as exc:
                    logging.warning(
                        f"{pkg}: failed to restore human stash {state.human_stash_ref} "
                        f"({exc}); leaving it in place."
                    )
            if state.sync_kind == "uncommitted" and state.agent_pre_wip:
                # Return the agent branch to its pre-WIP tip so the transfer's
                # work lives on as ordinary uncommitted working-tree changes
                # (rather than a committed WIP) for a future retry.
                assert st.agent_repo is not None
                st.agent_repo.git.reset("--mixed", state.agent_pre_wip)
        _clear_ledger(workspace)

    @classmethod
    def _report_dry_run(
        cls,
        statuses: dict[str, _Status],
        *,
        skip_uncommitted: bool,
        only_uncommitted: bool,
    ) -> None:
        for pkg, st in statuses.items():
            kind = st.classify()
            action = cls._dry_run_action(
                kind, st, skip_uncommitted=skip_uncommitted, only_uncommitted=only_uncommitted
            )
            logging.info(f"{pkg}: would {action}.")

    @classmethod
    def _dry_run_action(
        cls, kind: str, st: _Status, *, skip_uncommitted: bool, only_uncommitted: bool
    ) -> str:
        if kind == "skip":
            return "do nothing (nothing to transfer)"
        if kind == "fast":
            if only_uncommitted:
                return "not transfer committed work (--only-uncommitted)"
            return "fast-forward the human branch to the agent branch"
        if kind == "uncommitted":
            if skip_uncommitted:
                return "defer the uncommitted work (--skip-uncommitted)"
            return "transfer uncommitted work as unstaged changes"
        if kind == "diverged":
            if only_uncommitted:
                return "not transfer committed work (--only-uncommitted)"
            return "snapshot + interactive rebase (drop variant agent commits)"
        # mixed
        if skip_uncommitted:
            committed = "rebase" if st.ahead else "fast-forward"
            return f"{committed} committed side and defer uncommitted work"
        if only_uncommitted:
            guard = " (requires confirmation: agent branch is ahead)" if st.behind else ""
            return "transfer uncommitted work as unstaged changes" + guard
        return "error: pass --skip-uncommitted or --only-uncommitted"


def _confirm_prompt(message: str) -> bool:
    while True:
        answer = input(f"{message} [y/N] ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no", ""):
            return False
        logging.warning(f"Unrecognized response {answer!r}; expected y/n.")
