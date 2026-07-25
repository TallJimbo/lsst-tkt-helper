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

__all__ = ("PreCommit",)

import logging
import os
import shutil
import subprocess
from collections.abc import Iterable
from typing import Any

from ._environment import Environment, Tool
from ._workspace import Workspace


def _prek_available() -> bool:
    """Return True if the ``prek`` executable is on the PATH."""
    return shutil.which("prek") is not None


class PreCommit(Tool):
    """Tool that installs pre-commit or prek git hooks.

    For each package directory, this tool checks for the presence of
    ``.pre-commit-config.yaml`` or ``prek.toml`` and, if found,
    runs the appropriate ``install`` command to register git hooks.

    If ``prek`` is installed, it is used for both config file types
    (it is a drop-in replacement for ``pre-commit``).  Otherwise,
    ``pre-commit`` is used, but only for ``.pre-commit-config.yaml``
    (it cannot read ``prek.toml``).
    """

    @classmethod
    def from_json_data(cls, data: dict[str, Any]) -> Tool:
        if data:
            raise ValueError(f"Unexpected entries in pre-commit configuration: {data}.")
        return cls()

    def write(
        self,
        ticket: str,
        directory: str,
        packages: Iterable[str],
        workspace: Workspace,
        environment: Environment,
    ) -> None:
        self._process(packages, directory)

    def _process(
        self,
        packages: Iterable[str],
        directory: str,
    ) -> None:
        use_prek = _prek_available()
        for package in packages:
            package_dir = os.path.join(directory, package)
            if not os.path.exists(package_dir):
                logging.info(f"Skipping pre-commit for {package}: directory {package_dir} does not exist.")
                continue
            self._run_for_package(package_dir, package, use_prek)

    def _run_for_package(
        self,
        package_dir: str,
        package: str,
        use_prek: bool,
    ) -> None:
        pre_commit_config = os.path.join(package_dir, ".pre-commit-config.yaml")
        prek_config = os.path.join(package_dir, "prek.toml")
        has_pre_commit = os.path.exists(pre_commit_config)
        has_prek = os.path.exists(prek_config)
        if not has_pre_commit and not has_prek:
            return
        # Choose executable: prek if available, otherwise pre-commit.
        if use_prek:
            executable = "prek"
        else:
            if has_prek:
                raise RuntimeError(
                    f"Cannot configure hooks for {package}: prek.toml found but prek is not installed."
                )
            executable = "pre-commit"
        logging.info(f"Installing {executable} hooks for {package}.")
        result = subprocess.run(
            [executable, "install"],
            cwd=package_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            logging.warning(f"Failed to install {executable} hooks for {package}: {result.stderr.strip()}")
        elif result.stdout.strip():
            logging.debug(f"{executable} output for {package}: {result.stdout.strip()}")
