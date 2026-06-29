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

__all__ = ("Zed",)

import copy
import logging
import os
from collections.abc import Iterable
from typing import Any

from ._environment import Environment, Tool
from ._workspace import Workspace
from .utils import read_json_file, write_json_file


class Zed(Tool):
    """Tool specialization for Zed."""

    def __init__(
        self,
        base: dict[str, Any],
        packages: dict[str, Any],
    ):
        self._base = base
        self._packages = packages

    @classmethod
    def from_json_data(cls, data: dict[str, Any]) -> Tool:
        base = data.pop("base", {})
        packages = data.pop("packages", {})
        if data:
            raise ValueError(f"Unexpected entries in nested VSCode configuration: {data}.")
        return cls(base, packages)

    def write(
        self,
        ticket: str,
        directory: str,
        packages: Iterable[str],
        workspace: Workspace,
        environment: Environment,
    ) -> None:
        workspace_filename = os.path.join(directory, ".zed", "settings.json")
        config = copy.deepcopy(self._base)
        if os.path.exists(workspace_filename):
            config = read_json_file(workspace_filename, target=config)
        if config:
            os.makedirs(os.path.join(directory, ".zed"), exist_ok=True)
            write_json_file(config, workspace_filename)
        for package in packages:
            package_config = copy.deepcopy(self._packages.get(package))
            package_config = self._infer_format_on_save(directory, package, package_config)
            package_config_filename = os.path.join(directory, package, ".zed", "settings.json")
            if package_config:
                os.makedirs(os.path.join(directory, package, ".zed"), exist_ok=True)
            if package_config is not None:
                if os.path.exists(package_config_filename):
                    package_config = read_json_file(package_config_filename, target=package_config)
                write_json_file(package_config, package_config_filename)
            elif os.path.exists(package_config_filename):
                logging.warning(
                    f"{package_config_filename} exists, but no package-override configuration was present."
                )

    def _infer_format_on_save(
        self, directory: str, package: str, package_config: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if package_config is not None and (
            "format_on_save" in package_config
            or "format_on_save" in package_config.get("languages", {}).get("Python", {})
        ):
            return package_config
        pyproject_toml_filename = os.path.join(directory, package, "pyproject.toml")
        if os.path.exists(pyproject_toml_filename):
            with open(pyproject_toml_filename) as stream:
                data = stream.read()
            if "[tool.ruff.format]" in data:
                return package_config
        # Package has no pyproject.toml or no ruff.format config with in it;
        # better turn off format-on-save to avoid accidentally reformatting
        # unmodified code.
        if package_config is None:
            package_config = {}
        package_config.setdefault("languages", {}).setdefault("Python", {}).setdefault(
            "format_on_save", "off"
        )
        return package_config
