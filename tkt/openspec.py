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

__all__ = ("OpenSpec",)

import os
from collections.abc import Iterable
from typing import Any

from ._environment import Environment, Tool
from ._workspace import Workspace


class OpenSpec(Tool):
    """Tool that installs pre-commit or prek git hooks.

    For each package directory, this tool checks for the presence of
    ``.pre-commit-config.yaml`` or ``prek.toml`` and, if found,
    runs the appropriate ``install`` command to register git hooks.

    If ``prek`` is installed, it is used for both config file types
    (it is a drop-in replacement for ``pre-commit``).  Otherwise,
    ``pre-commit`` is used, but only for ``.pre-commit-config.yaml``
    (it cannot read ``prek.toml``).
    """

    def __init__(self, store: str):
        self.store = store

    @classmethod
    def from_json_data(cls, data: dict[str, Any]) -> Tool:
        store = data.pop("store")
        if data:
            raise ValueError(f"Unexpected entries in pre-commit configuration: {data}.")
        return cls(store)

    def write(
        self,
        ticket: str,
        directory: str,
        packages: Iterable[str],
        workspace: Workspace,
        environment: Environment,
    ) -> None:
        os.makedirs(os.path.join(directory, "openspec"), exist_ok=True)
        with open(os.path.join(directory, "openspec", "config.yaml"), "w") as stream:
            stream.write(f"store: {self.store}\n")
