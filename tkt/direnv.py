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

__all__ = ("DirEnv",)

import os
import shlex
import subprocess
from collections.abc import Iterable
from typing import Any

from ._environment import Environment, Tool
from ._workspace import Workspace

# Base variables always retained in the mostly-pristine capture environment.
_BASE_ENV = ("HOME", "PATH", "SHELL")

# Variables representing exported shell functions (e.g. BASH_FUNC_setup%%),
# not real envvars. direnv cannot propagate them, so they are excluded.
_SKIP_ENV_PREFIXES = ("BASH_FUNC_",)


class DirEnv(Tool):
    """Tool specialization for ``direnv``.

    Captures the conda/EUPS environment that ``loadLSST`` and ``setup -r .``
    produce in a mostly-pristine subprocess and writes it to the workspace's
    ``.envrc`` file.

    Parameters
    ----------
    scripts
        Ordered absolute paths of shell scripts to source in the capture
        subprocess (e.g. a ``loadLSST.*`` file).
    env
        Names of parent environment variables to propagate into the otherwise
        pristine capture subprocess environment.
    """

    def __init__(self, scripts: Iterable[str], env: Iterable[str]):
        self._scripts = tuple(scripts)
        self._env = tuple(env)

    @classmethod
    def from_json_data(cls, data: dict[str, Any]) -> Tool:
        scripts = data.pop("scripts", [])
        env = data.pop("env", [])
        if data:
            raise ValueError(f"Unexpected entries in direnv configuration: {data}.")
        return cls(scripts, env)

    def _pristine_env(self) -> dict[str, str]:
        """Build the mostly-pristine environment for the capture subprocess.

        Only the configured ``env`` names (propagated from the parent) and the
        essential base variables are retained; the rest of the parent
        environment is discarded.
        """
        result: dict[str, str] = {}
        for name in _BASE_ENV:
            if name in os.environ:
                result[name] = os.environ[name]
        for name in self._env:
            if name in os.environ:
                result[name] = os.environ[name]
        return result

    def write(
        self,
        ticket: str,
        directory: str,
        packages: Iterable[str],
        workspace: Workspace,
        environment: Environment,
    ) -> None:
        """Write the workspace's ``.envrc`` via :meth:`write_envrc`."""
        del ticket, packages, workspace  # unused
        self.write_envrc(directory, environment.shell)

    def write_envrc(self, directory: str, shell: str, *, dry_run: bool = False) -> int:
        """Capture the conda/EUPS environment and write it to ``.envrc``.

        Works on any EUPS product checkout (a directory with an ``ups/``
        subdirectory), not just a tkt workspace.

        Parameters
        ----------
        directory
            Root of the EUPS product checkout to capture (and write to).
        shell
            Path to the shell that runs the capture subprocess.
        dry_run
            If ``True``, run the guards and the capture subprocess but write
            nothing and run no ``direnv allow``.

        Returns
        -------
        int
            The number of ``export`` lines the capture produced (``0`` when
            the capture produced nothing and any existing ``.envrc`` is left
            untouched).

        Raises
        ------
        FileNotFoundError
            If ``directory`` has no ``ups/`` subdirectory, or if a configured
            capture script does not exist.
        RuntimeError
            If the capture subprocess exits with a nonzero status.
        """
        if not os.path.isdir(os.path.join(directory, "ups")):
            raise FileNotFoundError(f"{directory} is not an EUPS product (no ups/ directory).")
        for script in self._scripts:
            if not os.path.isfile(script):
                raise FileNotFoundError(f"Configured direnv script {script} does not exist.")
        pristine = self._pristine_env()
        command = self._build_command(directory)
        result = subprocess.run(
            [shell, "-c", command],
            env=pristine,
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"direnv capture failed (exit {result.returncode}): {result.stderr.strip()}")
        captured = self._parse_env(result.stdout)
        envrc_lines = self._envrc_lines(captured)
        if not envrc_lines:
            return 0
        if dry_run:
            return len(envrc_lines)
        with open(os.path.join(directory, ".envrc"), "w") as f:
            f.write("\n".join(envrc_lines) + "\n")
        subprocess.run(["direnv", "allow", directory])
        return len(envrc_lines)

    def _build_command(self, directory: str) -> str:
        lines = [f"cd {shlex.quote(directory)}"]
        for script in self._scripts:
            lines.append(f"source {shlex.quote(script)}")
        lines.append("setup -r .")
        lines.append("env")
        return "\n".join(lines)

    @staticmethod
    def _parse_env(output: str) -> dict[str, str]:
        """Parse the ``env``-style ``KEY=VALUE`` output into a dict."""
        result: dict[str, str] = {}
        for line in output.splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.startswith(_SKIP_ENV_PREFIXES):
                result[key] = value
        return result

    @staticmethod
    def _envrc_lines(captured: dict[str, str]) -> list[str]:
        """Build ``export`` lines for every captured variable."""
        return [f"export {key}={shlex.quote(value)}" for key, value in sorted(captured.items())]
