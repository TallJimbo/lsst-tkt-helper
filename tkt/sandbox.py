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
    setup_script
        Absolute path to a shell script (e.g. ``loadLSST.bash``) that must be
        ``source``d before ``setup -r .`` will work inside the sandbox.
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
        setup_script: str,
        mounts_ro: Sequence[str] = (),
        mounts_rw: Sequence[str] = (),
        env: dict[str, str] | None = None,
    ):
        self._command = tuple(command)
        self._setup_script = setup_script
        self._mounts_ro = tuple(mounts_ro)
        self._mounts_rw = tuple(mounts_rw)
        self._env = dict(env) if env is not None else {}

    @classmethod
    def from_json_data(cls, data: dict[str, Any]) -> Tool:
        command = data.pop("command")
        if isinstance(command, str):
            command = shlex.split(command)
        setup_script = data.pop("setup_script")
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
            setup_script=setup_script,
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

    def remove(
        self,
        ticket: str,
        directory: str,
        packages: Iterable[str],
        workspace: Workspace,
        environment: Environment,
    ) -> None:
        agent_dir = os.path.join(directory, AGENT_SUBDIR)
        for package in packages:
            package_dir = os.path.join(directory, package)
            agent_package_dir = os.path.join(agent_dir, package)
            if not os.path.exists(agent_package_dir):
                continue
            if os.path.exists(package_dir):
                try:
                    repo = git.Repo(package_dir)
                    repo.git.worktree("remove", "--force", agent_package_dir)
                    continue
                except Exception as err:
                    logging.warning(
                        f"Failed to remove worktree {agent_package_dir} via git: {err}; "
                        f"falling back to rmtree."
                    )
            shutil.rmtree(agent_package_dir, ignore_errors=True)
        if os.path.exists(agent_dir):
            shutil.rmtree(agent_dir, ignore_errors=True)

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
            "--tmpfs",
            home,
            "--setenv",
            "HOME",
            home,
        ]
        # Workspace-specific mounts.  The agent's directory is the only
        # writable location in the workspace; the human's worktrees, the
        # top-level ups/, and each external are read-only.
        argv += ["--bind", agent_dir, agent_dir]
        ups_dir = os.path.join(workspace.directory, "ups")
        if os.path.exists(ups_dir):
            argv += ["--ro-bind", ups_dir, ups_dir]
        for package in workspace.packages:
            package_dir = os.path.join(workspace.directory, package)
            if os.path.exists(package_dir):
                argv += ["--ro-bind", package_dir, package_dir]
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
        # Redirect stderr from the environment setup phase to a per-workspace
        # log file: Rubin's setup scripts print noise (e.g. harmless "grep:
        # write error: Broken pipe") that confuses ACP clients reading the
        # sandbox process's stderr.  OpenCode's own stderr is left untouched
        # so real errors surface to the client.
        #
        # This is a sequence of independent statements rather than a
        # bash-`-c` compound group; grouping statements causes bash to fork
        # a subshell to apply the group's redirection, which prevents the
        # final `exec` from replacing the top-level bash process.
        setup_log = shlex.quote(os.path.join(agent_dir, "setup.log"))
        setup_script = shlex.quote(self._setup_script)
        # Redirect stdout and stderr at the shell level (not per-command)
        # so they apply to every subshell spawned by Rubin's setup scripts.
        # ACP clients only start reading the sandbox process's streams
        # after their end of the JSON-RPC handshake completes; if setup
        # writes to those streams first the writes block on a full pipe
        # buffer and the whole environment hangs.
        #
        # The original stdout and stderr are saved on fds 3 and 4 so that
        # the target command can restore them and speak ACP normally.
        lines = [
            f"exec 3>&1 4>&2 >>{setup_log} 2>&1",
            f"source {setup_script}",
            "setup -r .",
        ]
        if shell:
            lines.append("exec /bin/bash --login -i >&3 2>&4 3>&- 4>&-")
        else:
            lines.append(f"exec {shlex.join(self._command)} >&3 2>&4 3>&- 4>&-")
        inner = "\n".join(lines)
        argv += ["/bin/bash", "-c", inner]
        return argv
