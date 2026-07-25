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
import shutil
from collections.abc import Iterable, Mapping

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
    ):
        self.ticket = ticket
        self.metapackage_name = metapackage_name
        self.metapackage_tag = metapackage_tag
        self._directory = directory
        self._packages = packages
        self._externals = externals
        self._workspace_eups_product = workspace_eups_product
        self._tools = tuple(tools)

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
        )

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
                directory = os.path.curdir
                while not os.path.exists(os.path.join(directory, "tkt.json")):
                    new_directory = os.path.normpath(os.path.join(directory, ".."))
                    if new_directory == directory:
                        raise RuntimeError(
                            "No ticket or directory provided, and no tkt.json found "
                            "in current or its parents."
                        )
                    directory = new_directory
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
        )
        instance._write_new(environment, dry_run=dry_run)
        return instance

    def update(
        self,
        packages: Iterable[str],
        *,
        externals: Mapping[str, str] | None = None,
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
        for package in packages:
            self._checkout_package(package, environment, dry_run=dry_run)
        if not dry_run:
            self._write_description()
            self._write_eups_table()
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
            self._write_eups_table()
            self._write_tools(environment)

    def remove(self) -> None:
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
            self._write_eups_table()
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
                },
                f,
                indent=2,
            )

    def _write_eups_table(self) -> None:
        os.makedirs(os.path.join(self._directory, "ups"), exist_ok=True)
        with open(
            os.path.join(self._directory, "ups", f"{self._workspace_eups_product}.table"),
            "w",
        ) as f:
            f.write(f"setupRequired({self.metapackage_name} -t {self.metapackage_tag})\n")
            for product, path in self._externals.items():
                f.write(f"setupRequired({product} -j -r {path})\n")
            for product in self._packages:
                path = os.path.join(self._directory, product, "ups")
                if os.path.exists(path):
                    f.write(f"setupRequired({product} -j -r ${{PRODUCT_DIR}}/{product})\n")
                else:
                    logging.info(f"Skipping setup line for {product} because {path} does not exist.")

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
