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

__all__ = ("Pyright",)

import copy
import logging
import os
from collections.abc import Iterable
from typing import Any

from ._environment import Environment, Tool
from ._workspace import Workspace
from .utils import read_json_file, write_json_file


class Pyright(Tool):
    """Tool specialization for PyRight."""

    def __init__(self, base: dict[str, Any], packages: dict[str, Any]):
        self._base = base
        self._packages = packages

    @classmethod
    def from_json_data(cls, data: dict[str, Any]) -> Tool:
        base = read_json_file(os.path.join(os.path.dirname(__file__), "..", "pyrightconfig.json"))
        packages = data.pop("packages", {})
        if data:
            raise ValueError(f"Unexpected entries in nested pyright configuration: {data}.")
        return cls(base, packages)

    def write(
        self,
        ticket: str,
        directory: str,
        packages: Iterable[str],
        workspace: Workspace,
        environment: Environment,
    ) -> None:
        workspace_filename = os.path.join(directory, "pyrightconfig.json")
        config = copy.deepcopy(self._base)
        if os.path.exists(workspace_filename):
            config = read_json_file(workspace_filename, target=config)
        write_json_file(config, workspace_filename)
        for package in packages:
            package_config = self._packages.get(package)
            package_config_filename = os.path.join(directory, package, "pyrightconfig.json")
            if package_config is not None:
                package_config = copy.deepcopy(package_config)
                if os.path.exists(package_config_filename):
                    package_config = read_json_file(package_config_filename, target=package_config)
                write_json_file(package_config, package_config_filename)
            elif os.path.exists(package_config_filename):
                logging.warning(
                    f"{package_config_filename} exists, but no package-override configuration was present."
                )
