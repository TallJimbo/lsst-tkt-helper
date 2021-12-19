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

__all__ = ("VSCode",)

import copy
import json
import os
import sys
from typing import Any, Dict, Iterable, Optional

from ._environment import Editor

BASE_EXPORTED_VARIABLES = frozenset(("MYPYPATH", "PATH", "PYTHONPATH", "LD_LIBRARY_PATH"))


def merge_hierarchical(target: Dict[str, Any], source: Dict[str, Any], override: bool = False) -> None:
    """Merge ``source`` into ``target``, combining dictionaries recursively
    when the same keys are present.  Modifies ``target`` in-place.
    """
    for k, v in source.items():
        d = target.setdefault(k, v)
        if d is not v:
            if isinstance(d, dict) and isinstance(v, dict):
                merge_hierarchical(d, v, override=override)
            elif override:
                target[k] = v
            elif d != v:
                raise ValueError(f"Cannot merge {k!r}: {v!r} into {d!r}.")


class VSCode(Editor):
    def __init__(
        self,
        base: Dict[str, Any],
        packages: Dict[str, Any],
    ):
        self._base = base
        self._packages = packages

    @classmethod
    def from_json_data(cls, data: Dict[str, Any]) -> Editor:
        base = data.pop("base", {})
        packages = data.pop("packages", {})
        if data:
            raise ValueError(f"Unexpected entries in nested VSCode configuration: {data}.")
        return cls(base, packages)

    @property
    def needs_envvars(self) -> bool:
        return True

    def write(
        self,
        ticket: str,
        directory: str,
        packages: Iterable[str],
        envvars: Optional[Dict[str, Any]] = None,
    ) -> None:
        workspace_filename = os.path.join(directory, f"{ticket}.code-workspace")
        config = copy.deepcopy(self._base)
        if os.path.exists(workspace_filename):
            with open(workspace_filename, "r") as f:
                old_config = json.load(f)
            merge_hierarchical(config, old_config, override=True)
        folders_list = config.setdefault("folders", [])
        for package in packages:
            for folder_config in folders_list:
                if folder_config.get("path") == package:
                    break
            else:
                folder_config = {"path": package}
                folders_list.append(folder_config)
            package_config = self._packages.get(package)
            if package_config is not None:
                merge_hierarchical(folder_config, copy.deepcopy(package_config))
        config.setdefault("settings", {})["python.pythonPath"] = sys.executable
        if envvars is not None and "PYTHONPATH" in envvars:
            config["settings"]["python.analysis.extraPaths"] = envvars["PYTHONPATH"].split(":")
        with open(workspace_filename, "w") as f:
            json.dump(config, f, indent=2)
        if envvars is not None:
            exported_variables = list(BASE_EXPORTED_VARIABLES.intersection(envvars.keys()))
            exported_variables.extend(
                var for var in envvars if (var.endswith("DIR") and f"SETUP_{var[:-4]}" in envvars)
            )
            with open(os.path.join(directory, ".env"), "w") as f:
                for var in exported_variables:
                    f.write(f"{var}={envvars[var]}\n")
