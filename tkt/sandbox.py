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

__all__ = ("Sandbox", "cleanup_stale_bridges")

import ctypes
import logging
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
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

# Default host port bridged into restricted sandboxes; the LLM endpoint that
# the agent reaches via localhost (backed by an ssh port-forward outside the
# sandbox).  Overridable through the ``port`` configuration entry, which may
# be a single integer or a list of integers.
_DEFAULT_PORT = 8080

# Sequence of default ports bridged into restricted sandboxes (see above).
_DEFAULT_PORTS = (_DEFAULT_PORT,)

# Default host port bridged into restricted sandboxes for the visual-companion
# server.  The same port number is used on the host and inside the sandbox, so
# the URL the companion reports (http://localhost:<port>/...) is reachable
# from the host browser verbatim.  Overridable through the ``vc_port``
# configuration entry.
_DEFAULT_VC_PORT = 8081


def _normalize_ports(port: int | Sequence[int]) -> tuple[int, ...]:
    """Return ``port`` as a tuple of ints, accepting an int or a sequence.

    Raises ``ValueError`` if ``port`` is a non-integer scalar or a sequence
    containing a non-integer.
    """
    if isinstance(port, bool):
        raise ValueError(f"'port' must be an integer or list of integers, got {port!r}.")
    if isinstance(port, int):
        return (port,)
    result = []
    for p in port:
        if isinstance(p, bool) or not isinstance(p, int):
            raise ValueError(f"'port' must contain only integers, got {port!r}.")
        result.append(int(p))
    if not result:
        raise ValueError("'port' must include at least one port.")
    return tuple(result)


def _set_pdeathsig() -> None:
    """Arrange for the current process to be SIGTERM'd when its parent dies.

    Used as a ``preexec_fn`` for the host-side bridge ``socat`` so that even if
    ``tkt`` is killed abruptly (e.g. SIGKILL/OOM), the kernel reaps the bridge
    instead of leaving an orphaned forwarder and its socket behind.
    """
    libc = ctypes.CDLL(None, use_errno=True)
    PR_SET_PDEATHSIG = 1
    libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)


def _bridge_net_dir(args: Sequence[str]) -> str | None:
    """Return the ``net-*`` dir name referenced by a bridge socat's argv.

    The host-side and sandbox-side bridge socats reference a shared socket
    under ``.../state/tkt/net-<id>/``; extract ``<id>``.  Returns ``None`` if
    ``args`` is not a bridge socat command line.
    """
    marker = "state/tkt/net-"
    for arg in args:
        if marker not in arg:
            continue
        rest = arg.split(marker, 1)[1]
        net = rest.split("/", 1)[0]
        if net:
            return "net-" + net
    return None


def _iter_bridge_socat_pids() -> Iterable[int]:
    """Yield PIDs of socat processes referencing a tkt bridge dir."""
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                raw = f.read()
        except OSError:
            continue
        args = raw.split(b"\x00")
        if not args or b"socat" not in args[0]:
            continue
        try:
            decoded = [a.decode("utf-8") for a in args]
        except UnicodeDecodeError:
            continue
        if _bridge_net_dir(decoded) is not None:
            yield int(entry)


def cleanup_stale_bridges(*, dry_run: bool = False) -> tuple[int, list[str]]:
    """Kill orphaned bridge socats whose shared ``net-*`` directory is gone.

    Each bridge socat references a ``net-*`` directory that ``_stop_bridge``
    removes on a clean shutdown, so a live bridge always has its directory
    present.  A socat is therefore stale when the directory it references no
    longer exists.  Orphans arise only from a bad shutdown: the owning ``tkt``
    process died without running ``_stop_bridge`` (e.g. an unclean kill that
    the parent-death signal did not cover).

    Returns a ``(count, net_dirs)`` tuple with the number of socats killed (or
    that would be killed under ``dry_run``) and the referenced net dirs.
    """
    state_dir = os.path.join(os.path.expanduser("~"), ".local", "state", "tkt")
    killed = 0
    dirs: list[str] = []
    for pid in _iter_bridge_socat_pids():
        cmdline = f"/proc/{pid}/cmdline"
        try:
            with open(cmdline, "rb") as f:
                raw = f.read()
        except OSError:
            continue
        args = [a.decode("utf-8") for a in raw.split(b"\x00")]
        net = _bridge_net_dir(args)
        if net is None:
            continue
        if os.path.isdir(os.path.join(state_dir, net)):
            continue
        if not dry_run:
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                continue
        killed += 1
        dirs.append(net)
    return killed, dirs


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
    network
        If ``True``, give the sandbox full, unrestricted network access by
        sharing the host network namespace (the historical default).  If
        ``False`` (the default), isolate the sandbox with ``--unshare-net``
        and bridge a single localhost port (see ``port``) back to the host so
        the LLM endpoint remains reachable while everything else is blocked.
    port
        Host port, or sequence of host ports, bridged into the sandbox's
        isolated network namespace so tools can reach the LLM via localhost.
        Default :data:`_DEFAULT_PORT`. Each port maps to the same port on the
        host's loopback (the ssh tunnel's listen port).  Only used when
        ``network`` is ``False``.
    vc_port
        Host port (default :data:`_DEFAULT_VC_PORT`) bridged into the sandbox's
        isolated network namespace for the visual-companion server, in the
        reverse direction (host browser -> in-sandbox server).  The same port
        number is used on the host and inside the sandbox so the companion's
        reported URL is reachable from the host browser verbatim.  Only used
        when ``network`` is ``False``.
    """

    def __init__(
        self,
        *,
        command: Sequence[str],
        mounts_ro: Sequence[str] = (),
        mounts_rw: Sequence[str] = (),
        env: dict[str, str] | None = None,
        network: bool = False,
        port: int | Sequence[int] = _DEFAULT_PORT,
        vc_port: int = _DEFAULT_VC_PORT,
    ):
        self._command = tuple(command)
        self._mounts_ro = tuple(mounts_ro)
        self._mounts_rw = tuple(mounts_rw)
        self._env = dict(env) if env is not None else {}
        self._network = bool(network)
        self._ports = _normalize_ports(port)
        self._vc_port = int(vc_port)

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
        network = data.pop("network", False)
        if not isinstance(network, bool):
            raise ValueError(f"'network' must be a boolean, got {network!r}.")
        port = data.pop("port", _DEFAULT_PORT)
        vc_port = data.pop("vc_port", _DEFAULT_VC_PORT)
        if isinstance(vc_port, bool) or not isinstance(vc_port, int):
            raise ValueError(f"'vc_port' must be an integer, got {vc_port!r}.")
        if data:
            raise ValueError(f"Unexpected entries in sandbox configuration: {data}.")
        return cls(
            command=command,
            mounts_ro=mounts_ro,
            mounts_rw=mounts_rw,
            env=env,
            network=network,
            port=port,
            vc_port=vc_port,
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
        network: bool | None = None,
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
        network
            Override the configured network mode for this run; ``None`` uses
            the value from configuration.  ``True`` grants full network
            access; ``False`` restricts to the bridged localhost port.
        """
        if network is None:
            network = self._network
        if network:
            argv = self._build_bwrap_argv(workspace, shell=shell, command=command, network=True)
            logging.debug("exec: %s", shlex.join(argv))
            os.execvp(argv[0], argv)
        # Restricted mode: bridge the LLM port back to the host.
        net_dir, procs = self._start_bridge()
        try:
            argv = self._build_bwrap_argv(
                workspace,
                shell=shell,
                command=command,
                network=False,
                net_dir=net_dir,
                ports=self._ports,
                vc_port=self._vc_port,
            )
            logging.debug("exec: %s", shlex.join(argv))
            rc = subprocess.Popen(argv).wait()
        finally:
            self._stop_bridge(net_dir, procs)
        raise SystemExit(rc)

    def run_single_repo(
        self,
        repo_dir: str,
        *,
        shell: bool = False,
        conda_env: str | None = None,
        command: str | None = None,
        network: bool | None = None,
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
        network
            Override the configured network mode for this run; ``None`` uses
            the value from configuration.  ``True`` grants full network
            access; ``False`` restricts to the bridged localhost port.
        """
        if network is None:
            network = self._network
        if network:
            argv = self._build_single_repo_argv(
                repo_dir, shell=shell, conda_env=conda_env, command=command, network=True
            )
            logging.debug("exec: %s", shlex.join(argv))
            os.execvp(argv[0], argv)
        net_dir, procs = self._start_bridge()
        try:
            argv = self._build_single_repo_argv(
                repo_dir,
                shell=shell,
                conda_env=conda_env,
                command=command,
                network=False,
                net_dir=net_dir,
                ports=self._ports,
                vc_port=self._vc_port,
            )
            logging.debug("exec: %s", shlex.join(argv))
            rc = subprocess.Popen(argv).wait()
        finally:
            self._stop_bridge(net_dir, procs)
        raise SystemExit(rc)

    def _start_bridge(self) -> tuple[str, list[subprocess.Popen[Any]]]:
        """Start the host-side bridges for a restricted sandbox.

        Creates a private directory for the shared unix sockets under the
        user's state directory and launches host-side ``socat`` forwarders:

        - One ``llm-<port>.sock`` per bridged ``port`` accepts connections on
          the unix socket (visible inside the sandbox via a read-write bind
          mount) and forwards them to the host's localhost ``<port>`` (where
          an ssh port-forward exposes the LLM).  Direction: sandbox -> host.
          ``vc.sock`` accepts connections on the host's localhost ``vc_port``
          and forwards them to the unix socket (read by an in-sandbox socat
          that hands them to the visual-companion server).  Direction: host ->
          sandbox.

        Returns the socket directory and the list of host ``socat``
        subprocesses.
        """
        state_dir = os.path.join(os.path.expanduser("~"), ".local", "state", "tkt")
        os.makedirs(state_dir, exist_ok=True)
        net_dir = tempfile.mkdtemp(prefix="net-", dir=state_dir)
        vc_sock = os.path.join(net_dir, "vc.sock")
        vc_log = os.path.join(net_dir, "host-vc-socat.log")
        procs: list[subprocess.Popen[Any]] = []
        sock_paths: list[str] = []
        try:
            for p in self._ports:
                sock = os.path.join(net_dir, f"llm-{p}.sock")
                log = os.path.join(net_dir, f"host-llm-{p}.log")
                sock_paths.append(sock)
                with open(log, "w", encoding="utf-8") as f:
                    procs.append(
                        subprocess.Popen(
                            ["socat", f"UNIX-LISTEN:{sock},fork", f"TCP4:127.0.0.1:{p}"],
                            stdout=f,
                            stderr=subprocess.STDOUT,
                            # Isolate each forwarder in its own process group
                            # (so we can signal its forked children during
                            # teardown) and arrange for it to be SIGTERM'd if
                            # we are killed abruptly.
                            start_new_session=True,
                            preexec_fn=_set_pdeathsig,
                        )
                    )
            # The visual-companion forwarder listens on TCP and, per accepted
            # connection, lazily connects to vc.sock.  vc.sock is created by an
            # in-sandbox socat (UNIX-LISTEN) after bwrap launches, so there is
            # no startup race here: the host listener binds immediately and
            # only needs vc.sock once a browser connection arrives.
            with open(vc_log, "w", encoding="utf-8") as f:
                procs.append(
                    subprocess.Popen(
                        [
                            "socat",
                            f"TCP-LISTEN:{self._vc_port},fork,reuseaddr",
                            f"UNIX-CONNECT:{vc_sock}",
                        ],
                        stdout=f,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                        preexec_fn=_set_pdeathsig,
                    )
                )
            # Wait for all LLM listeners to create their sockets before the
            # sandbox's socats try to connect, so there is no startup race.
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if all(os.path.exists(s) for s in sock_paths):
                    break
                if any(p.poll() is not None for p in procs):
                    break
                time.sleep(0.05)
            if any(p.poll() is not None for p in procs):
                detail = ""
                logs = [os.path.join(net_dir, f"host-llm-{p}.log") for p in self._ports] + [vc_log]
                try:
                    for lp in logs:
                        if os.path.exists(lp):
                            with open(lp, encoding="utf-8") as f:
                                tail = f.read().strip()
                            if tail:
                                detail = f": {tail}"
                                break
                except OSError:
                    pass
                raise RuntimeError(f"host socat for sandbox bridge exited early{detail}")
            if not all(os.path.exists(s) for s in sock_paths):
                missing = ", ".join(s for s in sock_paths if not os.path.exists(s))
                raise RuntimeError(f"host socat did not create socket(s) {missing}")
        except BaseException:
            shutil.rmtree(net_dir, ignore_errors=True)
            raise
        return net_dir, procs

    def _stop_bridge(self, net_dir: str, procs: Sequence[subprocess.Popen[Any]]) -> None:
        """Tear down the host-side bridges and remove their socket dir."""
        for proc in procs:
            # Each socat is its own session/process-group leader
            # (start_new_session in _start_bridge), so its pgid equals its pid;
            # signal the whole group to also reach any processes forked by
            # socat's `,fork`.
            try:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    # The process group may already be gone (abnormal exit).
                    proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
                proc.wait()
        shutil.rmtree(net_dir, ignore_errors=True)

    def _workspace_mounts(self, workspace: Workspace) -> list[str]:
        """Build the mount list for a tkt workspace.

        The agent directory is the only writable location in the workspace;
        main workspace, per-package ``.git`` dirs are writable, and externals
        are read-only.
        """
        mounts: list[str] = []
        mounts += ["--ro-bind", workspace.directory, workspace.directory]
        agent_dir = os.path.join(workspace.directory, AGENT_SUBDIR)
        mounts += ["--bind", agent_dir, agent_dir]
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
        return mounts

    def _single_repo_mounts(self, repo_dir: str) -> list[str]:
        """Build the mount list for a single repository.

        The whole repository is writable by the agent; no per-package worktrees
        are involved.
        """
        return ["--bind", repo_dir, repo_dir]

    def _build_bwrap_argv(
        self,
        workspace: Workspace,
        *,
        shell: bool,
        command: str | None = None,
        network: bool = True,
        net_dir: str | None = None,
        ports: Sequence[int] = _DEFAULT_PORTS,
        vc_port: int = _DEFAULT_VC_PORT,
    ) -> list[str]:
        home = os.path.expanduser("~")
        mounts = self._workspace_mounts(workspace)
        inner = self._build_inner_script(shell=shell, command=command)
        return self._build_common_argv(
            home=home,
            mounts=mounts,
            inner=inner,
            network=network,
            net_dir=net_dir,
            ports=ports,
            vc_port=vc_port,
        )

    def _build_single_repo_argv(
        self,
        repo_dir: str,
        *,
        shell: bool,
        conda_env: str | None = None,
        command: str | None = None,
        network: bool = True,
        net_dir: str | None = None,
        ports: Sequence[int] = _DEFAULT_PORTS,
        vc_port: int = _DEFAULT_VC_PORT,
    ) -> list[str]:
        home = os.path.expanduser("~")
        mounts = self._single_repo_mounts(repo_dir)
        inner = self._build_inner_script(shell=shell, conda_env=conda_env, repo_dir=repo_dir, command=command)
        return self._build_common_argv(
            home=home,
            mounts=mounts,
            inner=inner,
            network=network,
            net_dir=net_dir,
            ports=ports,
            vc_port=vc_port,
        )

    def warm_holder_argv(
        self,
        *,
        workspace: Workspace | None = None,
        repo_dir: str | None = None,
        inner: str,
        network: bool = False,
    ) -> list[str]:
        """Build a bwrap argv for a long-lived warm holder running ``inner``.

        Exactly one of ``workspace`` or ``repo_dir`` must be given. The warm
        holder uses the same mount model as the corresponding sandbox mode but
        runs the provided driver script ``inner`` instead of a fixed command,
        and is network-restricted with no LLM bridge.
        """
        home = os.path.expanduser("~")
        if workspace is not None and repo_dir is None:
            mounts = self._workspace_mounts(workspace)
        elif repo_dir is not None and workspace is None:
            mounts = self._single_repo_mounts(repo_dir)
        else:
            raise ValueError("Exactly one of workspace or repo_dir must be provided.")
        return self._build_common_argv(
            home=home,
            mounts=mounts,
            inner=inner,
            network=network,
            bridge_llm=False,
        )

    def _build_common_argv(
        self,
        *,
        home: str,
        mounts: list[str],
        inner: str,
        network: bool = True,
        net_dir: str | None = None,
        ports: Sequence[int] = _DEFAULT_PORTS,
        vc_port: int = _DEFAULT_VC_PORT,
        bridge_llm: bool = True,
    ) -> list[str]:
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
        if not network and bridge_llm:
            if net_dir is None or not os.path.isdir(net_dir):
                raise ValueError("restricted network requires a valid net_dir socket directory")
            # Share the bridge socket directory read-write with the sandbox.
            argv += ["--bind", net_dir, net_dir]
            # Start one in-sandbox socat per bridged port so the agent's
            # localhost:<port> reaches the host bridge, plus one for the host's
            # localhost:<vc_port> companion server.  Background them (the inner
            # script ends with `exec`), and redirect logs into the shared
            # net_dir.
            for p in ports:
                sock_path = shlex.quote(os.path.join(net_dir, f"llm-{p}.sock"))
                log_path = shlex.quote(os.path.join(net_dir, f"sandbox-llm-{p}.log"))
                inner = (
                    f"socat TCP4-LISTEN:{p},fork,reuseaddr "
                    f"UNIX-CONNECT:{sock_path} >{log_path} 2>&1 &\n" + inner
                )
            vc_sock_path = shlex.quote(os.path.join(net_dir, "vc.sock"))
            vc_log_path = shlex.quote(os.path.join(net_dir, "sandbox-vc-socat.log"))
            inner = (
                f"socat UNIX-LISTEN:{vc_sock_path},fork "
                f"TCP4:127.0.0.1:{vc_port} >{vc_log_path} 2>&1 &\n" + inner
            )
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
        # Namespaces and cleanup.  Network access is isolated by default: only
        # the bridged localhost port is reachable from the agent.  When full
        # network access is requested we deliberately omit --unshare-net so the
        # sandbox shares the host's network namespace.
        argv += [
            "--unshare-user",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup-try",
            "--die-with-parent",
        ]
        if not network:
            argv += ["--unshare-net"]
        argv += ["--"]
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
        if repo_dir is None:
            # in workspace mode, set up the .agent tree
            lines += ["exec", "setup -r .agent"]
        elif os.path.isdir(os.path.join(repo_dir, "ups")):
            # in standalone mode, if there is a ups directory, set that up.
            lines += ["exec", "setup -r ."]
        if shell:
            lines.append("exec /bin/bash --login -i")
        elif command is not None:
            lines.append(f"exec {shlex.join(shlex.split(command))}")
        else:
            lines.append(f"exec {shlex.join(self._command)}")
        return "\n".join(lines)
