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
from typing import (
    Dict,
    Iterable,
    Mapping,
    Optional,
)

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
        metapackage: str,
        tag: str,
        workspace_eups_product: str,
    ):
        self._directory = directory
        self._ticket = ticket
        self._packages = packages
        self._externals = externals
        self._metapackage = metapackage
        self._tag = tag
        self._workspace_eups_product = workspace_eups_product

    @classmethod
    def from_directory(cls, directory: str) -> Optional[Workspace]:
        if not os.path.exists(directory):
            return None
        with open(os.path.join(directory, "tkt.json"), "r") as f:
            descr = json.load(f)
        return cls(
            directory=directory,
            ticket=descr["ticket"],
            packages=dict(descr["packages"]),
            externals=dict(descr["externals"]),
            metapackage=descr["metapackage"],
            tag=descr["tag"],
            workspace_eups_product=descr["workspace_eups_product"],
        )

    @classmethod
    def from_ticket_directory(
        cls, ticket: str, environment: Environment
    ) -> Optional[Workspace]:
        return cls.from_directory(environment.get_workspace_directory(ticket))

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
        dry_run: bool = False,
    ) -> Workspace:
        if environment is None:
            environment = Environment.minimal()
        if directory is None:
            directory = environment.get_workspace_directory(ticket)
        if branches is None:
            branches = {}
        if externals is None:
            externals = {}
        else:
            externals = dict(externals)
        if metapackage is None:
            metapackage = environment.default_metapackage
        if tag is None:
            tag = environment.default_tag
        if workspace_eups_product is None:
            workspace_eups_product = environment.default_workspace_eups_product
        packages_dict = {}
        for package in packages:
            if package in branches:
                packages_dict[package] = branches[package]
            else:
                package_external_path = environment.get_external_path(package)
                if package_external_path is not None:
                    externals[package] = package_external_path
                else:
                    packages_dict[package] = environment.get_default_branch(
                        package, ticket
                    )
        instance = cls(
            directory=directory,
            ticket=ticket,
            packages=packages_dict,
            externals=externals,
            metapackage=metapackage,
            tag=tag,
            workspace_eups_product=workspace_eups_product,
        )
        instance._write_new(environment, dry_run=dry_run)
        return instance

    def _write_new(self, environment: Environment, *, dry_run: bool) -> None:
        if os.path.exists(self._directory):
            logging.info(f"Using existing workspace directory {self._directory}.")
        else:
            logging.info(f"Creating workspace directory {self._directory}.")
            if not dry_run:
                os.makedirs(self._directory)
        if not dry_run:
            self.write_description()
            self.write_eups_table()
        for package in self._packages:
            self._checkout_package(package, environment, dry_run=dry_run)

    def write_description(self) -> None:
        with open(os.path.join(self._directory, "tkt.json"), "w") as f:
            json.dump(
                {
                    "ticket": self._ticket,
                    "packages": dict(self._packages),
                    "externals": list(self._externals),
                    "metapackage": self._metapackage,
                    "tag": self._tag,
                    "workspace_eups_product": self._workspace_eups_product,
                },
                f,
            )

    def write_eups_table(self) -> None:
        os.makedirs(os.path.join(self._directory, "ups"), exist_ok=True)
        with open(
            os.path.join(
                self._directory, "ups", f"{self._workspace_eups_product}.table"
            ),
            "w",
        ) as f:
            f.write(f"setupRequired({self._metapackage} -t {self._tag})\n")
            for product, path in self._externals.items():
                f.write(f"setupRequired({product} -j -r {path})\n")
            for product in self._packages:
                f.write(f"setupRequired({product} -j -r ${{PRODUCT_DIR}}/{product})\n")

    def _checkout_package(
        self, package: str, environment: Environment, *, dry_run: bool
    ) -> None:
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
            logging.info(
                f"{package}: (cannot determine {branch_name} checkout action in dry run)."
            )
        elif repo.active_branch != branch_name:
            if branch_name in repo.heads:
                logging.info(
                    f"{package}: checking out existing local branch {branch_name}."
                )
                if not dry_run:
                    repo.heads[branch_name].checkout()
            else:
                remotes_with_branch = [
                    remote for remote in repo.remotes if branch_name in remote.refs
                ]
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
