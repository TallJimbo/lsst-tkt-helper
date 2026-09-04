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

import pytest

from tkt.mcp_files import DIFF_CHAR_CAP, MCPFilesError, edit_op, main, write_op


def test_write_creates_file_with_parents(tmp_path):
    """Create a file at a nested path, auto-creating parent directories."""
    target = tmp_path / "a" / "b" / "f.txt"
    msg = write_op(str(target), b"hello\n")
    assert msg == f"Wrote [`{target.resolve()}`]({target.resolve()})"
    assert target.read_bytes() == b"hello\n"


def test_write_overwrites_existing(tmp_path):
    """Overwrite an existing file's contents."""
    target = tmp_path / "f.txt"
    target.write_bytes(b"old")
    write_op(str(target), b"new")
    assert target.read_bytes() == b"new"


def test_write_preserves_arbitrary_bytes(tmp_path):
    """Write arbitrary (including non-UTF-8) bytes losslessly."""
    target = tmp_path / "bin"
    blob = b"\x00\xff\x10\x00\n"
    write_op(str(target), blob)
    assert target.read_bytes() == blob


def test_write_resolves_relative_to_cwd(tmp_path, monkeypatch):
    """Resolve a relative target against the current working directory."""
    monkeypatch.chdir(tmp_path)
    msg = write_op("rel.txt", b"x")
    assert msg == f"Wrote [`{tmp_path / 'rel.txt'}`]({tmp_path / 'rel.txt'})"
    assert (tmp_path / "rel.txt").read_bytes() == b"x"


def test_edit_replaces_first_occurrence(tmp_path):
    """Replace only the first occurrence of the pattern."""
    target = tmp_path / "f.txt"
    target.write_text("aaa bbb")
    msg = edit_op(str(target), "aaa", "XXX")
    assert "Edited" in msg
    assert target.read_text() == "XXX bbb"


def test_edit_replace_all_replaces_every_occurrence(tmp_path):
    """Replace every occurrence when replace_all is set."""
    target = tmp_path / "f.txt"
    target.write_text("aaa aaa aaa")
    msg = edit_op(str(target), "aaa", "X", replace_all=True)
    assert "Edited" in msg
    assert target.read_text() == "X X X"


def test_edit_pattern_not_found(tmp_path):
    """Raise when the pattern is absent from the file."""
    target = tmp_path / "f.txt"
    target.write_text("abc")
    with pytest.raises(MCPFilesError, match="pattern not found"):
        edit_op(str(target), "zzz", "y")


def test_edit_multiple_matches_requires_replace_all(tmp_path):
    """Raise when multiple matches need replace_all."""
    target = tmp_path / "f.txt"
    target.write_text("x x x")
    with pytest.raises(MCPFilesError, match="matches 3 times"):
        edit_op(str(target), "x", "y")


def test_edit_non_utf8_rejected(tmp_path):
    """Reject files that are not valid UTF-8."""
    target = tmp_path / "f.bin"
    target.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(MCPFilesError, match="not valid UTF-8"):
        edit_op(str(target), "x", "y")


def test_edit_small_diff_shown(tmp_path):
    """Show the diff when it fits the line budget."""
    target = tmp_path / "f.txt"
    target.write_text("one\ntwo\n")
    msg = edit_op(str(target), "two", "TWO")
    assert "```diff" in msg


def test_edit_large_diff_uses_stats(tmp_path):
    """Use a one-line stats summary for oversized diffs."""
    target = tmp_path / "f.txt"
    target.write_text("x\n" * 200)
    msg = edit_op(str(target), "x", "y", replace_all=True)
    assert "```diff" not in msg
    assert "200 replacements" in msg
    assert "+200/-200" in msg


def test_edit_char_cap_truncates_long_lines(tmp_path):
    """Cap a diff with few but pathologically long lines to the char budget."""
    target = tmp_path / "f.txt"
    target.write_text("a" * 30_000 + "MARKER" + "b" * 30_000)
    msg = edit_op(str(target), "MARKER", "X")
    assert "\n... [" in msg
    assert " chars truncated] ...\n" in msg
    body = msg.split("```diff\n", 1)[1].rsplit("\n```", 1)[0]
    assert len(body) <= DIFF_CHAR_CAP + 100


def test_main_write_success(capsys, tmp_path, monkeypatch):
    """Drive a write through the CLI entry point."""
    monkeypatch.chdir(tmp_path)
    rc = main(["write", "out.txt", base64.b64encode(b"hi").decode("ascii")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Wrote" in out
    assert (tmp_path / "out.txt").read_bytes() == b"hi"


def test_main_edit_error_returns_1(capsys, tmp_path):
    """Return 1 and print the message when the edit pattern is missing."""
    target = tmp_path / "f.txt"
    target.write_text("abc")
    rc = main(
        [
            "edit",
            str(target),
            base64.b64encode(b"zzz").decode("ascii"),
            base64.b64encode(b"y").decode("ascii"),
            "0",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "pattern not found" in out


def test_main_unknown_op_returns_1(capsys):
    """Return 1 for an unrecognized operation."""
    assert main(["nope"]) == 1
