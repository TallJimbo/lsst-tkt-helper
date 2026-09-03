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
    "ReadResult",
    "WarmSandbox",
    "build_driver_script",
    "build_read_command",
    "decode_field",
    "encode_field",
    "parse_result_line",
    "run_server",
    "truncate_output",
)

import base64
import os
import re
import shlex
import subprocess
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from .sandbox import Sandbox

# Model-facing cap for the `bash` tool output, in characters (unchanged
# default).
_BASH_OUTPUT_CHARS = 5_000

# Shared hard cap / ceiling (chars) for tool output; `read`'s per-call cap
# scales up to this value.
_MAX_OUTPUT_CHARS = 25_000

# Driver-side hard cap (bytes) per stream; SIGPIPE-kills a runaway producer.
_DRIVER_OUT_CAP = 50_000

# Sandbox-side bound for `read` (bytes): `fold` wraps over-long lines at this
# width and `head -c` caps the shipped slice, so a huge single-line file can
# neither OOM `sed` nor exceed the driver's wire cap
# (base64(CAP) < _DRIVER_OUT_CAP).
_READ_BYTE_CAP = 32_000

# Per-line scale factor for `read`'s model-facing cap (this repo's ruff
# line-length). cap = min(_MAX_OUTPUT_CHARS, limit * _CHARS_PER_LINE).
_CHARS_PER_LINE = 110


class BashResult(BaseModel):
    """The outcome of one sandboxed ``bash`` call.

    ``stdout`` and ``stderr`` are the child's captured output (truncated to
    ``_BASH_OUTPUT_CHARS`` for the model); ``exit_code`` is its exit status;
    ``timed_out`` is True when the call was killed for exceeding its
    ``timeout_ms``; ``truncated`` is True when either stream was cut to
    ``_BASH_OUTPUT_CHARS``.
    """

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    truncated: bool = False


class ReadResult(BaseModel):
    """The outcome of one sandboxed ``read`` call.

    ``content`` is the line-numbered slice of a text file. When lines remain
    past the slice, ``content`` ends with a ``... (N more lines)`` note and
    ``truncated`` is True.
    """

    content: str
    truncated: bool


def encode_field(text: str) -> str:
    """Base64-encode ``text`` with no newlines, so it is safe on one line."""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def decode_field(field: str) -> str:
    """Inverse of :func:`encode_field`."""
    return base64.b64decode(field.encode("ascii")).decode("utf-8")


def read_char_cap(limit: int) -> int:
    """Model-facing char cap for a ``read`` of ``limit`` lines.

    Scales with the requested line count (about one ruff-formatted line per
    ``_CHARS_PER_LINE`` chars) and is hard-capped at ``_MAX_OUTPUT_CHARS``.
    """
    return min(_MAX_OUTPUT_CHARS, limit * _CHARS_PER_LINE)


def truncate_output(text: str, max_chars: int) -> tuple[str, bool]:
    """Keep head+tail of ``text`` within ``max_chars`` chars.

    Returns ``(text, truncated)``. When ``len(text) <= max_chars`` the input is
    returned unchanged with ``truncated=False``. When it must be cut, roughly
    half the budget is kept as head and half as tail, joined by a marker
    reporting exactly how many characters were dropped. 0 or negative
    ``max_chars`` keeps marker-only output.
    """
    if len(text) <= max_chars:
        return text, False
    n_head = max_chars // 2
    n_tail = max_chars - n_head
    head = text[:n_head]
    tail = text[-n_tail:] if n_tail else ""
    dropped = len(text) - len(head) - len(tail)
    marker = f"\n... [{dropped} chars truncated] ...\n"
    return head + marker + tail, True


def _cap_result(result: BashResult, max_chars: int) -> BashResult:
    """Apply ``truncate_output`` to both streams, setting ``truncated``."""
    stdout, s_trunc = truncate_output(result.stdout, max_chars)
    stderr, e_trunc = truncate_output(result.stderr, max_chars)
    return BashResult(
        stdout=stdout,
        stderr=stderr,
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        truncated=s_trunc or e_trunc,
    )


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


_READ_TOTAL_RE = re.compile(r"READ_TOTAL (\d+)")


def build_read_command(path: str, offset: int, limit: int) -> str:
    """Build the sandbox command that reads a slice of ``path``.

    Reads lines ``[offset+1, offset+limit]`` (1-based, via ``sed``) and emits
    the raw slice base64-encoded on stdout, so the byte stream round-trips
    losslessly through the UTF-8 decode in :func:`parse_result_line` (a binary
    file degrades to a host-side "binary" message instead of crashing the
    framing). ``fold -b -w`` bounds per-line memory and ``head -c`` bounds
    the shipped output bytes. The total line count is reported on stderr as a
    ``READ_TOTAL <n>`` marker so the host can compute the truncation note.
    ``path`` is embedded via ``shlex.quote``.
    """
    quoted = shlex.quote(path)
    start = offset + 1
    end = offset + limit
    return (
        f"f={quoted}\n"
        'if [ ! -f "$f" ]; then '
        'printf "read: no such file or not a regular file: %s\\n" '
        '"$f" >&2; exit 1; fi\n'
        'printf "READ_TOTAL %s\\n" "$(wc -l < "$f")" >&2\n'
        f'fold -b -w "{_READ_BYTE_CAP}" "$f" | '
        f'sed -n "{start},{end}p" | '
        f'head -c "{_READ_BYTE_CAP}" | base64 -w0\n'
        'printf "\\n"\n'
    )


def _parse_read_total(stderr: str) -> int | None:
    """Return the ``READ_TOTAL`` count parsed from ``stderr``, or None."""
    m = _READ_TOTAL_RE.search(stderr)
    return int(m.group(1)) if m else None


def _trim_incomplete_trailing(raw: bytes) -> bytes:
    """Drop trailing bytes that form an incomplete UTF-8 sequence.

    Byte-based truncation (``head -c`` / ``fold -b``) can cut a multi-byte
    character mid-sequence; the incomplete tail would otherwise make the whole
    buffer fail strict UTF-8 decode. Returns the buffer with up to 3 trailing
    bytes dropped when that recovers valid UTF-8, else the input unchanged.
    """
    for n in range(4):
        candidate = raw[:-n] if n else raw
        try:
            candidate.decode("utf-8")
        except UnicodeDecodeError:
            continue
        return candidate
    return raw


def read_tool(warm: WarmSandbox, *, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
    """Run one sandboxed ``read`` against ``warm`` and return a
    :class:`ReadResult`.
    """
    offset = max(0, offset)
    limit = max(1, limit)
    result = warm.run(build_read_command(file_path, offset, limit))
    if result.exit_code != 0:
        err = (result.stderr or result.stdout or "").strip()
        return ReadResult(content=f"read: {err}", truncated=False)
    total = _parse_read_total(result.stderr)
    raw = base64.b64decode(result.stdout.strip())
    try:
        trimmed = _trim_incomplete_trailing(raw)
        text = trimmed.decode("utf-8")
    except UnicodeDecodeError:
        return ReadResult(
            content="read: file appears to be binary (did not decode as UTF-8)",
            truncated=False,
        )
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    lines = lines[:limit]
    numbered = "\n".join(f"{offset + i + 1}\t{line}" for i, line in enumerate(lines))
    returned = offset + len(lines)
    if total is None:
        total = returned
    more = total - returned
    line_truncated = more > 0
    if line_truncated and offset == 0:
        numbered += f"\n... ({more} more lines)"
    # Model-facing byte cap: truncate head+tail, flagging if cut. Dropping
    # trailing incomplete-UTF-8 bytes is itself evidence that the sandbox's
    # byte cap cut the output, so that read is reported as truncated too.
    content, byte_truncated = truncate_output(numbered, read_char_cap(limit))
    byte_cut = len(trimmed) < len(raw)
    return ReadResult(content=content, truncated=line_truncated or byte_truncated or byte_cut)


def build_driver_script(setup_lines: list[str]) -> str:
    """Build the warm-holder driver bash script.

    ``setup_lines`` run once at startup (conda activation + EUPS setup). Their
    stdout is redirected to stderr so startup diagnostics never leak into the
    framed stdout channel. The EUPS/conda shell functions are exported so fresh
    children can call ``setup``/``conda``. Then a loop reads three base64 lines
    per request (cwd, command, timeout_ms), runs the command in a fresh
    ``bash -c`` child (under ``timeout --kill-after=5`` when a positive timeout
    is given: default TERM at the deadline, escalating to KILL), and emits one
    result line of 5 space-separated base64 fields (stdout, stderr, exit_code,
    cwd, timed_out). ``timed_out`` maps from ``rc==124 || rc==137``. Both
    streams are hard-capped at ``_DRIVER_OUT_CAP`` bytes via ``head -c``: a
    producer that exceeds the cap is SIGPIPE-killed (``rc==141``), a marker is
    appended to stderr, and only the capped bytes ever reach the frame.
    ``rc=${PIPESTATUS[0]}`` preserves the command's own exit code; the bare
    ``wait`` syncs the stderr process substitution before framing. The
    ``timeout_ms`` value is enforced at whole-second granularity (sub-second
    values round up to 1s).
    """
    setup = "\n".join(setup_lines)
    return (
        "{\n"
        f"{setup}\n"
        '    while IFS= read -r _f; do export -f "$_f"; done < <(compgen -A function)\n'
        "} >&2\n"
        f'OC="{_DRIVER_OUT_CAP}"\n'
        "while IFS= read -r cwd_b64 && IFS= read -r cmd_b64 && IFS= read -r tmo_b64; do\n"
        "    cwd=$(printf '%s' \"$cwd_b64\" | base64 -d)\n"
        "    cmd=$(printf '%s' \"$cmd_b64\" | base64 -d)\n"
        "    tmo=$(printf '%s' \"$tmo_b64\" | base64 -d)\n"
        '    cd "$cwd" 2>/dev/null || true\n'
        "    out=$(mktemp)\n"
        "    errf=$(mktemp)\n"
        '    if [ "$tmo" -gt 0 ]; then\n'
        "        secs=$(( (tmo + 999) / 1000 ))\n"
        '        timeout --kill-after=5 "${secs}s" bash -c -- "$cmd" '
        '</dev/null 2> >(head -c "$OC" >"$errf") | head -c "$OC" >"$out"\n'
        "    else\n"
        '        bash -c -- "$cmd" </dev/null 2> >(head -c "$OC" >"$errf") | head -c "$OC" >"$out"\n'
        "    fi\n"
        "    rc=${PIPESTATUS[0]}\n"
        "    wait\n"
        '    if [ "$rc" -eq 141 ]; then\n'
        '        printf "\\n[output hard-capped: command produced more than %s bytes and was killed]\\n" '
        '"$OC" >>"$errf"\n'
        "    fi\n"
        "    cur=$(pwd)\n"
        '    if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then to=1; else to=0; fi\n'
        '    printf "%s %s %s %s %s\\n" \\\n'
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
        # Strip only the trailing line terminator, never leading whitespace:
        # when the command produced no stdout, the driver's first result field
        # is empty and the line begins with a space. strip() would remove that
        # leading space, collapsing the 5 fields into 4 and breaking the split.
        frame = parse_result_line(line.decode().rstrip("\r\n"))
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
        that exceeds its timeout is killed and reported with ``timed_out``. The
        command is also hard-capped in the sandbox at ``_DRIVER_OUT_CAP`` bytes
        per stream (a runaway producer is killed); the model sees stdout/stderr
        truncated to ``_BASH_OUTPUT_CHARS`` chars each (head+tail,
        ``truncated`` set if cut).
        ``description`` is a per-call rationale for the human (e.g. shown when
        a host requests tool confirmation); it is not used to change behavior.

        Args:
            command: The shell command to run.
            timeout_ms: Kill the command after this many milliseconds (0 means
                no timeout).
            description: Optional human-readable rationale for this call.
        """
        return _cap_result(warm.run(command, timeout_ms=timeout_ms), _BASH_OUTPUT_CHARS)

    @mcp.tool()
    def read(
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
        description: str | None = None,  # present for human approvals of tool actions
    ) -> ReadResult:
        """Read a file (or a slice of it) inside the tkt sandbox.

        The sandbox blocks ``$HOME`` (so credentials are never exposed) but
        mounts the workspace
        and the read-only ``~/.agents/skills`` directory, so skill reference
        files are readable. ``offset`` (default 0) is the number of lines to
        skip; ``limit`` (default 2000) is the max lines to read. When more
        lines remain past the slice, ``content`` ends with a ``... (N more
        lines)`` note and ``truncated`` is True. ``description`` is a per-call
        rationale for the human; it does not change behavior.

        Args:
            file_path: The file to read (absolute, or relative to the sandbox
                cwd).
            offset: Number of lines to skip from the start.
            limit: Maximum number of lines to read.
            description: Optional human-readable rationale for this call.
        """
        return read_tool(warm, file_path=file_path, offset=offset, limit=limit)

    mcp.run()
