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
        shutil.copy2(_AGENTS_MD_TEMPLATE, os.path.join(agent_dir, "AGENTS.md"))
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

    def run(
        self,
        workspace: Workspace,
        *,
        shell: bool = False,
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
        """
        argv = self._build_bwrap_argv(workspace, shell=shell)
        logging.debug("exec: %s", shlex.join(argv))
        os.execvp(argv[0], argv)

    def _build_bwrap_argv(self, workspace: Workspace, *, shell: bool) -> list[str]:
        home = os.path.expanduser("~")
        agent_dir = os.path.join(workspace.directory, AGENT_SUBDIR)
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
        # Workspace-specific mounts. The agent's directory and the .git
        # subdirectories are the only writable locations in the workspace; main
        # workspace and each external are read-only.
        argv += ["--ro-bind", workspace.directory, workspace.directory]
        argv += ["--bind", agent_dir, agent_dir]
        for package in workspace.packages:
            package_dir = os.path.join(workspace.directory, package)
            git_dir = os.path.join(package_dir, ".git")
            if os.path.exists(package_dir):
                argv += ["--ro-bind", package_dir, package_dir]
                argv += ["--bind", git_dir, git_dir]
        for external_path in workspace.externals.values():
            if os.path.exists(external_path):
                argv += ["--ro-bind", external_path, external_path]
            else:
                logging.warning(f"External path {external_path} does not exist; skipping mount.")
        # Extra mounts from configuration.
        for path in self._mounts_ro:
            expanded = os.path.expanduser(path)
            argv += ["--ro-bind-try", expanded, expanded]
        for path in self._mounts_rw:
            expanded = os.path.expanduser(path)
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
            "--chdir",
            agent_dir,
            "--",
        ]
        lines = [
            "exec",
            "setup -r .",
        ]
        if shell:
            lines.append("exec /bin/bash --login -i")
        else:
            lines.append(f"exec {shlex.join(self._command)}")
        inner = "\n".join(lines)
        argv += ["/bin/bash", "-c", inner]
        return argv
