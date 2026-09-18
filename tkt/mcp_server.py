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
    "BrainstormManager",
    "TodoItem",
    "TodoStore",
    "WarmSandbox",
    "build_driver_script",
    "build_edit_command",
    "build_glob_command",
    "build_grep_command",
    "build_ls_command",
    "build_read_command",
    "build_write_command",
    "decode_field",
    "edit_tool",
    "encode_field",
    "format_bash_result",
    "glob_tool",
    "grep_tool",
    "ls_tool",
    "parse_result_line",
    "run_server",
    "truncate_output",
    "write_tool",
)

import base64
import json
import os
import re
import shlex
import signal
import subprocess
import time
from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from .mcp_files import MAX_CONTENT_BYTES
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


class TodoItem(BaseModel):
    """One entry in the agent's todo list.

    ``status`` is one of ``pending``, ``in_progress``, ``completed``, or
    ``cancelled``; ``activeForm`` is the present-participle verb phrase
    (e.g. ``Building``, ``Testing``) shown while the item is in progress.
    """

    content: str
    status: str = "pending"
    activeForm: str | None = None


class TodoStore:
    """In-memory scratchpad holding the agent's current todo list.

    Each ``run_server`` instance owns one ``TodoStore``. ``todo_write``
    replaces the list wholesale (Claude Code-style) and returns it rendered as
    a markdown checklist, so the store is a trivial holder. State is host-side
    (not sandboxed — this is model bookkeeping with no file access) and is
    lost if the server process restarts, which is acceptable for a scratchpad.
    """

    def __init__(self) -> None:
        self._todos: list[TodoItem] = []

    def replace(self, todos: list[TodoItem]) -> str:
        """Replace the stored list with ``todos`` and return it as markdown."""
        self._todos = list(todos)
        return _format_todos(self._todos)


def _format_todos(todos: list[TodoItem]) -> str:
    """Render a todo list as a markdown checklist.

    ``completed`` maps to ``- [x]``, ``cancelled`` to a struck-through
    ``- [~]``, and ``pending``/``in_progress`` to ``- [ ]`` (the latter
    showing ``activeForm`` when present).
    """
    lines = []
    for t in todos:
        if t.status == "completed":
            lines.append(f"- [x] {t.content}")
        elif t.status == "cancelled":
            lines.append(f"- [~] ~~{t.content}~~")
        elif t.status == "in_progress":
            suffix = f" ({t.activeForm})" if t.activeForm else ""
            lines.append(f"- [ ] {t.content}{suffix}")
        else:
            lines.append(f"- [ ] {t.content}")
    return "\n".join(lines)


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


def _md_fence(text: str) -> str:
    """Wrap ``text`` in a ``text`` code fence for monospace rendering."""
    return f"```text\n{text}\n```"


def _format_listing(text: str) -> str:
    """Render listing output (ls/glob/grep) as a fenced block.

    Trailing newlines are stripped so the fence closes cleanly; empty output
    yields an empty string (not an empty fence).
    """
    body = text.rstrip("\n")
    if not body:
        return ""
    return _md_fence(body)


def format_bash_result(result: BashResult) -> str:
    """Render a :class:`BashResult` as markdown for the agent UI.

    Stdout is shown in its own code fence; stderr is fenced separately and
    labelled. Nonzero exit codes, timeouts, and truncation are appended as
    plain-text notes so the human can distinguish output from status.
    """
    parts = []
    if result.stdout:
        parts.append(_md_fence(result.stdout.rstrip("\n")))
    if result.stderr:
        parts.append(f"stderr:\n{_md_fence(result.stderr.rstrip('\n'))}")
    notes = []
    if result.exit_code != 0:
        notes.append(f"exit code {result.exit_code}")
    if result.timed_out:
        notes.append("timed out")
    if result.truncated:
        notes.append("output truncated")
    if notes:
        parts.append("\n".join(notes))
    return "\n\n".join(parts)


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


def build_ls_command(path: str) -> str:
    """Build the sandbox command that lists ``path``.

    ``ls -laF`` lists all entries in long format with type indicators; ``--``
    guards a path that begins with ``-``. ``path`` is embedded via
    ``shlex.quote``.
    """
    return f"ls -laF -- {shlex.quote(path)}\n"


def build_glob_command(pattern: str, path: str) -> str:
    """Build the sandbox command that finds files matching a glob.

    ``globstar`` makes ``**`` recurse across directories and ``nullglob`` drops
    unmatched patterns, so a no-match yields empty output (rc 0). The
    pattern is assigned to a quoted variable (preventing shell injection) and
    then ``for f in $pattern`` glob-expands it; ``[ -e "$f" ]`` filters
    literals that do not exist. ``path`` and ``pattern`` are embedded via
    ``shlex.quote``.
    """
    quoted_path = shlex.quote(path)
    quoted_pattern = shlex.quote(pattern)
    return (
        f"cd {quoted_path} && "
        "shopt -s globstar nullglob && "
        f"pattern={quoted_pattern} && "
        'for f in $pattern; do [ -e "$f" ] && printf \'%s\\n\' "$f"; done\n'
    )


def build_grep_command(
    pattern: str,
    path: str = ".",
    glob: str | None = None,
    output_mode: str = "content",
    ignore_case: bool = False,
    line_number: bool = False,
) -> str:
    """Build the sandbox command that searches file contents.

    ``grep -rE -IH`` searches recursively with extended regex, skipping
    binary files (``-I`` avoids the UTF-8 framing failure ``read`` defends
    against) and always prefixing filenames (``-H``). ``--exclude-dir=.git``
    skips the
    mounted git dir; ``--include`` (when ``glob`` is given) restricts to
    matching files. ``output_mode`` maps to ``-l`` (files) or ``-o`` (matches);
    ``line_number`` adds ``-n`` (content mode). ``-e`` precedes the pattern so
    patterns beginning with ``-`` work. ``pattern`` and ``path`` are embedded
    via ``shlex.quote``.
    """
    flags = ["-r", "-E", "-I", "-H"]
    if ignore_case:
        flags.append("-i")
    if glob is not None:
        flags.append(f"--include={shlex.quote(glob)}")
    flags.append("--exclude-dir=.git")
    if output_mode == "files":
        flags.append("-l")
    elif output_mode == "matches":
        flags.append("-o")
    elif line_number:
        flags.append("-n")
    flag_str = " ".join(flags)
    return f"grep {flag_str} -e {shlex.quote(pattern)} -- {shlex.quote(path)}\n"


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


def read_tool(warm: WarmSandbox, *, file_path: str, offset: int = 0, limit: int = 2000) -> str:
    """Run one sandboxed ``read`` against ``warm`` and return markdown.

    The result links the file path (clickable) and shows the line-numbered
    slice in a code fence, keeping the ``... (N more lines)`` note.
    """
    offset = max(0, offset)
    limit = max(1, limit)
    result = warm.run(build_read_command(file_path, offset, limit))
    if result.exit_code != 0:
        err = (result.stderr or result.stdout or "").strip()
        return f"read: {err}"
    total = _parse_read_total(result.stderr)
    raw = base64.b64decode(result.stdout.strip())
    try:
        trimmed = _trim_incomplete_trailing(raw)
        text = trimmed.decode("utf-8")
    except UnicodeDecodeError:
        return "read: file appears to be binary (did not decode as UTF-8)"
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    lines = lines[:limit]
    numbered = "\n".join(f"{offset + i + 1}\t{line}" for i, line in enumerate(lines))
    returned = offset + len(lines)
    if total is None:
        total = returned
    more = total - returned
    if more > 0 and offset == 0:
        numbered += f"\n... ({more} more lines)"
    # Model-facing byte cap: truncate head+tail. Dropping trailing
    # incomplete-UTF-8 bytes is itself evidence that the sandbox's byte cap cut
    # the output, so that read is reported as truncated too.
    content, _ = truncate_output(numbered, read_char_cap(limit))
    return f"[`{file_path}`]({file_path})\n{_md_fence(content)}"


def ls_tool(warm: WarmSandbox, *, path: str = ".") -> str:
    """Run one sandboxed ``ls`` against ``warm`` and return markdown.

    The ``ls -laF`` listing is shown in a code fence to preserve column
    alignment.
    """
    result = warm.run(build_ls_command(path))
    if result.exit_code != 0:
        err = (result.stderr or result.stdout or "").strip()
        return f"ls: {err}"
    content, _ = truncate_output(result.stdout, _MAX_OUTPUT_CHARS)
    return _format_listing(content)


def glob_tool(warm: WarmSandbox, *, pattern: str, path: str = ".") -> str:
    """Run one sandboxed ``glob`` against ``warm`` and return markdown.

    Matching paths are shown one per line in a code fence.
    """
    result = warm.run(build_glob_command(pattern, path))
    if result.exit_code != 0:
        err = (result.stderr or result.stdout or "").strip()
        return f"glob: {err}"
    content, _ = truncate_output(result.stdout, _MAX_OUTPUT_CHARS)
    return _format_listing(content)


def grep_tool(
    warm: WarmSandbox,
    *,
    pattern: str,
    path: str = ".",
    glob: str | None = None,
    output_mode: str = "content",
    ignore_case: bool = False,
    line_number: bool = False,
) -> str:
    """Run one sandboxed ``grep`` against ``warm`` and return markdown.

    ``grep``'s rc 1 (no matches) is normalized to empty output rather than
    reported as an error.
    """
    result = warm.run(build_grep_command(pattern, path, glob, output_mode, ignore_case, line_number))
    if result.exit_code == 1:
        return ""
    if result.exit_code != 0:
        err = (result.stderr or result.stdout or "").strip()
        return f"grep: {err}"
    content, _ = truncate_output(result.stdout, _MAX_OUTPUT_CHARS)
    return _format_listing(content)


def build_write_command(file_path: str, content: str) -> str:
    """Build the sandbox command that writes ``content`` to ``file_path``.

    ``content`` rides as base64 (shell-safe, arbitrary bytes) and is decoded by
    ``tkt.mcp_files`` inside the sandbox; ``file_path`` is embedded via
    ``shlex.quote`` and resolved against the tracked cwd by the module.
    """
    content_b64 = base64.b64encode(content.encode()).decode("ascii")
    return f"python -m tkt.mcp_files write {shlex.quote(file_path)} {content_b64}"


def build_edit_command(file_path: str, old_string: str, new_string: str, replace_all: bool) -> str:
    """Build the sandbox command that edits ``file_path``.

    ``old_string``/``new_string`` ride as base64; ``replace_all`` is
    ``1``/``0``.
    """
    old_b64 = base64.b64encode(old_string.encode()).decode("ascii")
    new_b64 = base64.b64encode(new_string.encode()).decode("ascii")
    flag = "1" if replace_all else "0"
    return f"python -m tkt.mcp_files edit {shlex.quote(file_path)} {old_b64} {new_b64} {flag}"


def _run_files_op(warm: WarmSandbox, *, command: str) -> str:
    """Run a ``python -m tkt.mcp_files`` command and return its message.

    The module formats both success and failure as markdown on stdout; exit 0
    means success, nonzero a graceful error (or, on a crash, the stderr tail).
    """
    result = warm.run(command)
    body = result.stdout.strip()
    if result.exit_code != 0 and not body:
        body = result.stderr.strip()
    if result.exit_code != 0 and not body:
        body = f"mcp_files exited {result.exit_code}"
    return body


def write_tool(warm: WarmSandbox, *, file_path: str, content: str) -> str:
    """Run one sandboxed ``write`` against ``warm`` and return markdown."""
    nbytes = len(content.encode("utf-8"))
    if nbytes > MAX_CONTENT_BYTES:
        return f"Write failed: content too large ({nbytes} bytes, max {MAX_CONTENT_BYTES})"
    return _run_files_op(warm, command=build_write_command(file_path, content))


def edit_tool(
    warm: WarmSandbox,
    *,
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """Run one sandboxed ``edit`` against ``warm`` and return markdown."""
    for name, value in (("old_string", old_string), ("new_string", new_string)):
        nbytes = len(value.encode("utf-8"))
        if nbytes > MAX_CONTENT_BYTES:
            return f"Edit failed: {name} too large ({nbytes} bytes, max {MAX_CONTENT_BYTES})"
    return _run_files_op(warm, command=build_edit_command(file_path, old_string, new_string, replace_all))


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
            if getattr(self._workspace, "shared_worktree", False):
                # No .agent copy in shared mode: set up the human's workspace
                # product from its read-only ups/ directory.
                lines.append(f"setup -r {shlex.quote(self._workspace.directory)}")
            else:
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


# Path of the visual-companion launcher inside this repo's pinned superpowers
# submodule, and the fallback location installed by ``tkt install-zed-agent``.
_BRAINSTORM_SCRIPT_RELPATH = os.path.join(
    "superpowers", "skills", "brainstorming", "scripts", "start-server.sh"
)
_BRAINSTORM_SCRIPT_HOME_RELPATH = os.path.join(
    ".agents", "skills", "brainstorming", "scripts", "start-server.sh"
)
# Wall-clock bound for the launcher script (it self-bounds to ~8 s waiting for
# the server to start and prove it stays alive).
_BRAINSTORM_START_TIMEOUT_S = 30


def _find_brainstorm_script() -> str:
    """Locate ``start-server.sh`` on the host, honoring the env override.

    Raises:
        FileNotFoundError: if no candidate launcher script exists.
    """
    override = os.environ.get("TKT_BRAINSTORM_SCRIPT")
    candidates = (
        [override]
        if override
        else [
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), _BRAINSTORM_SCRIPT_RELPATH
            ),
            os.path.join(os.path.expanduser("~"), _BRAINSTORM_SCRIPT_HOME_RELPATH),
        ]
    )
    for cand in candidates:
        if cand and os.path.isfile(cand):
            return os.path.realpath(cand)
    raise FileNotFoundError(
        "brainstorm server launcher not found; tried: " + ", ".join(c for c in candidates if c)
    )


def _last_json_object(text: str) -> dict[str, Any] | None:
    """Return the last JSON-object line in ``text``, or ``None``."""
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
    return None


class BrainstormManager:
    """Own the visual-companion server as a host-side process.

    The companion server talks to the agent purely through files — it serves
    the newest HTML file the agent writes into ``screen_dir`` and records
    browser clicks into ``state_dir/events`` — while only the human's browser
    needs HTTP.  Running it on the host (outside the sandbox) therefore needs
    no network bridge, is reachable from the human's browser verbatim, and
    ``--open`` can auto-open the browser again.

    Lifetime is anchored to this MCP server process: the launcher is given
    ``--owner-pid`` of this process, so the companion server self-exits within
    its watchdog interval if the Zed session (and hence this server) dies, and
    otherwise after its idle timeout.  No teardown is required.
    """

    def __init__(self, *, root: str) -> None:
        self._root = os.path.realpath(root)
        self._sessions: dict[str, str] = {}  # real project dir -> state dir

    @property
    def root(self) -> str:
        return self._root

    def _resolve_project_dir(self, project_dir: str) -> str:
        """Resolve ``project_dir`` and require it inside the workspace root."""
        if os.path.isabs(project_dir):
            resolved = os.path.realpath(project_dir)
        else:
            resolved = os.path.realpath(os.path.join(self._root, project_dir))
        if resolved != self._root and not resolved.startswith(self._root + os.sep):
            raise ValueError(
                f"project_dir must be inside the workspace root ({self._root}), got {project_dir!r}."
            )
        if not os.path.isdir(resolved):
            raise ValueError(f"project_dir does not exist: {project_dir!r}.")
        return resolved

    def _scan_state_dir(self, project: str) -> str | None:
        """Return the newest session ``state`` dir under ``project``."""
        base = os.path.join(project, ".superpowers", "brainstorm")
        newest: str | None = None
        newest_mtime = 0.0
        try:
            entries = list(os.scandir(base))
        except OSError:
            return None
        for entry in entries:
            if not entry.is_dir():
                continue
            state_dir = os.path.join(entry.path, "state")
            info = os.path.join(state_dir, "server-info")
            try:
                mtime = os.path.getmtime(info)
            except OSError:
                continue
            if mtime > newest_mtime:
                newest, newest_mtime = state_dir, mtime
        return newest

    def _state_dir(self, project: str) -> str | None:
        """Return the current session state dir for ``project``, if any."""
        cached = self._sessions.get(project)
        if cached is not None and os.path.isdir(cached):
            return cached
        found = self._scan_state_dir(project)
        if found is not None:
            self._sessions[project] = found
        return found

    @staticmethod
    def _read_info(state_dir: str) -> dict[str, Any] | None:
        """Read the server's ``server-info`` JSON, or ``None``."""
        try:
            with open(os.path.join(state_dir, "server-info"), encoding="utf-8") as f:
                obj = _last_json_object(f.read())
        except OSError:
            return None
        return obj

    @staticmethod
    def _read_pid(state_dir: str) -> int | None:
        """Read the launcher's recorded server pid, or ``None``."""
        try:
            with open(os.path.join(state_dir, "server.pid"), encoding="utf-8") as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    def _is_alive(self, state_dir: str | None) -> bool:
        """True when a live companion server exists for this session."""
        if state_dir is None or not os.path.isdir(state_dir):
            return False
        if os.path.exists(os.path.join(state_dir, "server-stopped")):
            return False
        pid = self._read_pid(state_dir)
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def start(
        self,
        project_dir: str,
        *,
        port: int | None = None,
        idle_timeout_minutes: int | None = None,
        open_browser: bool = True,
    ) -> dict[str, Any]:
        """Start the companion server, or return the live session's info.

        The launcher script does the backgrounding (``nohup``/``disown``) and
        waits for the server's startup JSON, so this call returns once the
        server is confirmed up (or failed).  A still-running server for this
        project is reused rather than duplicated.
        """
        project = self._resolve_project_dir(project_dir)
        state_dir = self._state_dir(project)
        if self._is_alive(state_dir):
            assert state_dir is not None
            info = dict(self._read_info(state_dir) or {})
            info["type"] = "server-running"
            info["reused"] = True
            return info
        env = dict(os.environ)
        if port is None:
            env.pop("BRAINSTORM_PORT", None)  # let the script reuse/choose a port
        else:
            env["BRAINSTORM_PORT"] = str(int(port))
        argv = ["bash", _find_brainstorm_script(), "--project-dir", project, "--owner-pid", str(os.getpid())]
        if idle_timeout_minutes is not None:
            argv += ["--idle-timeout-minutes", str(int(idle_timeout_minutes))]
        if open_browser:
            argv += ["--open"]
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=_BRAINSTORM_START_TIMEOUT_S, env=env, check=False
        )
        started = _last_json_object(proc.stdout)
        if started is None:
            detail = proc.stdout.strip() or proc.stderr.strip() or f"exit code {proc.returncode}"
            raise RuntimeError(f"brainstorm server failed to start: {detail}")
        if "error" in started:
            return started
        self._sessions[project] = str(started["state_dir"])
        return started

    def status(self, project_dir: str) -> dict[str, Any]:
        """Report whether a companion server is live for ``project_dir``."""
        project = self._resolve_project_dir(project_dir)
        state_dir = self._state_dir(project)
        if state_dir is None:
            return {"type": "not-running", "project_dir": project}
        info = dict(self._read_info(state_dir) or {})
        info["type"] = "server-running" if self._is_alive(state_dir) else "server-stopped"
        return info

    def stop(self, project_dir: str) -> dict[str, Any]:
        """Terminate the companion server for ``project_dir``, if any."""
        project = self._resolve_project_dir(project_dir)
        state_dir = self._state_dir(project)
        if not self._is_alive(state_dir):
            self._sessions.pop(project, None)
            return {"type": "not-running", "project_dir": project}
        assert state_dir is not None
        pid = self._read_pid(state_dir)
        info = self._read_info(state_dir) or {}
        assert pid is not None  # guaranteed by _is_alive
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                break
            time.sleep(0.1)
        self._sessions.pop(project, None)
        return {
            "type": "server-stopped",
            "port": info.get("port"),
            "state_dir": state_dir,
            "project_dir": project,
        }


def _format_brainstorm_info(info: dict[str, Any]) -> str:
    """Render a BrainstormManager result dict as agent-facing markdown."""
    body = "```json\n" + json.dumps(info, indent=2) + "\n```\n"
    kind = info.get("type")
    if kind in ("server-started", "server-running"):
        return body + (
            "Give the human the **complete** `url` verbatim, including the "
            "`?key=…` query string (requests without the key are rejected). "
            "Push a screen by writing an HTML file into `screen_dir`; the "
            "human's clicks are recorded in `state_dir/events`. Check "
            "`status` before referring to the URL again."
        )
    if kind == "not-running":
        return body + "No companion server session exists for this project yet."
    return body


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
    ) -> str:
        """Run a shell command inside the tkt sandbox.

        The result is markdown rendered in the agent UI. Stdout appears in a
        code fence; stderr, when present, is fenced separately under a
        ``stderr:`` label. A nonzero exit code, a timeout, and truncated output
        are each appended as a plain-text note (``exit code N``, ``timed out``,
        ``output truncated``); a clean run adds no note, so the absence of an
        ``exit code N`` note means the command exited 0.

        ``timeout_ms`` defaults to 60s; a request may override it. A command
        that exceeds its timeout is killed and reported with ``timed_out``. The
        command is also hard-capped in the sandbox at ``_DRIVER_OUT_CAP`` bytes
        per stream (a runaway producer is killed); the model sees stdout/stderr
        truncated to ``_BASH_OUTPUT_CHARS`` chars each (head+tail,
        ``truncated`` set if cut). ``description`` is a per-call rationale for
        the human (e.g. shown when a host requests tool confirmation); it is
        not used to change behavior.

        Args:
            command: The shell command to run.
            timeout_ms: Kill the command after this many milliseconds (0 means
                no timeout).
            description: Optional human-readable rationale for this call.
        """
        return format_bash_result(_cap_result(warm.run(command, timeout_ms=timeout_ms), _BASH_OUTPUT_CHARS))

    @mcp.tool()
    def read(
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
        description: str | None = None,  # present for human approvals of tool actions
    ) -> str:
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

    @mcp.tool()
    def ls(
        path: str = ".",
        description: str | None = None,  # present for human approvals of tool actions
    ) -> str:
        """List files and subdirectories at ``path`` inside the tkt sandbox.

        Equivalent to ``ls -laF`` (all entries, long format, type indicators).
        The sandbox blocks ``$HOME`` (so credentials are never exposed) but
        mounts the workspace and the read-only ``~/.agents/skills`` directory.
        ``description`` is a per-call rationale for the human; it does not
        change behavior.

        Args:
            path: Directory to list (default ".").
            description: Optional human-readable rationale for this call.
        """
        return ls_tool(warm, path=path)

    @mcp.tool()
    def glob(
        pattern: str,
        path: str = ".",
        description: str | None = None,  # present for human approvals of tool actions
    ) -> str:
        """Find files under ``path`` matching the glob ``pattern`` in the
        sandbox.

        ``*`` matches within a directory; ``**`` matches recursively across
        directories (bash ``globstar``). Hidden entries require an explicit
        leading dot. Returns one matching path per line. ``description`` is a
        per-call rationale for the human; it does not change behavior.

        Args:
            pattern: The glob pattern to match.
            path: Directory to search under (default ".").
            description: Optional human-readable rationale for this call.
        """
        return glob_tool(warm, pattern=pattern, path=path)

    @mcp.tool()
    def grep(
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        output_mode: str = "content",
        ignore_case: bool = False,
        line_number: bool = False,
        description: str | None = None,  # present for human approvals of tool actions
    ) -> str:
        """Search file contents under ``path`` for a regex in the sandbox.

        ``output_mode`` is ``content`` (default), ``files`` (paths only), or
        ``matches`` (matched text only). ``ignore_case`` is case-insensitive;
        ``line_number`` prefixes line numbers (content mode); ``glob``
        restricts to matching file paths. Binary files are skipped. No matches
        yields an empty ``content`` (not an error). ``description`` is a
        per-call rationale for the human; it does not change behavior.

        Args:
            pattern: The regular expression to search for.
            path: Directory to search under (default ".").
            glob: Optional glob restricting which files are searched.
            output_mode: "content", "files", or "matches".
            ignore_case: Case-insensitive search.
            line_number: Prefix line numbers in content mode.
            description: Optional human-readable rationale for this call.
        """
        return grep_tool(
            warm,
            pattern=pattern,
            path=path,
            glob=glob,
            output_mode=output_mode,
            ignore_case=ignore_case,
            line_number=line_number,
        )

    @mcp.tool()
    def write(
        file_path: str,
        content: str,
        description: str | None = None,  # present for human approvals of tool actions
    ) -> str:
        """Create or overwrite a file inside the tkt sandbox.

        Writes are confined by the sandbox mount model (``.agent/**`` in a
        default workspace, the whole workspace in a shared-worktree
        workspace, the whole repo in single-repo mode).  Missing parent
        directories are created; ``content`` may contain arbitrary bytes.
        Returns a path-only confirmation (clickable). ``description`` is a
        per-call rationale for the human; it does not change behavior.

        Args:
            file_path: Path to create or overwrite (absolute, or relative to
                the sandbox cwd).
            content: The file content to write.
            description: Optional human-readable rationale for this call.
        """
        return write_tool(warm, file_path=file_path, content=content)

    @mcp.tool()
    def edit(
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        description: str | None = None,  # present for human approvals of tool actions
    ) -> str:
        """Edit a file inside the tkt sandbox.

        Replaces ``old_string`` with ``new_string`` (once, or every
        occurrence when ``replace_all`` is true) and returns a per-call
        snapshot diff, or a stats confirmation when the diff exceeds the
        line budget. Confined by the sandbox mount model. ``description``
        is a per-call rationale for the human; it does not change behavior.

        Args:
            file_path: Path to edit (absolute, or relative to the sandbox cwd).
            old_string: The exact text to find.
            new_string: The replacement text.
            replace_all: Replace every occurrence instead of just the first.
            description: Optional human-readable rationale for this call.
        """
        return edit_tool(
            warm,
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
        )

    brainstorm = BrainstormManager(root=cwd)

    @mcp.tool()
    def brainstorm_server(
        action: str,
        project_dir: str = ".",
        port: int | None = None,
        idle_timeout_minutes: int | None = None,
        open_browser: bool = True,
        description: str | None = None,  # present for human approvals of tool actions
    ) -> str:
        """Manage the brainstorming visual-companion server on the host.

        The companion server runs outside the sandbox, where the human's
        browser reaches it directly (no network bridge needed) and can
        auto-open.  You interact with it only through files: write HTML
        screens into ``screen_dir`` and read the human's clicks from
        ``state_dir/events``.  Prefer this tool over launching
        ``start-server.sh`` from ``bash``: the sandbox is network-isolated
        (an in-sandbox server's URL is unreachable) and detached processes
        there are fragile.  See the brainstorming skill's visual companion
        guide for the screen-writing conventions.

        Args:
            action: One of "start", "status", or "stop".
            project_dir: Writable project root for session files, inside the
                workspace (relative paths resolve against the project root);
                screens and events live under
                ``<project_dir>/.superpowers/brainstorm/``.
            port: Fixed host port for "start"; by default a random high port
                is chosen and then reused across restarts of the same
                project.
            idle_timeout_minutes: "start" only; auto-shutdown after this many
                idle minutes (server default 240).
            open_browser: "start" only; auto-open the human's browser on the
                first pushed screen (default True; only ever opens once, and
                not if a tab is already connected).
            description: Optional human-readable rationale for this call.
        """
        action = action.strip().lower()
        try:
            if action == "start":
                info = brainstorm.start(
                    project_dir,
                    port=port,
                    idle_timeout_minutes=idle_timeout_minutes,
                    open_browser=open_browser,
                )
            elif action == "status":
                info = brainstorm.status(project_dir)
            elif action == "stop":
                info = brainstorm.stop(project_dir)
            else:
                return f"brainstorm_server: unknown action {action!r}; use 'start', 'status', or 'stop'."
        except (ValueError, RuntimeError, FileNotFoundError, OSError) as exc:
            return f"brainstorm_server failed: {exc}"
        return _format_brainstorm_info(info)

    todo_store = TodoStore()

    @mcp.tool()
    def todo_write(
        todos: list[TodoItem],
        description: str | None = None,  # present for human approvals of tool actions
    ) -> str:
        """Replace the agent's todo list and return the full new list.

        The caller passes the entire desired list on every call (Claude
        Code-style); the stored list is replaced wholesale, so this is
        idempotent and stateless from the model's perspective. The list is
        held in memory for the life of this server process (not sandboxed —
        it is model bookkeeping with no file access) and is lost if the
        process restarts. ``description`` is a per-call rationale for the
        human; it does not change behavior.

        Args:
            todos: The full desired todo list. Each item has ``content``,
                ``status`` (``pending``, ``in_progress``, ``completed``, or
                ``cancelled``; default ``pending``), and an optional
                ``activeForm`` verb phrase.
            description: Optional human-readable rationale for this call.
        """
        return todo_store.replace(todos)

    mcp.run()
