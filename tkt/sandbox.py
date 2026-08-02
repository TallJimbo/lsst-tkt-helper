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

__all__ = ("Sandbox",)

import logging
import os
import shlex
import shutil
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

import git

from ._environment import Environment, Tool
from ._workspace import Workspace

# Subdirectory of the workspace where the agent's writable state lives.
AGENT_SUBDIR = ".agent"

# Suffix appended to the workspace's ticket branch to form the agent branch.
AGENT_BRANCH_SUFFIX = "-agent"

# Path to the AGENTS.md boilerplate template installed into the agent
# directory to guide the LLM agent.
_AGENTS_MD_TEMPLATE = os.path.join(os.path.dirname(__file__), "AGENTS.md.in")


class Sandbox(Tool):
    """Tool that runs an LLM agent inside a ``bwrap`` sandbox.

    The sandbox uses the same filesystem paths as the host (so paths passed
    across the sandbox boundary work in both directions), gives the agent a
    read-write git worktree under ``<workspace>/.agent/`` on a separate
    branch, and leaves the human's worktree read-only from the agent's
    perspective.

    Parameters
    ----------
    command
        Command (as an argv list) to ``exec`` inside the sandbox after the
        Rubin environment has been set up.
    mounts_ro
        Absolute host paths to bind-mount read-only into the sandbox at the
        same path.  ``~`` is expanded to ``$HOME``.
    mounts_rw
        Absolute host paths to bind-mount read-write into the sandbox at the
        same path.  ``~`` is expanded to ``$HOME``.
    env
        Extra environment variables to set inside the sandbox.
    """

    def __init__(
        self,
        *,
        command: Sequence[str],
        mounts_ro: Sequence[str] = (),
        mounts_rw: Sequence[str] = (),
        env: dict[str, str] | None = None,
    ):
        self._command = tuple(command)
        self._mounts_ro = tuple(mounts_ro)
        self._mounts_rw = tuple(mounts_rw)
        self._env = dict(env) if env is not None else {}

    @classmethod
    def from_json_data(cls, data: dict[str, Any]) -> Tool:
        command = data.pop("command")
        if isinstance(command, str):
            command = shlex.split(command)
        mounts = data.pop("mounts", {})
        mounts_ro = mounts.pop("ro", [])
        mounts_rw = mounts.pop("rw", [])
        if mounts:
            raise ValueError(f"Unexpected entries in sandbox mounts configuration: {mounts}.")
        env = data.pop("env", {})
        if data:
            raise ValueError(f"Unexpected entries in sandbox configuration: {data}.")
        return cls(
            command=command,
            mounts_ro=mounts_ro,
            mounts_rw=mounts_rw,
            env=env,
        )

    def write(
        self,
        ticket: str,
        directory: str,
        packages: Iterable[str],
        workspace: Workspace,
        environment: Environment,
    ) -> None:
        packages = list(packages)
        agent_dir = os.path.join(directory, AGENT_SUBDIR)
        os.makedirs(agent_dir, exist_ok=True)
        # Install the AGENTS.md boilerplate into the agent directory so the
        # LLM agent has context about the sandbox setup.
        shutil.copy2(_AGENTS_MD_TEMPLATE, os.path.join(directory, "AGENTS.md"))
        for package in packages:
            package_dir = os.path.join(directory, package)
            agent_package_dir = os.path.join(agent_dir, package)
            if not os.path.exists(package_dir):
                logging.info(
                    f"Skipping agent worktree for {package}: main clone at {package_dir} does not exist."
                )
                continue
            if os.path.exists(agent_package_dir):
                logging.info(f"Agent worktree for {package} already exists at {agent_package_dir}.")
                continue
            self._add_agent_worktree(package_dir, agent_package_dir, ticket)
        # Refresh the copied metapackage each time; the top-level ups/ is
        # regenerated whenever `tkt update` runs, and we want the agent's
        # copy to track it.
        ups_src = os.path.join(directory, "ups")
        ups_dst = os.path.join(agent_dir, "ups")
        if os.path.exists(ups_src):
            if os.path.exists(ups_dst):
                shutil.rmtree(ups_dst)
            shutil.copytree(ups_src, ups_dst)

    def _add_agent_worktree(self, package_dir: str, agent_package_dir: str, ticket: str) -> None:
        repo = git.Repo(package_dir)
        source_branch = repo.active_branch.name
        agent_branch = f"{source_branch}{AGENT_BRANCH_SUFFIX}"
        logging.info(
            f"Creating agent worktree at {agent_package_dir} on branch {agent_branch} (from {source_branch})."
        )
        if agent_branch in repo.heads:
            # Existing branch: attach a worktree to it without touching the
            # branch head (may have been rebased/reset by the user).
            repo.git.worktree("add", agent_package_dir, agent_branch)
        else:
            repo.git.worktree("add", "-b", agent_branch, agent_package_dir, source_branch)

    def reset(self, workspace: Workspace) -> None:
        """Restore every agent worktree to its human-workspace branch.

        For each package with a worktree at ``<workspace>/.agent/<package>``,
        save the agent's work before discarding it:

        - Uncommitted work (staged, unstaged, untracked, and ignored files) is
          pushed to the git stash with a message naming the package.
        - Any commits on the agent branch not reachable from the human branch
          are saved to a timestamped backup branch
          ``<agent-branch>-saved-<%Y%m%dT%H%M%S>``.

        Then the worktree is reset to the human branch
        (``git reset --hard``) and cleaned of remaining untracked/ignored
        files (``git clean -fdx``). Packages without a ``.agent/<package>``
        worktree are skipped.
        """
        agent_dir = os.path.join(workspace.directory, AGENT_SUBDIR)
        for package, human_branch in workspace.packages.items():
            agent_package_dir = os.path.join(agent_dir, package)
            if not os.path.exists(agent_package_dir):
                logging.info(
                    f"Skipping agent worktree for {package}: no .agent worktree at {agent_package_dir}."
                )
                continue
            self._reset_agent_worktree(agent_package_dir, package, human_branch)

    def _reset_agent_worktree(self, agent_package_dir: str, package: str, human_branch: str) -> None:
        repo = git.Repo(agent_package_dir)
        agent_branch = repo.active_branch.name
        logging.info(f"Resetting agent worktree for {package} ({agent_branch}) to {human_branch}.")
        # Save uncommitted work (staged, unstaged, untracked, and ignored)
        # to the stash. `git stash push` is a no-op on a clean worktree
        # (it reports "No local changes to save" and creates no entry), so it
        # is safe to run unconditionally. `--all` also captures ignored files,
        # which `git clean -fdx` below would otherwise delete.
        repo.git.stash("push", "--all", "-m", f"tkt reset backup: {package}")
        # Save any commits on the agent branch not reachable from the human
        # branch to a uniquely-named backup branch so the reset does not
        # discard them.
        unmerged = repo.git.rev_list(f"{human_branch}..HEAD").split()
        if unmerged:
            backup = f"{agent_branch}-saved-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
            repo.git.branch(backup, agent_branch)
            logging.info(f"Saved unmerged commits to backup branch {backup}.")
        # Restore the worktree to the state of the human branch.
        repo.git.reset("--hard", human_branch)
        repo.git.clean("-fdx")
        logging.info(f"Reset {agent_branch} to {human_branch} and cleaned.")

    def run(
        self,
        workspace: Workspace,
        *,
        shell: bool = False,
        command: str | None = None,
    ) -> None:
        """Launch the sandbox for ``workspace``.

        Parameters
        ----------
        workspace
            The workspace to run the sandbox for.
        shell
            If ``True``, launch an interactive login shell inside the sandbox
            (with the Rubin environment set up) instead of the configured
            command.  Useful for debugging the sandbox contents.
        command
            If given, override the configured final command with this
            shlex-split string.  Mutually exclusive with ``shell``.
        """
        argv = self._build_bwrap_argv(workspace, shell=shell, command=command)
        logging.debug("exec: %s", shlex.join(argv))
        os.execvp(argv[0], argv)

    def run_single_repo(
        self,
        repo_dir: str,
        *,
        shell: bool = False,
        conda_env: str | None = None,
        command: str | None = None,
    ) -> None:
        """Launch the sandbox for a single repository (not a ticket workspace).

        Unlike :meth:`run`, the repository root is bind-mounted read-write and
        the agent writes directly to the main worktree.

        Parameters
        ----------
        repo_dir
            Root directory of the git repository to run the sandbox for.
        shell
            If ``True``, launch an interactive login shell inside the sandbox
            instead of the configured command.
        conda_env
            If given, activate this conda environment before setting up the
            Rubin environment inside the sandbox.
        command
            If given, override the configured final command with this
            shlex-split string.  Mutually exclusive with ``shell``.
        """
        argv = self._build_single_repo_argv(repo_dir, shell=shell, conda_env=conda_env, command=command)
        logging.debug("exec: %s", shlex.join(argv))
        os.execvp(argv[0], argv)

    def _build_bwrap_argv(
        self, workspace: Workspace, *, shell: bool, command: str | None = None
    ) -> list[str]:
        home = os.path.expanduser("~")
        agent_dir = os.path.join(workspace.directory, AGENT_SUBDIR)
        # Workspace-specific mounts. The agent's directory and the .git
        # subdirectories are the only writable locations in the workspace; main
        # workspace and each external are read-only.
        mounts: list[str] = []
        mounts += ["--ro-bind", workspace.directory, workspace.directory]
        mounts += ["--bind", agent_dir, agent_dir]
        opencode_hidden_dir = os.path.join(workspace.directory, ".opencode")
        mounts += ["--bind", opencode_hidden_dir, opencode_hidden_dir]
        for package in workspace.packages:
            package_dir = os.path.join(workspace.directory, package)
            git_dir = os.path.join(package_dir, ".git")
            if os.path.exists(package_dir):
                mounts += ["--ro-bind", package_dir, package_dir]
                mounts += ["--bind", git_dir, git_dir]
        for external_path in workspace.externals.values():
            if os.path.exists(external_path):
                mounts += ["--ro-bind", external_path, external_path]
            else:
                logging.warning(f"External path {external_path} does not exist; skipping mount.")
        inner = self._build_inner_script(shell=shell, command=command)
        return self._build_common_argv(home=home, mounts=mounts, inner=inner)

    def _build_single_repo_argv(
        self,
        repo_dir: str,
        *,
        shell: bool,
        conda_env: str | None = None,
        command: str | None = None,
    ) -> list[str]:
        home = os.path.expanduser("~")
        # The whole repository is writable by the agent; no separate .git
        # handling is needed since there are no per-package worktrees.
        mounts: list[str] = ["--bind", repo_dir, repo_dir]
        inner = self._build_inner_script(shell=shell, conda_env=conda_env, repo_dir=repo_dir, command=command)
        return self._build_common_argv(home=home, mounts=mounts, inner=inner)

    def _build_common_argv(self, *, home: str, mounts: list[str], inner: str) -> list[str]:
        argv: list[str] = [
            "bwrap",
            "--ro-bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
            # $HOME is a tmpfs (empty writable directory), so ~/.ssh/,
            # ~/.git-credentials, ~/.config/git/credentials/, and other
            # credential stores are NOT visible to the agent. This prevents the
            # agent from authenticating git pushes to existing remotes.
            "--tmpfs",
            home,
            "--setenv",
            "HOME",
            home,
        ]
        # Mode-specific mounts go between the base and config mounts.
        argv += mounts
        # Track destinations already mounted read-write (from the mode-specific
        # mounts and the configured rw mounts) so we can avoid re-mounting them
        # read-only, which would override the rw mount and make it read-only.
        rw_paths = {mounts[i + 2] for i in range(0, len(mounts), 3) if mounts[i] in ("--bind", "--bind-try")}
        for path in self._mounts_ro:
            expanded = os.path.expanduser(path)
            if expanded in rw_paths:
                logging.info(f"Skipping read-only mount of {expanded}; already mounted read-write.")
                continue
            argv += ["--ro-bind-try", expanded, expanded]
        for path in self._mounts_rw:
            expanded = os.path.expanduser(path)
            rw_paths.add(expanded)
            argv += ["--bind-try", expanded, expanded]
        # Extra environment variables from configuration.
        for name, value in self._env.items():
            argv += ["--setenv", name, value]
        # Namespaces and cleanup.  Note: --unshare-net is intentionally
        # absent -- OpenCode needs to reach the LLM API.
        argv += [
            "--unshare-user",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup-try",
            "--die-with-parent",
            "--",
        ]
        argv += ["/bin/bash", "-c", inner]
        return argv

    def _build_inner_script(
        self,
        *,
        shell: bool,
        command: str | None = None,
        conda_env: str | None = None,
        repo_dir: str | None = None,
    ) -> str:
        lines: list[str] = []
        if conda_env is not None:
            # Source the conda base's profile.d/conda.sh from $CONDA_PREFIX,
            # falling back to deriving the base from `which conda`.
            lines.append(
                "source $CONDA_PREFIX/etc/profile.d/conda.sh "
                "|| source $(dirname $(dirname $(which conda)))/etc/profile.d/conda.sh"
            )
            lines.append(f"conda activate {conda_env}")
        # `setup -r .` runs unconditionally for workspace mode (repo_dir is
        # None) since the workspace always has an ups/; in single-repo mode it
        # only runs when the repository actually contains an ups/ directory.
        if repo_dir is None or os.path.isdir(os.path.join(repo_dir, "ups")):
            lines += ["exec", "setup -r ."]
        if shell:
            lines.append("exec /bin/bash --login -i")
        elif command is not None:
            lines.append(f"exec {shlex.join(shlex.split(command))}")
        else:
            lines.append(f"exec {shlex.join(self._command)}")
        return "\n".join(lines)
