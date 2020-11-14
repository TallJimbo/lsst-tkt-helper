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

__all__ = ("RubinEnvironment",)

import os
from typing import (
    Any,
    Dict,
    Mapping,
    Optional,
)

import yaml

from ._environment import Editor, Environment


class RubinEnvironment(Environment):
    def __init__(
        self,
        *,
        eups_path: str,
        workspace_path: str,
        repos_yaml: str,
        shell: str,
        eups_prelude: str,
        default_tag: str,
        externals: Mapping[str, str],
        editors: Mapping[str, Editor],
    ):
        self._eups_path = eups_path
        self._workspace_path = workspace_path
        with open(repos_yaml, "r") as f:
            self._repo_data = yaml.safe_load(f)
        self._shell = shell
        self._eups_prelude = eups_prelude
        self._externals = externals
        self._editors = editors
        self._default_tag = default_tag

    @classmethod
    def from_json_data(cls, data: Dict[str, Any]) -> Environment:
        return cls(
            eups_path=data["eups_path"],
            workspace_path=data["workspace_path"],
            repos_yaml=data["repos_yaml"],
            eups_prelude=data["eups_prelude"],
            shell=data.get("shell", "/bin/bash"),
            externals=data.get("externals", {}),
            editors=cls._read_editors(data),
            default_tag=data.get("default_tag", "current"),
        )

    @property
    def default_metapackage(self) -> str:
        return "lsst_distrib"

    @property
    def default_tag(self) -> str:
        return self._default_tag

    @property
    def shell(self) -> str:
        return self._shell

    @property
    def eups_prelude(self) -> str:
        return self._eups_prelude

    @property
    def default_workspace_eups_product(self) -> str:
        return "tkt_workspace"

    def get_default_branch(self, package: str, ticket: str) -> str:
        return f"tickets/{ticket}"

    def get_workspace_directory(self, ticket: str) -> str:
        return os.path.join(self._workspace_path, ticket)

    def get_origin(self, package: str) -> str:
        repo_entry = self._repo_data.get(package)
        if repo_entry is not None:
            if isinstance(repo_entry, str):
                return repo_entry
            else:
                return repo_entry["url"]
        else:
            raise ValueError(f"No origin found for package {package}.")

    def get_external_path(self, package: str) -> Optional[str]:
        return self._externals.get(package)

    def get_editor(self, name: str) -> Optional[Editor]:
        return self._editors.get(name)
