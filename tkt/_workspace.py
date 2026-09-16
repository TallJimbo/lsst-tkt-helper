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

__all__ = ("Workspace",)

import json
import logging
import os
import re
import shutil
from collections.abc import Callable, Iterable, Mapping

import git

from ._environment import Environment


class Workspace:
    def __init__(
        self,
        *,
        ticket: str,
        directory: str,
        metapackage_name: str,
        metapackage_tag: str,
        packages: dict[str, str],
        externals: dict[str, str],
        workspace_eups_product: str,
        tools: Iterable[str],
        shared_worktree: bool = False,
    ):
        self.ticket = ticket
        self.metapackage_name = metapackage_name
        self.metapackage_tag = metapackage_tag
        self._directory = directory
        self._packages = packages
        self._externals = externals
        self._workspace_eups_product = workspace_eups_product
        self._tools = tuple(tools)
        self._shared_worktree = bool(shared_worktree)

    @property
    def directory(self) -> str:
        """Absolute path to the workspace directory."""
        return self._directory

    @property
    def packages(self) -> Mapping[str, str]:
        """Mapping from package name to branch for each cloned package."""
        return self._packages

    @property
    def externals(self) -> Mapping[str, str]:
        """Mapping from package name to filesystem path for external
        packages that are referenced by the workspace's EUPS table without
        being cloned into the workspace directory.
        """
        return self._externals

    @property
    def workspace_eups_product(self) -> str:
        """Name of the EUPS product representing the workspace as a whole."""
        return self._workspace_eups_product

    @property
    def tools(self) -> tuple[str, ...]:
        """Names of the `Tool` objects configured for this workspace."""
        return self._tools

    @property
    def shared_worktree(self) -> bool:
        """Whether the agent works directly in the human's worktrees.

        When ``True``, the sandbox gives the agent read-write access to the
        package directories themselves (on the human's ticket branches)
        instead of creating per-package worktrees under ``.agent/``.
        """
        return self._shared_worktree

    def remove_tools(self, tools: Iterable[str]) -> None:
        """Remove tool names from this workspace's configured set."""
        remove = set(tools)
        self._tools = tuple(t for t in self._tools if t not in remove)

    @classmethod
    def from_directory(cls, directory: str) -> Workspace:
        directory = os.path.abspath(directory)
        with open(os.path.join(directory, "tkt.json")) as f:
            data = json.load(f)
        if "tag" in data:
            metapackage_name = data["metapackage"]
            metapackage_tag = data["tag"]
        else:
            metapackage_name = data["metapackage_name"]
            metapackage_tag = data.get("metapackage_tag", "")
        return cls(
            directory=directory,
            ticket=data["ticket"],
            packages=dict(data["packages"]),
            externals=dict(data["externals"]),
            metapackage_name=metapackage_name,
            metapackage_tag=metapackage_tag,
            workspace_eups_product=data["workspace_eups_product"],
            tools=data["tools"],
            shared_worktree=data.get("shared_worktree", False),
        )

    @staticmethod
    def find_directory(start: str = ".") -> str:
        """Search up from ``start`` for the nearest directory with tkt.json."""
        directory = os.path.abspath(start)
        while not os.path.exists(os.path.join(directory, "tkt.json")):
            parent = os.path.dirname(directory)
            if parent == directory:
                raise RuntimeError(
                    "No ticket or directory provided, and no tkt.json found in current or its parents."
                )
            directory = parent
        return directory

    @classmethod
    def from_existing(
        cls,
        *,
        ticket: str | None,
        directory: str | None,
        environment: Environment,
    ) -> Workspace:
        if directory is None:
            if ticket is not None:
                directory = environment.get_workspace_directory(ticket)
            else:
                directory = cls.find_directory()
        return cls.from_directory(directory)

    @classmethod
    def new(
        cls,
        ticket: str,
        packages: Iterable[str],
        *,
        directory: str | None = None,
        externals: Mapping[str, str] | None = None,
        metapackage: str | None = None,
        tag: str | None = None,
        workspace_eups_product: str | None = None,
        environment: Environment,
        tools: Iterable[str] = (),
        shared_worktree: bool = False,
        dry_run: bool = False,
    ) -> Workspace:
        packages, externals, environment = cls._handle_package_args(
            ticket,
            packages=packages,
            externals=externals,
            environment=environment,
        )
        if directory is None:
            directory = environment.get_workspace_directory(ticket)
        directory = os.path.abspath(directory)
        if metapackage is None:
            metapackage = environment.default_metapackage
        if tag is None:
            tag = environment.default_tag
        if workspace_eups_product is None:
            workspace_eups_product = environment.default_workspace_eups_product
        instance = cls(
            directory=directory,
            ticket=ticket,
            packages=packages,
            externals=externals,
            metapackage_name=metapackage,
            metapackage_tag=tag,
            workspace_eups_product=workspace_eups_product,
            tools=tools,
            shared_worktree=shared_worktree,
        )
        instance._write_new(environment, dry_run=dry_run)
        return instance

    def update(
        self,
        packages: Iterable[str],
        *,
        externals: Mapping[str, str] | None = None,
        tools: Iterable[str] = (),
        environment: Environment,
        dry_run: bool = False,
    ) -> None:
        packages, externals, environment = self._handle_package_args(
            self.ticket,
            packages=packages,
            externals=externals,
            environment=environment,
        )
        self._packages.update(packages)
        self._externals.update(externals)
        for tool in tools:
            if tool not in self._tools:
                self._tools = (*self._tools, tool)
        for package in packages:
            self._checkout_package(package, environment, dry_run=dry_run)
        if not dry_run:
            self._write_description()
            self._write_eups_table(environment)
            self._write_tools(environment)

    def upgrade_metapackage(
        self,
        *,
        metapackage: str | None = None,
        tag: str | None = None,
        environment: Environment,
        dry_run: bool = False,
    ) -> None:
        if metapackage is not None:
            self.metapackage_name = metapackage
            logging.info(f"Changing EUPS base metapackage to {metapackage}.")
        if tag is not None:
            self.metapackage_tag = tag
            logging.info(f"Changing EUPS base tag to {tag}.")
        if not dry_run:
            self._write_description()
            self._write_eups_table(environment)
            self._write_tools(environment)

    def remove_packages(
        self,
        packages: Iterable[str],
        *,
        environment: Environment,
        dry_run: bool = False,
        force: bool = False,
        confirm: Callable[[str], bool] | None = None,
    ) -> list[str]:
        """Remove package(s) from this workspace.

        Cloned packages are removed from disk (after their ``.agent`` sandbox
        worktree is removed); externals only lose their EUPS table line.
        All removed packages are dropped from ``tkt.json``, the workspace
        EUPS table and the tool configs.

        Unless ``force`` is set, removal of a clone is refused if it is
        still EUPS-setup in place in the caller's shell (EUPS cannot
        resolve or unsetup a product whose directory has been deleted),
        and ``confirm`` (e.g. ``click.confirm``) is asked before deleting
        a clone with unsaved work; without a ``confirm`` callable such
        packages are skipped.  Returns the names actually removed.
        """
        removed: list[str] = []
        for package in packages:
            if package in self._externals:
                logging.info(f"{package}: dropping external {self._externals[package]} from the EUPS table.")
                if not dry_run:
                    del self._externals[package]
                removed.append(package)
                continue
            if package not in self._packages:
                raise KeyError(f"{package} is not a package or external of this workspace.")
            pkg_dir = os.path.join(self._directory, package)
            if not force:
                if self.is_setup_in_place(package, pkg_dir):
                    raise RuntimeError(
                        f"{package} is still EUPS-setup in place from this workspace. "
                        f"Use the 'tkt-rm-package' shell function, or run "
                        f"'unsetup -j {package}' first, or pass --force."
                    )
                warnings = self._package_work_warnings(package)
                if warnings and (confirm is None or not confirm("; ".join(warnings) + ". Remove?")):
                    logging.warning(f"{package}: removal declined; skipping.")
                    continue
            self._remove_agent_worktree(package, dry_run=dry_run)
            logging.info(f"{package}: removing {pkg_dir}.")
            if not dry_run:
                if os.path.exists(pkg_dir):
                    shutil.rmtree(pkg_dir)
                del self._packages[package]
            removed.append(package)
        if removed and not dry_run:
            self._write_description()
            self._write_eups_table(environment)
            self._write_tools(environment)
        return removed

    @staticmethod
    def _eups_env_prefix(product: str) -> str:
        """Return the EUPS variable-name identifier for ``product``."""
        return re.sub(r"[^A-Z0-9]", "_", product.upper())

    def is_setup_in_place(self, product: str, directory: str) -> bool:
        """Report whether ``product`` is setup from ``directory``.

        Checks the caller's shell environment: EUPS records setup state in
        exported variables such as ``SETUP_<PRODUCT>`` and ``<PRODUCT>_DIR``,
        which a subprocess inherits but cannot modify.
        """
        prefix = self._eups_env_prefix(product)
        if os.environ.get(f"{prefix}_DIR") == directory:
            return True
        value = os.environ.get(f"SETUP_{prefix}")
        if value is None:
            return False
        tokens = value.split()
        return directory in tokens or f"LOCAL:{directory}" in tokens

    def _package_work_warnings(self, package: str) -> list[str]:
        """Return warnings about work that removing ``package`` would lose."""
        warnings: list[str] = []
        pkg_dir = os.path.join(self._directory, package)
        agent_dir = os.path.join(self._directory, ".agent", package)
        if os.path.isdir(pkg_dir):
            try:
                repo = git.Repo(pkg_dir)
            except git.InvalidGitRepositoryError:
                repo = None
            if repo is None:
                warnings.append(f"the {package} directory is not a git repository")
            else:
                if repo.is_dirty(untracked_files=True):
                    warnings.append(f"{package} has uncommitted changes")
                branch = self._packages[package]
                try:
                    unpushed = int(repo.git.rev_list("--count", branch, "--not", "--remotes").strip())
                except git.GitCommandError:
                    unpushed = -1  # e.g. branch or remote refs missing; treat as unsaved
                if unpushed > 0:
                    warnings.append(f"{package} has {unpushed} commit(s) not on any remote")
                elif unpushed < 0:
                    warnings.append(f"{package} has commits that could not be checked against remotes")
        if os.path.isdir(agent_dir):
            try:
                if git.Repo(agent_dir).is_dirty(untracked_files=True):
                    warnings.append(
                        "the agent worktree has uncommitted changes "
                        "(run tkt pull-sandbox or sandbox-reset first)"
                    )
            except (git.GitCommandError, git.InvalidGitRepositoryError):
                warnings.append("the agent worktree could not be checked for uncommitted changes")
        return warnings

    def _remove_agent_worktree(self, package: str, *, dry_run: bool = False) -> None:
        """Remove the sandbox worktree at ``.agent/<package>`` if present."""
        pkg_dir = os.path.join(self._directory, package)
        agent_dir = os.path.join(self._directory, ".agent", package)
        if not os.path.isdir(agent_dir):
            return
        logging.info(f"{package}: removing agent worktree at {agent_dir}.")
        if dry_run:
            return
        if os.path.isdir(pkg_dir):
            try:
                git.Repo(pkg_dir).git.worktree("remove", "--force", agent_dir)
                return
            except (git.GitCommandError, git.InvalidGitRepositoryError):
                logging.info(f"{package}: git worktree remove failed; removing the directory.")
        shutil.rmtree(agent_dir, ignore_errors=True)
        if os.path.isdir(pkg_dir):
            try:
                git.Repo(pkg_dir).git.worktree("prune")
            except (git.GitCommandError, git.InvalidGitRepositoryError):
                pass

    def remove(self, environment: Environment) -> None:
        """Delete the workspace, letting each configured tool clean up first.

        Tools' ``remove`` hooks run before the directory tree is deleted so
        they can deregister state kept outside the workspace (e.g. git
        worktree registrations in shared repositories).
        """
        for name in self._tools:
            tool = environment.get_tool(name)
            if tool is not None:
                tool.remove(self._directory)
        shutil.rmtree(self._directory)

    @staticmethod
    def _handle_package_args(
        ticket: str,
        *,
        packages: Iterable[str],
        externals: Mapping[str, str] | None = None,
        environment: Environment,
    ) -> tuple[dict[str, str], dict[str, str], Environment]:
        if externals is None:
            externals = {}
        else:
            externals = dict(externals)
        packages_dict = {}
        for package in packages:
            package_external_path = environment.get_external_path(package)
            if package_external_path is not None:
                externals[package] = package_external_path
            else:
                packages_dict[package] = environment.get_default_branch(package, ticket)
        return (packages_dict, externals, environment)

    def _write_new(self, environment: Environment, *, dry_run: bool) -> None:
        if os.path.exists(self._directory):
            logging.info(f"Using existing workspace directory {self._directory}.")
        else:
            logging.info(f"Creating workspace directory {self._directory}.")
            if not dry_run:
                os.makedirs(self._directory)
        if not dry_run:
            self._write_description()
        for package in self._packages:
            self._checkout_package(package, environment, dry_run=dry_run)
        if not dry_run:
            self._write_eups_table(environment)
            self._write_tools(environment)

    def _write_description(self) -> None:
        with open(os.path.join(self._directory, "tkt.json"), "w") as f:
            json.dump(
                {
                    "ticket": self.ticket,
                    "packages": dict(self._packages),
                    "externals": dict(self._externals),
                    "metapackage_name": self.metapackage_name,
                    "metapackage_tag": self.metapackage_tag,
                    "workspace_eups_product": self._workspace_eups_product,
                    "tools": list(self._tools),
                    "shared_worktree": self._shared_worktree,
                },
                f,
                indent=2,
            )

    def _write_eups_table(self, environment: Environment) -> None:
        os.makedirs(os.path.join(self._directory, "ups"), exist_ok=True)
        with open(
            os.path.join(self._directory, "ups", f"{self._workspace_eups_product}.table"),
            "w",
        ) as f:
            tkt_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            f.write(f"envSet(TAB_TITLE, {self.ticket})\n")
            f.write(f"setupRequired(tkt -r {tkt_dir})\n")
            f.write(f"setupRequired({self.metapackage_name} -t {self.metapackage_tag})\n")
            for product, path in self._externals.items():
                f.write(f"setupRequired({product} -j -r {path})\n")
            for product in self._packages:
                path = os.path.join(self._directory, product, "ups")
                if os.path.exists(path):
                    f.write(f"setupRequired({product} -j -r ${{PRODUCT_DIR}}/{product})\n")
                else:
                    logging.info(f"Skipping setup line for {product} because {path} does not exist.")
            for name in self._tools:
                tool = environment.get_tool(name)
                if tool is not None:
                    for line in tool.eups_env_lines(self.ticket):
                        f.write(line + "\n")

    def _write_tools(self, environment: Environment) -> None:
        for name in self._tools:
            tool = environment.get_tool(name)
            if tool is None:
                raise LookupError(f"No editor configuration for {name}.")
            tool.write(self.ticket, self._directory, self._packages.keys(), self, environment)

    def _checkout_package(self, package: str, environment: Environment, *, dry_run: bool) -> None:
        branch_name = self._packages[package]
        package_dir = os.path.join(self._directory, package)
        if os.path.exists(package_dir):
            repo = git.Repo(package_dir)
        else:
            origin_url = environment.get_origin(package)
            logging.info(f"{package}: cloning from {origin_url}.")
            if not dry_run:
                repo = git.Repo.clone_from(origin_url, package_dir)
            else:
                repo = None
        if repo is None:
            logging.info(f"{package}: (cannot determine {branch_name} checkout action in dry run).")
        elif repo.active_branch != branch_name:
            if branch_name in repo.heads:
                logging.info(f"{package}: checking out existing local branch {branch_name}.")
                if not dry_run:
                    repo.heads[branch_name].checkout()
            else:
                remotes_with_branch = [remote for remote in repo.remotes if branch_name in remote.refs]
                if len(remotes_with_branch) == 1:
                    logging.info(
                        f"{package}: creating local branch {branch_name} tracking {remotes_with_branch[0]}."
                    )
                    if not dry_run:
                        upstream = remotes_with_branch[0].refs[branch_name]
                        local = repo.create_head(branch_name, upstream.commit)
                        assert isinstance(local, git.Head)
                        local.set_tracking_branch(upstream)
                        local.checkout()
                elif not remotes_with_branch:
                    logging.info(f"{package}: creating new local branch {branch_name}.")
                    if not dry_run:
                        local = repo.create_head(branch_name)
                        assert isinstance(local, git.Head)
                        local.checkout()
                else:
                    logging.warning(
                        f"{package}: {branch_name} found in multiple remotes; not checking out any of them."
                    )
