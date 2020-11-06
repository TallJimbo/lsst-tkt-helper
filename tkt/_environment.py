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

__all__ = ("Environment",)

from abc import ABC, abstractmethod
import importlib
import json
from typing import (
    Any,
    Dict,
    Iterable,
    Optional,
    TextIO,
)


class Editor(ABC):
    @classmethod
    @abstractmethod
    def from_json_data(cls, data: dict) -> Editor:
        raise NotImplementedError()

    @property
    @abstractmethod
    def needs_envvars(self) -> bool:
        raise NotADirectoryError()

    @abstractmethod
    def write(
        self,
        ticket: str,
        directory: str,
        packages: Iterable[str],
        envvars: Optional[dict] = None,
    ) -> None:
        raise NotImplementedError()


class Environment(ABC):
    @staticmethod
    def from_file(f: TextIO) -> Environment:
        data = json.load(f)
        mod = importlib.import_module(data["module"])
        cls = getattr(mod, data["cls"])
        return cls.from_json_data(data)

    @staticmethod
    def minimal() -> Environment:
        return _MinimalEnvironment()

    @classmethod
    @abstractmethod
    def from_json_data(cls, data: dict) -> Environment:
        raise NotImplementedError()

    @classmethod
    def _read_editors(cls, data: dict) -> Dict[str, Editor]:
        result: Dict[str, Editor] = {}
        for name, section in data.pop("editors", {}).items():
            mod = importlib.import_module(section.pop("module"))
            EditorClass = getattr(mod, section.pop("cls"))
            result[name] = EditorClass.from_json_data(section)
        return result

    @property
    @abstractmethod
    def default_metapackage(self) -> str:
        raise NotImplementedError()

    @property
    @abstractmethod
    def default_tag(self) -> str:
        raise NotImplementedError()

    @property
    @abstractmethod
    def shell(self) -> str:
        raise NotImplementedError()

    @property
    @abstractmethod
    def eups_prelude(self) -> str:
        raise NotImplementedError()

    @property
    @abstractmethod
    def default_workspace_eups_product(self) -> str:
        raise NotImplementedError()

    @abstractmethod
    def get_default_branch(self, package: str, ticket: str) -> str:
        raise NotImplementedError()

    @abstractmethod
    def get_workspace_directory(self, ticket: str) -> str:
        raise NotImplementedError()

    @abstractmethod
    def get_origin(self, package: str) -> str:
        raise NotImplementedError()

    def get_external_path(self, package: str) -> Optional[str]:
        return None

    @abstractmethod
    def get_editor(self, name: str) -> Optional[Editor]:
        raise NotImplementedError()


class _MinimalEnvironment(Environment):
    @classmethod
    def from_json_data(cls, data: Dict[str, Any]) -> Environment:
        return cls()

    @property
    def default_metapackage(self) -> str:
        raise TypeError("No environment and no metapackage provided.")

    @property
    def default_tag(self) -> str:
        raise TypeError("No environment and no tag provided.")

    @property
    def shell(self) -> str:
        raise TypeError("No shell provided.")

    @property
    def eups_prelude(self) -> str:
        raise TypeError("No EUPS prelude provided.")

    @property
    def default_workspace_eups_product(self) -> str:
        raise TypeError("No environment and no workspace eups product provided.")

    def get_default_branch(self, package: str, ticket: str) -> str:
        raise TypeError(f"No environment and no branch for {package} provided.")

    def get_workspace_directory(self, ticket: str) -> str:
        raise TypeError("No environment and no checkout directory provided.")

    def get_origin(self, package: str) -> str:
        raise TypeError(f"No environment and no existing repository provided for {package}.")

    def get_editor(self, name: str) -> Optional[Editor]:
        return None
