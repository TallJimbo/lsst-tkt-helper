# Copyright 2020 Jim Bosch
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
import subprocess
from typing import (
    Dict,
    Iterable,
    Mapping,
    Optional,
    Tuple,
)

import eups
import git

from ._environment import Environment


class Workspace:
    def __init__(
        self,
        *,
        directory: str,
        ticket: str,
        packages: Dict[str, str],
        externals: Dict[str, str],
        metapackage_name: str,
        metapackage_version: str,
        workspace_eups_product: str,
        editors: Iterable[str],
    ):
        self._directory = directory
        self._ticket = ticket
        self._packages = packages
        self._externals = externals
        self._metapackage_name = metapackage_name
        self._metapackage_version = metapackage_version
        self._workspace_eups_product = workspace_eups_product
        self._editors = tuple(editors)

    @classmethod
    def from_directory(cls, directory: str) -> Workspace:
        with open(os.path.join(directory, "tkt.json"), "r") as f:
            data = json.load(f)
        if "tag" in data:
            metapackage_name = data["metapackage"]
            metapackage_version = eups.Eups().findProduct(metapackage_name, eups.Tag(data["tag"])).version
        else:
            metapackage_name = data["metapackage_name"]
            metapackage_version = data["metapackage_version"]
        return cls(
            directory=directory,
            ticket=data["ticket"],
            packages=dict(data["packages"]),
            externals=dict(data["externals"]),
            metapackage_name=metapackage_name,
            metapackage_version=metapackage_version,
            workspace_eups_product=data["workspace_eups_product"],
            editors=data["editors"],
        )

    @classmethod
    def from_existing(
        cls,
        *,
        ticket: Optional[str],
        directory: Optional[str],
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
        directory: Optional[str] = None,
        branches: Optional[Mapping[str, str]] = None,
        externals: Optional[Mapping[str, str]] = None,
        metapackage: Optional[str] = None,
        tag: Optional[str] = None,
        workspace_eups_product: Optional[str] = None,
        environment: Optional[Environment] = None,
        editors: Iterable[str] = (),
        dry_run: bool = False,
    ) -> Workspace:
        packages, externals, environment = cls._handle_package_args(
            ticket,
            packages=packages,
            branches=branches,
            externals=externals,
            environment=environment,
        )
        if directory is None:
            directory = environment.get_workspace_directory(ticket)
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
            metapackage_version=eups.Eups().findProduct(metapackage, eups.Tag(tag)).version,
            workspace_eups_product=workspace_eups_product,
            editors=editors,
        )
        instance._write_new(environment, dry_run=dry_run)
        return instance

    def update(
        self,
        packages: Iterable[str],
        *,
        branches: Optional[Mapping[str, str]] = None,
        externals: Optional[Mapping[str, str]] = None,
        environment: Optional[Environment] = None,
        dry_run: bool = False,
    ) -> None:
        packages, externals, environment = self._handle_package_args(
            self._ticket,
            packages=packages,
            branches=branches,
            externals=externals,
            environment=environment,
        )
        self._packages.update(packages)
        self._externals.update(externals)
        if not dry_run:
            self._write_description()
            self._write_eups_table()
            self._write_editors(environment)
        for package in packages:
            self._checkout_package(package, environment, dry_run=dry_run)

    def upgrade_metapackage(
        self,
        *,
        metapackage: Optional[str] = None,
        tag: Optional[str] = None,
        environment: Optional[Environment] = None,
        dry_run: bool = False,
    ) -> None:
        if environment is None:
            environment = Environment.minimal()
        if metapackage is not None:
            self._metapackage_name = metapackage
            logging.info(f"Changing EUPS base metapackage to {metapackage}.")
        if tag is not None:
            self._metapackage_version = eups.Eups().findProduct(self._metapackage_name, eups.Tag(tag)).version
            logging.info(f"Changing EUPS base tag to {tag} (version {self._metapackage_version}).")
        if not dry_run:
            self._write_description()
            self._write_eups_table()
            self._write_editors(environment)

    @staticmethod
    def _handle_package_args(
        ticket: str,
        *,
        packages: Iterable[str],
        branches: Optional[Mapping[str, str]] = None,
        externals: Optional[Mapping[str, str]] = None,
        environment: Optional[Environment] = None,
    ) -> Tuple[Dict[str, str], Dict[str, str], Environment]:
        if environment is None:
            environment = Environment.minimal()
        if branches is None:
            branches = {}
        if externals is None:
            externals = {}
        else:
            externals = dict(externals)
        packages_dict = {}
        for package in packages:
            if package in branches:
                packages_dict[package] = branches[package]
            else:
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
            self._write_eups_table()
        for package in self._packages:
            self._checkout_package(package, environment, dry_run=dry_run)
        if not dry_run:
            self._write_editors(environment)

    def _write_description(self) -> None:
        with open(os.path.join(self._directory, "tkt.json"), "w") as f:
            json.dump(
                {
                    "ticket": self._ticket,
                    "packages": dict(self._packages),
                    "externals": dict(self._externals),
                    "metapackage_name": self._metapackage_name,
                    "metapackage_version": self._metapackage_version,
                    "workspace_eups_product": self._workspace_eups_product,
                    "editors": list(self._editors),
                },
                f,
            )

    def _write_eups_table(self) -> None:
        os.makedirs(os.path.join(self._directory, "ups"), exist_ok=True)
        with open(
            os.path.join(self._directory, "ups", f"{self._workspace_eups_product}.table"),
            "w",
        ) as f:
            f.write(f"setupRequired({self._metapackage_name} {self._metapackage_version})\n")
            for product, path in self._externals.items():
                f.write(f"setupRequired({product} -j -r {path})\n")
            for product in self._packages:
                f.write(f"setupRequired({product} -j -r ${{PRODUCT_DIR}}/{product})\n")

    def _capture_env(self, environment: Environment) -> Dict[str, str]:
        sentinal_line = "######## BEGIN ENV ########"
        result = subprocess.run(
            environment.shell,
            input=(
                f"{environment.eups_prelude}\n"
                f"setup -r {self._directory}\n"
                f"echo '{sentinal_line}'\n"
                "env\n"
            ),
            capture_output=True,
            text=True,
            env={},  # type: ignore
        )
        sentinal_seen = False
        envvars: Dict[str, str] = {}
        for line in result.stdout.splitlines():
            if sentinal_seen:
                name, separator, value = line.partition("=")
                assert separator == "="
                envvars[name] = value
            elif line.startswith(sentinal_line):
                sentinal_seen = True
        return envvars

    def _write_editors(self, environment: Environment) -> None:
        envvars = None
        for name in self._editors:
            editor = environment.get_editor(name)
            if editor is None:
                raise LookupError("No editor configuration for {name}.")
            if editor.needs_envvars and envvars is None:
                envvars = self._capture_env(environment)
            editor.write(self._ticket, self._directory, self._packages.keys(), envvars=envvars)

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
                        local.set_tracking_branch(upstream)
                        local.checkout()
                elif not remotes_with_branch:
                    logging.info(f"{package}: creating new local branch {branch_name}.")
                    if not dry_run:
                        local = repo.create_head(branch_name)
                        local.checkout()
                else:
                    logging.warning(
                        f"{package}: {branch_name} found in multiple remotes; "
                        "not checking out any of them."
                    )
