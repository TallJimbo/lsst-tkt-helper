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

import base64
import difflib
import os
import sys
from collections.abc import Sequence

__all__ = (
    "DIFF_CHAR_CAP",
    "DIFF_LINE_BUDGET",
    "MAX_CONTENT_BYTES",
    "MCPFilesError",
    "edit_op",
    "main",
    "write_op",
)

# Number of unified-diff lines beyond which an `Edit` shows a one-line stats
# confirmation instead of the diff itself.
DIFF_LINE_BUDGET = 100

# Model-facing char cap for a shown `Edit` diff (head+tail truncation).
DIFF_CHAR_CAP = 25_000

# Raw content byte budget for content riding in the `bash -c` argv. Content is
# base64-encoded into a single argv element, so the binding limit is Linux's
# per-argument cap (MAX_ARG_STRLEN, 128 KiB) minus base64's 4/3 inflation and
# command/path overhead. 90_000 leaves clear margin (95 KiB is the empirical
# edge).
MAX_CONTENT_BYTES = 90_000


class MCPFilesError(Exception):
    """A graceful, user-facing failure in a sandbox file operation."""


def _cap_text(text: str, max_chars: int) -> str:
    """Keep head+tail of ``text`` within ``max_chars``, with a dropped
    marker.
    """
    if len(text) <= max_chars:
        return text
    n_head = max_chars // 2
    n_tail = max_chars - n_head
    dropped = len(text) - max_chars
    return text[:n_head] + f"\n... [{dropped} chars truncated] ...\n" + text[-n_tail:]


def _compute_diff(before: str, after: str, path: str) -> tuple[list[str], int, int]:
    """Return the unified diff of before->after plus added/removed counts."""
    diff = list(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=path,
            tofile=path,
        )
    )
    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    return diff, added, removed


def write_op(target: str, content: bytes) -> str:
    """Create or overwrite ``target`` (resolved against cwd) with ``content``.

    Auto-creates missing parent directories. Returns the markdown confirmation
    for the tool card; raises :class:`MCPFilesError` on failure.
    """
    path = os.path.abspath(target)
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)
    except OSError as e:
        raise MCPFilesError(f"Write failed: {e}") from e
    return f"Wrote [`{path}`]({path})"


def edit_op(target: str, old: str, new: str, replace_all: bool = False) -> str:
    """Replace ``old`` with ``new`` in ``target`` and return a markdown
    summary.

    Returns the per-call snapshot diff when it fits the line budget, else a
    one-line stats confirmation. Raises :class:`MCPFilesError` on failure.
    """
    path = os.path.abspath(target)
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise MCPFilesError(f"Edit failed: {e}") from e
    try:
        before = data.decode("utf-8")
    except UnicodeDecodeError:
        raise MCPFilesError(f"Edit failed: file is not valid UTF-8 text at {path}") from None
    count = before.count(old)
    if count == 0:
        raise MCPFilesError(f"Edit failed: pattern not found at {path}")
    if count > 1 and not replace_all:
        raise MCPFilesError(f"Edit failed: pattern matches {count} times at {path} (use replace_all=True)")
    after = before.replace(old, new) if replace_all else before.replace(old, new, 1)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(after)
    except OSError as e:
        raise MCPFilesError(f"Edit failed: {e}") from e
    replacements = count if replace_all else 1
    diff, added, removed = _compute_diff(before, after, path)
    if len(diff) <= DIFF_LINE_BUDGET:
        body = "".join(diff).rstrip("\n")
        return f"Edited [`{path}`]({path}):\n```diff\n{_cap_text(body, DIFF_CHAR_CAP)}\n```"
    return f"Edited [`{path}`]({path}): {replacements} replacements, +{added}/-{removed} lines"


def _dispatch(argv: Sequence[str]) -> str:
    if not argv:
        raise MCPFilesError("mcp_files: missing operation (write|edit)")
    op = argv[0]
    if op == "write":
        if len(argv) < 3:
            raise MCPFilesError("mcp_files: write requires <target> <content_b64>")
        return write_op(argv[1], base64.b64decode(argv[2]))
    if op == "edit":
        if len(argv) < 5:
            raise MCPFilesError("mcp_files: edit requires <target> <old_b64> <new_b64> <replace_all>")
        old = base64.b64decode(argv[2]).decode("utf-8")
        new = base64.b64decode(argv[3]).decode("utf-8")
        return edit_op(argv[1], old, new, argv[4] == "1")
    raise MCPFilesError(f"mcp_files: unknown operation {op!r}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run one sandbox-side file operation; print the markdown result.

    Exits 0 on success and 1 on a graceful failure. Unexpected errors are
    caught and reported gracefully rather than as a traceback, so the MCP
    surface never shows one.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        message = _dispatch(args)
    except MCPFilesError as e:
        print(e)
        return 1
    except Exception as e:  # pragma: no cover - defensive; see _dispatch
        print(f"mcp_files failed: {e}")
        return 1
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
