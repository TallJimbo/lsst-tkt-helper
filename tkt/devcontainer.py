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

__all__ = ("DevContainer",)

import copy
import os
from collections.abc import Iterable
from typing import Any

from ._environment import Environment, Tool
from ._workspace import Workspace
from .utils import format_dict, read_json_file, write_json_file


class DevContainer(Tool):
    """Tool specialization for devcontainer."""

    def __init__(self, base: dict[str, Any], packages: dict[str, Any]):
        self._base = base
        self._packages = packages

    @classmethod
    def from_json_data(cls, data: dict[str, Any]) -> Tool:
        base = data.pop("base", {})
        packages = data.pop("packages", {})
        if data:
            raise ValueError(f"Unexpected entries in nested devcontainer configuration: {data}.")
        return cls(base, packages)

    def write(
        self,
        ticket: str,
        directory: str,
        packages: Iterable[str],
        workspace: Workspace,
        environment: Environment,
    ) -> None:
        config_filename = os.path.join(directory, ".devcontainer", "devcontainer.json")
        config = copy.deepcopy(self._base)
        if os.path.exists(config_filename):
            config = read_json_file(config_filename, target=config, warn_on_conflict=False)
        config = format_dict(config, workspace=workspace, environment=environment)
        if config:
            os.makedirs(os.path.join(directory, ".devcontainer"), exist_ok=True)
            write_json_file(config, config_filename)
