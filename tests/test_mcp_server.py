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
from unittest import mock

from tkt.mcp_server import (
    BashResult,
    WarmSandbox,
    build_driver_script,
    build_read_command,
    decode_field,
    encode_field,
    parse_result_line,
)


def test_encode_decode_roundtrip():
    """encode_field/decode_field round-trip arbitrary text without newlines."""
    for text in ("hello", "multi\nline\n", "unicode \u00e9", "quote's \" and $vars"):
        assert decode_field(encode_field(text)) == text


def test_parse_result_line():
    """parse_result_line splits 5 space-separated base64 fields."""
    line = " ".join(encode_field(field) for field in ("out", "err", "3", "/some/cwd", "1"))
    parsed = parse_result_line(line)
    assert parsed == {
        "stdout": "out",
        "stderr": "err",
        "exit_code": 3,
        "cwd": "/some/cwd",
        "timed_out": True,
    }


def test_build_driver_script_runs_setup_once():
    """The driver runs setup once, then one fresh bash child per loop iter."""
    script = build_driver_script(["conda activate env", "setup -r .agent"])
    assert "conda activate env" in script
    assert "setup -r .agent" in script
    assert "bash -c --" in script
    assert "base64" in script


def test_build_driver_script_keeps_setup_off_framed_stdout():
    """Setup must never write to the framed stdout channel before the loop.

    The holder's stdout is the pipe ``WarmSandbox.run`` reads result frames
    from. If conda activate or setup emitted anything to stdout at startup,
    the first ``readline()`` would return that stray line and
    ``parse_result_line`` would raise ``ValueError`` on the first ``bash``
    call. The setup block must therefore be redirected away from stdout.
    """
    script = build_driver_script(["conda activate env", "setup -r .agent"])
    pre_loop = script.split("while IFS= read -r cwd_b64", 1)[0]
    assert ">&2" in pre_loop
    # The setup block must be grouped and redirected as a whole; no unguarded
    # stdout write may precede the loop.
    assert pre_loop.count("} >&2") == 1
    assert not any(line.lstrip().startswith(("printf", "echo")) for line in pre_loop.splitlines())
    # The first line of the loop body (which frames results) must come after
    # the setup redirect is closed.
    assert pre_loop.rstrip().endswith("} >&2")


def _fake_proc():
    proc = mock.Mock()
    cwd_field = encode_field("/fake/cwd")
    out_field = encode_field("hello out")
    err_field = encode_field("hello err")
    rc_field = encode_field("0")
    to_field = encode_field("1")
    proc.stdout.readline.return_value = (
        f"{out_field} {err_field} {rc_field} {cwd_field} {to_field}\n"
    ).encode()
    proc.stdin = mock.Mock()
    return proc


def _fake_proc_empty_stdout():
    """Return a fake holder whose first result has an empty stdout field.

    Mirrors a command that produced no stdout (only stderr), the case that
    made the driver emit a leading space before the stderr field.
    """
    proc = mock.Mock()
    cwd_field = encode_field("/fake/cwd")
    err_field = encode_field("ls: cannot access x: No such file or directory\n")
    rc_field = encode_field("2")
    to_field = encode_field("0")
    proc.stdout.readline.return_value = (f" {err_field} {rc_field} {cwd_field} {to_field}\n").encode()
    proc.stdin = mock.Mock()
    return proc


def test_warm_sandbox_run_empty_stdout_field(tmp_path):
    """WarmSandbox.run survives a result line whose stdout field is empty.

    The old strip() removed the leading space of such a line, collapsing the
    5 result fields into 4 and raising ValueError (Malformed result line).
    Only the trailing newline should be removed.
    """
    from tkt.sandbox import Sandbox

    sandbox = Sandbox(command=["opencode", "acp"])
    sandbox.warm_holder_argv = mock.Mock(return_value=["bwrap", "args"])
    with mock.patch("tkt.mcp_server.subprocess.Popen", return_value=_fake_proc_empty_stdout()):
        ws = WarmSandbox(sandbox, repo_dir=str(tmp_path), cwd="/start")
        result = ws.run("ls /nonexistent", timeout_ms=500)
        assert result.stdout == ""
        assert "No such file" in result.stderr
        assert result.exit_code == 2
        assert result.timed_out is False
        assert ws.cwd == "/fake/cwd"


def test_warm_sandbox_run_frames_command_and_tracks_cwd(tmp_path):
    """WarmSandbox.run frames cwd+command+timeout and updates cwd/timed_out."""
    from tkt.sandbox import Sandbox

    sandbox = Sandbox(command=["opencode", "acp"])  # real Sandbox; _start asserts type
    sandbox.warm_holder_argv = mock.Mock(return_value=["bwrap", "args"])
    with mock.patch("tkt.mcp_server.subprocess.Popen", return_value=_fake_proc()):
        ws = WarmSandbox(sandbox, repo_dir=str(tmp_path), cwd="/start")
        result = ws.run("echo hi", timeout_ms=500)
        assert isinstance(result, BashResult)
        assert result.stdout == "hello out"
        assert result.stderr == "hello err"
        assert result.exit_code == 0
        assert result.timed_out is True
        assert ws.cwd == "/fake/cwd"
        # written stdin payload: three base64 lines (cwd, command, timeout_ms)
        payload = ws._proc.stdin.write.call_args[0][0].decode()
        fields = payload.splitlines()
        assert decode_field(fields[0]) == "/start"
        assert decode_field(fields[1]) == "echo hi"
        assert decode_field(fields[2]) == "500"
        # default timeout and negative-clamp timeout are also framed correctly
        ws.run("echo hi")
        payload = ws._proc.stdin.write.call_args[0][0].decode()
        assert decode_field(payload.splitlines()[2]) == "60000"
        ws.run("echo hi", timeout_ms=-1)
        payload = ws._proc.stdin.write.call_args[0][0].decode()
        assert decode_field(payload.splitlines()[2]) == "0"


def test_mcp_server_run_server_builds_warm_sandbox(tmp_path, monkeypatch):
    """run_server constructs a WarmSandbox with the detected repo cwd/mode."""
    from tkt import mcp_server

    captured = {}

    def fake_warm(*args, **kwargs):
        captured["kwargs"] = kwargs
        return mock.Mock()

    monkeypatch.setattr(mcp_server, "WarmSandbox", fake_warm)
    # mcp.run() drives the stdio transport, which would block/error on the
    # test runner's stdin; neutralize it so we only exercise construction.
    monkeypatch.setattr(mcp_server.FastMCP, "run", lambda self: None)
    sandbox = mock.Mock()
    mcp_server.run_server(sandbox, cwd=str(tmp_path), repo_dir=str(tmp_path))
    assert captured["kwargs"]["repo_dir"] == str(tmp_path)


def test_build_driver_script_hardening():
    """Driver exports functions, uses a fresh bash -c child, blocks stdin, and
    applies a coreutils timeout with a timed_out flag.
    """
    script = build_driver_script(["setup -r .agent"])
    # exported functions so children can call setup/conda
    assert "compgen -A function" in script
    assert "export -f" in script
    # fresh non-login child, stdin detached from the framing pipe
    assert 'bash -c -- "$cmd" </dev/null' in script
    assert "bash -lc" not in script
    # timeout wrapper (default TERM, kill-after escalation) and the
    # 124/137 -> timed_out mapping
    assert 'timeout --kill-after=5 "${secs}s"' in script
    assert 'if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then to=1; else to=0; fi' in script


def test_build_read_command_quotes_path_and_slices():
    """build_read_command quotes the path and selects the slice."""
    cmd = build_read_command("/a b/c.txt", offset=0, limit=2000)
    assert "'/a b/c.txt'" in cmd  # shlex.quote wraps in single quotes
    assert 'sed -n "1,2000p"' in cmd
    assert '"$f"' in cmd
    assert "wc -l" in cmd
    assert "base64 -w0" in cmd


def test_build_read_command_respects_offset():
    """Offset shifts the 1-based sed range and does not renumber."""
    cmd = build_read_command("/tmp/x.txt", offset=5, limit=3)
    assert 'sed -n "6,8p"' in cmd


def test_read_tool_numbers_lines_and_no_truncation():
    """A full slice is numbered with absolute line numbers, not truncated."""
    from tkt.mcp_server import read_tool

    sl = b"a\nbb\nccc\n"
    warm = mock.Mock()
    warm.run.return_value = BashResult(
        stdout=base64.b64encode(sl).decode(), stderr="READ_TOTAL 3\n", exit_code=0
    )
    res = read_tool(warm, file_path="/tmp/x.txt")
    assert res.content == "1\ta\n2\tbb\n3\tccc"
    assert res.truncated is False


def test_read_tool_truncation_note():
    """A partial slice appends a '... (N more lines)' note; truncated=True."""
    from tkt.mcp_server import read_tool

    sl = b"l1\nl2\nl3\nl4\nl5\n"
    warm = mock.Mock()
    warm.run.return_value = BashResult(
        stdout=base64.b64encode(sl).decode(), stderr="READ_TOTAL 5\n", exit_code=0
    )
    res = read_tool(warm, file_path="/tmp/x.txt", offset=0, limit=2)
    assert res.content == "1\tl1\n2\tl2\n... (3 more lines)"
    assert res.truncated is True


def test_read_tool_offset_numbers_from_absolute():
    """Offset skips leading lines but numbers from the true line number."""
    from tkt.mcp_server import read_tool

    sl = b"l3\nl4\n"
    warm = mock.Mock()
    warm.run.return_value = BashResult(
        stdout=base64.b64encode(sl).decode(), stderr="READ_TOTAL 5\n", exit_code=0
    )
    res = read_tool(warm, file_path="/tmp/x.txt", offset=2, limit=2)
    assert res.content == "3\tl3\n4\tl4"
    assert res.truncated is True  # 2 returned + 2 offset = 4 < 5


def test_read_tool_missing_file_error():
    """A nonzero exit propagates a read: ... error in content."""
    from tkt.mcp_server import read_tool

    warm = mock.Mock()
    warm.run.return_value = BashResult(
        stdout="", stderr="read: no such file or not a regular file: /nope\n", exit_code=1
    )
    res = read_tool(warm, file_path="/nope")
    assert res.content.startswith("read: ")
    assert "no such file" in res.content
    assert res.truncated is False


def test_read_tool_binary_file():
    """A non-UTF-8 slice yields a binary-file message instead of a crash."""
    from tkt.mcp_server import read_tool

    raw = b"\xff\xfe\x00binary"
    warm = mock.Mock()
    warm.run.return_value = BashResult(
        stdout=base64.b64encode(raw).decode(), stderr="READ_TOTAL 1\n", exit_code=0
    )
    res = read_tool(warm, file_path="/tmp/blob")
    assert "binary" in res.content
    assert res.truncated is False


def test_read_tool_clamps_offset_and_limit():
    """Offset clamps to >=0, limit to >=1."""
    from tkt.mcp_server import read_tool

    warm = mock.Mock()
    warm.run.return_value = BashResult(
        stdout=base64.b64encode(b"x\n").decode(), stderr="READ_TOTAL 1\n", exit_code=0
    )
    res = read_tool(warm, file_path="/tmp/x.txt", offset=-5, limit=0)
    assert res.content == "1\tx"
