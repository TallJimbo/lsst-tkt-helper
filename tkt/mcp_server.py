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

__all__ = (
    "BashResult",
    "WarmSandbox",
    "build_driver_script",
    "decode_field",
    "encode_field",
    "parse_result_line",
    "run_server",
)

import base64
import os
import subprocess
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from .sandbox import Sandbox


class BashResult(BaseModel):
    """The outcome of one sandboxed ``bash`` call.

    ``stdout`` and ``stderr`` are the child's captured output; ``exit_code``
    is its exit status; ``timed_out`` is True when the call was killed for
    exceeding its ``timeout_ms``.
    """

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False


def encode_field(text: str) -> str:
    """Base64-encode ``text`` with no newlines, so it is safe on one line."""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def decode_field(field: str) -> str:
    """Inverse of :func:`encode_field`."""
    return base64.b64decode(field.encode("ascii")).decode("utf-8")


def parse_result_line(line: str) -> dict[str, Any]:
    """Parse one driver result line into a dict.

    The driver emits 5 space-separated base64 fields: stdout, stderr,
    exit_code, cwd, timed_out. base64 has no spaces, so splitting on a single
    space is unambiguous.
    """
    parts = line.split(" ")
    if len(parts) != 5:
        raise ValueError(f"Malformed result line: {line!r}")
    stdout, stderr, exit_code, cwd, timed_out = (decode_field(p) for p in parts)
    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": int(exit_code),
        "cwd": cwd,
        "timed_out": bool(int(timed_out)),
    }


def build_driver_script(setup_lines: list[str]) -> str:
    """Build the warm-holder driver bash script.

    ``setup_lines`` run once at startup (conda activation + EUPS setup), with
    their stdout redirected to stderr so startup diagnostics never leak into
    the framed stdout channel. The EUPS/conda shell functions are exported so
    fresh children can call ``setup``/``conda``. Then a loop reads three base64
    lines per request (cwd, command, timeout_ms), runs the command in a fresh
    ``bash -c`` child (under ``timeout --kill-after=5`` when a positive timeout
    is given: default TERM at the deadline, escalating to KILL), and emits one
    result line of 5 space-separated base64 fields (stdout, stderr, exit_code,
    cwd, timed_out). ``timed_out`` maps from ``rc==124 || rc==137``. The
    ``timeout_ms`` value is enforced at whole-second granularity (sub-second
    values round up to 1s).
    """
    setup = "\n".join(setup_lines)
    return (
        "{\n"
        f"{setup}\n"
        '    while IFS= read -r _f; do export -f "$_f"; done < <(compgen -A function)\n'
        "} >&2\n"
        "while IFS= read -r cwd_b64 && IFS= read -r cmd_b64 && IFS= read -r tmo_b64; do\n"
        "    cwd=$(printf '%s' \"$cwd_b64\" | base64 -d)\n"
        "    cmd=$(printf '%s' \"$cmd_b64\" | base64 -d)\n"
        "    tmo=$(printf '%s' \"$tmo_b64\" | base64 -d)\n"
        '    cd "$cwd" 2>/dev/null || true\n'
        "    out=$(mktemp)\n"
        "    errf=$(mktemp)\n"
        '    if [ "$tmo" -gt 0 ]; then\n'
        "        secs=$(( (tmo + 999) / 1000 ))\n"
        '        timeout --kill-after=5 "${secs}s" bash -c -- "$cmd" </dev/null >"$out" 2>"$errf"\n'
        "    else\n"
        '        bash -c -- "$cmd" </dev/null >"$out" 2>"$errf"\n'
        "    fi\n"
        "    rc=$?\n"
        "    cur=$(pwd)\n"
        '    if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then to=1; else to=0; fi\n'
        "    printf '%s %s %s %s %s\\n' \\\n"
        '        "$(base64 -w0 < "$out")" \\\n'
        '        "$(base64 -w0 < "$errf")" \\\n'
        '        "$(printf \'%s\' "$rc" | base64 -w0)" \\\n'
        '        "$(printf \'%s\' "$cur" | base64 -w0)" \\\n'
        '        "$(printf \'%s\' "$to" | base64 -w0)"\n'
        '    rm -f "$out" "$errf"\n'
        "done\n"
    )


class WarmSandbox:
    """A long-lived bwrap holder that runs commands in fresh child shells.

    Spawns the bwrap holder lazily on first :meth:`run`. The holder runs the
    conda/EUPS setup once; each call runs the command in a fresh ``bash -c``
    child that inherits the warm environment. The working directory is tracked
    server-side from the end-of-call ``pwd``.
    """

    def __init__(
        self,
        sandbox: Sandbox,
        *,
        workspace=None,
        repo_dir=None,
        cwd: str,
        conda_env: str | None = None,
        timeout_ms: int = 60000,
    ) -> None:
        self._sandbox = sandbox
        self._workspace = workspace
        self._repo_dir = repo_dir
        self._conda_env = conda_env
        self._cwd = cwd
        self._default_timeout_ms = timeout_ms
        self._proc: subprocess.Popen[bytes] | None = None

    @property
    def cwd(self) -> str:
        return self._cwd

    def _start(self) -> None:
        assert isinstance(self._sandbox, Sandbox)
        setup_lines = self._setup_lines(self._conda_env)
        inner = build_driver_script(setup_lines)
        argv = self._sandbox.warm_holder_argv(
            workspace=self._workspace,
            repo_dir=self._repo_dir,
            inner=inner,
        )
        self._proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )

    def _setup_lines(self, conda_env: str | None = None) -> list[str]:
        """Build the one-time setup lines for the warm holder.

        Mirrors ``tkt.sandbox._build_inner_script``: conda is activated only
        when a ``conda_env`` name is explicitly provided (the
        ``LSST_CONDA_ENV_NAME`` environment variable is not consulted here),
        and the Rubin environment is set up with EUPS.
        """
        lines: list[str] = []
        if conda_env is not None:
            lines.append(
                "source $CONDA_PREFIX/etc/profile.d/conda.sh "
                "|| source $(dirname $(dirname $(which conda)))/etc/profile.d/conda.sh"
            )
            lines.append(f"conda activate {conda_env}")
        if self._workspace is not None:
            lines.append("setup -r .agent")
        elif self._repo_dir is not None and os.path.isdir(os.path.join(self._repo_dir, "ups")):
            lines.append("setup -r .")
        return lines

    def run(self, command: str, *, timeout_ms: int | None = None) -> BashResult:
        if self._proc is None:
            self._start()
        assert self._proc is not None and self._proc.stdin is not None
        assert self._proc.stdout is not None
        if timeout_ms is None:
            timeout_ms = self._default_timeout_ms
        if timeout_ms < 0:
            timeout_ms = 0
        # Write cwd, command, and timeout_ms as three base64 lines.
        self._proc.stdin.write(
            (
                encode_field(self._cwd)
                + "\n"
                + encode_field(command)
                + "\n"
                + encode_field(str(timeout_ms))
                + "\n"
            ).encode()
        )
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            rc = self._proc.poll()
            if rc is not None:
                raise RuntimeError(f"Warm holder exited unexpectedly (exit code {rc}).")
            raise RuntimeError("Warm holder exited unexpectedly.")
        frame = parse_result_line(line.strip().decode())
        self._cwd = frame["cwd"]
        return BashResult(
            stdout=frame["stdout"],
            stderr=frame["stderr"],
            exit_code=frame["exit_code"],
            timed_out=frame["timed_out"],
        )


def run_server(
    sandbox,
    *,
    cwd: str,
    workspace=None,
    repo_dir=None,
    conda_env: str | None = None,
) -> None:
    """Run the FastMCP stdio server exposing the ``bash`` tool.

    ``sandbox`` is the configured ``tkt.sandbox.Sandbox`` tool. ``cwd`` is the
    project root (Zed spawns this process with cwd = project root). Warm start
    is lazy: the holder spawns on the first ``bash`` call.
    """
    warm = WarmSandbox(
        sandbox,
        workspace=workspace,
        repo_dir=repo_dir,
        cwd=cwd,
        conda_env=conda_env,
    )
    mcp = FastMCP(name="tkt")

    @mcp.tool()
    def bash(
        command: str,
        timeout_ms: int | None = None,
        description: str | None = None,  # present for human approvals of tool actions
    ) -> BashResult:
        """Run a shell command inside the tkt sandbox.

        ``timeout_ms`` defaults to 60s; a request may override it. A command
        that exceeds its timeout is killed and reported with ``timed_out``.
        ``description`` is a per-call rationale for the human (e.g. shown when
        a host requests tool confirmation); it is not used to change behavior.

        Args:
            command: The shell command to run.
            timeout_ms: Kill the command after this many milliseconds (0 means
                no timeout).
            description: Optional human-readable rationale for this call.
        """
        return warm.run(command, timeout_ms=timeout_ms)

    mcp.run()
