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

__all__ = ("Environment", "Tool")

import importlib
import json
from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, TextIO

if TYPE_CHECKING:
    from ._workspace import Workspace


class Tool(ABC):
    @classmethod
    @abstractmethod
    def from_json_data(cls, data: dict[str, Any]) -> Tool:
        raise NotImplementedError()

    @abstractmethod
    def write(
        self,
        ticket: str,
        directory: str,
        packages: Iterable[str],
        workspace: Workspace,
        environment: Environment,
    ) -> None:
        raise NotImplementedError()


class Environment(ABC):
    @staticmethod
    def load_config(f: TextIO) -> tuple[type[Environment], dict[str, Any]]:
        """Load config and resolve the Environment class."""
        data = json.load(f)
        mod = importlib.import_module(data["module"])
        cls = getattr(mod, data["cls"])
        return cls, data

    @staticmethod
    def from_file(f: TextIO) -> Environment:
        cls, data = Environment.load_config(f)
        return cls.from_json_data(data)

    @classmethod
    @abstractmethod
    def from_json_data(cls, data: dict[str, Any]) -> Environment:
        raise NotImplementedError()

    @classmethod
    def load_tools(cls, data: dict[str, Any]) -> dict[str, Tool]:
        result: dict[str, Tool] = {}
        for name, section in data.pop("tools", {}).items():
            mod = importlib.import_module(section.pop("module"))
            tool_cls = getattr(mod, section.pop("cls"))
            result[name] = tool_cls.from_json_data(section)
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

    def get_external_path(self, package: str) -> str | None:
        return None

    @abstractmethod
    def get_tool(self, name: str) -> Tool | None:
        raise NotImplementedError()
