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

__all__ = ("Superpowers",)

import os
from collections.abc import Iterable
from typing import Any

from ._environment import Environment, Tool
from ._workspace import Workspace


class Superpowers(Tool):
    """Tool that gives a workspace a shared superpowers docs home.

    ``write`` creates the per-ticket namespace ``<path>/<ticket>/specs`` and
    ``<path>/<ticket>/plans`` in the shared repo so the ticket has a
    ready-to-use docs location.  The workspace EUPS table sets
    ``SUPERPOWERS_DIR`` to ``<path>/<ticket>``, which the superpowers skills
    read.
    """

    def __init__(self, path: str):
        self.path = path

    @classmethod
    def from_json_data(cls, data: dict[str, Any]) -> Tool:
        path = data.pop("path")
        if data:
            raise ValueError(f"Unexpected entries in superpowers configuration: {data}.")
        return cls(path)

    def write(
        self,
        ticket: str,
        directory: str,
        packages: Iterable[str],
        workspace: Workspace,
        environment: Environment,
    ) -> None:
        namespace = os.path.join(self.path, ticket)
        os.makedirs(os.path.join(namespace, "specs"), exist_ok=True)
        os.makedirs(os.path.join(namespace, "plans"), exist_ok=True)

    def eups_env_lines(self, ticket: str) -> Iterable[str]:
        return (f"envSet(SUPERPOWERS_DIR, {self.path}/{ticket})",)
