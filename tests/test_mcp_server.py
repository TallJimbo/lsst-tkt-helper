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
import subprocess as sp
from unittest import mock

from tkt.mcp_files import MAX_CONTENT_BYTES
from tkt.mcp_server import (
    _MAX_OUTPUT_CHARS,
    BashResult,
    TodoItem,
    TodoStore,
    WarmSandbox,
    _cap_result,
    build_driver_script,
    build_edit_command,
    build_glob_command,
    build_grep_command,
    build_ls_command,
    build_read_command,
    build_write_command,
    decode_field,
    edit_tool,
    encode_field,
    glob_tool,
    grep_tool,
    ls_tool,
    parse_result_line,
    read_char_cap,
    truncate_output,
    write_tool,
)


def test_read_char_cap_scales_with_limit():
    """Cap grows with requested lines, hard-capped at _MAX_OUTPUT_CHARS."""
    assert read_char_cap(1) == 110
    assert read_char_cap(100) == 11000
    assert read_char_cap(2000) == 25000
    assert read_char_cap(10**6) == 25000
    assert read_char_cap(2000) == _MAX_OUTPUT_CHARS


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
    # the setup redirect is closed. The OC= cap line is a variable assignment,
    # not an output, so it sits harmlessly between the redirect and the loop.
    assert pre_loop.rstrip().endswith('OC="50000"')


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


def test_build_read_command_bounds_line_length_and_output():
    """Fold bounds per-line memory and head -c bounds output before base64."""
    from tkt.mcp_server import _READ_BYTE_CAP

    cmd = build_read_command("/a b/c.txt", offset=0, limit=2000)
    assert f'fold -b -w "{_READ_BYTE_CAP}"' in cmd
    assert f'head -c "{_READ_BYTE_CAP}"' in cmd
    assert "| base64 -w0" in cmd
    # The fold must appear before sed in the pipeline.
    assert cmd.index("fold -b -w") < cmd.index("sed -n")


def test_read_tool_numbers_lines_and_no_truncation():
    """A full slice is numbered with absolute line numbers, not truncated."""
    from tkt.mcp_server import read_tool

    sl = b"a\nbb\nccc\n"
    warm = mock.Mock()
    warm.run.return_value = BashResult(
        stdout=base64.b64encode(sl).decode(), stderr="READ_TOTAL 3\n", exit_code=0
    )
    res = read_tool(warm, file_path="/tmp/x.txt")
    assert res == ("[`/tmp/x.txt`](/tmp/x.txt)\n```text\n1\ta\n2\tbb\n3\tccc\n```")


def test_read_tool_truncation_note():
    """A partial slice appends a '... (N more lines)' note."""
    from tkt.mcp_server import read_tool

    sl = b"l1\nl2\nl3\nl4\nl5\n"
    warm = mock.Mock()
    warm.run.return_value = BashResult(
        stdout=base64.b64encode(sl).decode(), stderr="READ_TOTAL 5\n", exit_code=0
    )
    res = read_tool(warm, file_path="/tmp/x.txt", offset=0, limit=2)
    assert res == ("[`/tmp/x.txt`](/tmp/x.txt)\n```text\n1\tl1\n2\tl2\n... (3 more lines)\n```")


def test_read_tool_offset_numbers_from_absolute():
    """Offset skips leading lines but numbers from the true line number."""
    from tkt.mcp_server import read_tool

    sl = b"l3\nl4\n"
    warm = mock.Mock()
    warm.run.return_value = BashResult(
        stdout=base64.b64encode(sl).decode(), stderr="READ_TOTAL 5\n", exit_code=0
    )
    res = read_tool(warm, file_path="/tmp/x.txt", offset=2, limit=2)
    assert "3\tl3\n4\tl4" in res


def test_read_tool_missing_file_error():
    """A nonzero exit propagates a read: ... error in content."""
    from tkt.mcp_server import read_tool

    warm = mock.Mock()
    warm.run.return_value = BashResult(
        stdout="", stderr="read: no such file or not a regular file: /nope\n", exit_code=1
    )
    res = read_tool(warm, file_path="/nope")
    assert res.startswith("read: ")
    assert "no such file" in res


def test_read_tool_binary_file():
    """A non-UTF-8 slice yields a binary-file message instead of a crash."""
    from tkt.mcp_server import read_tool

    raw = b"\xff\xfe\x00binary"
    warm = mock.Mock()
    warm.run.return_value = BashResult(
        stdout=base64.b64encode(raw).decode(), stderr="READ_TOTAL 1\n", exit_code=0
    )
    res = read_tool(warm, file_path="/tmp/blob")
    assert "binary" in res


def test_read_tool_clamps_offset_and_limit():
    """Offset clamps to >=0, limit to >=1."""
    from tkt.mcp_server import read_tool

    warm = mock.Mock()
    warm.run.return_value = BashResult(
        stdout=base64.b64encode(b"x\n").decode(), stderr="READ_TOTAL 1\n", exit_code=0
    )
    res = read_tool(warm, file_path="/tmp/x.txt", offset=-5, limit=0)
    assert "1\tx" in res


def test_read_tool_byte_cap_truncates_long_single_line():
    """A single line longer than the per-call cap is head+tail truncated."""
    from tkt.mcp_server import read_tool

    sl = ("x" * 50000 + "\n").encode()
    warm = mock.Mock()
    warm.run.return_value = BashResult(
        stdout=base64.b64encode(sl).decode(), stderr="READ_TOTAL 1\n", exit_code=0
    )
    res = read_tool(warm, file_path="/tmp/x.txt", limit=100)  # cap = 11000
    assert "chars truncated" in res
    assert len(res) < 12000


def test_read_tool_byte_cap_not_truncated_when_within_budget():
    """A read within the char cap is not byte-truncated."""
    from tkt.mcp_server import read_tool

    sl = b"hello\n"
    warm = mock.Mock()
    warm.run.return_value = BashResult(
        stdout=base64.b64encode(sl).decode(), stderr="READ_TOTAL 1\n", exit_code=0
    )
    res = read_tool(warm, file_path="/tmp/x.txt", limit=1)
    assert "chars truncated" not in res
    assert "1\thello" in res


def test_read_tool_tolerates_byte_cut_multibyte_utf8():
    """A byte-cap cut landing mid-multibyte-char returns content, not
    'binary'.
    """
    from tkt.mcp_server import read_tool

    full = ("\u4e2d" * 12000).encode()  # 36000 bytes of 3-byte UTF-8
    cut = full[:31999]  # ends mid-character (31999 % 3 == 1)
    warm = mock.Mock()
    warm.run.return_value = BashResult(
        stdout=base64.b64encode(cut).decode(), stderr="READ_TOTAL 1\n", exit_code=0
    )
    res = read_tool(warm, file_path="/tmp/x.txt", limit=100)
    assert "binary" not in res


def test_truncate_output_passthrough_when_within_cap():
    """Text within max_chars is returned unchanged with truncated=False."""
    text = "a" * 100
    out, truncated = truncate_output(text, 5000)
    assert out == text
    assert truncated is False


def test_truncate_output_at_cap_not_truncated():
    """Text exactly at max_chars is not truncated."""
    text = "a" * 5000
    out, truncated = truncate_output(text, 5000)
    assert out == text
    assert truncated is False


def test_truncate_output_head_and_tail_with_marker():
    """Oversized text keeps head+tail and the dropped-count marker."""
    text = "A" * 7000
    out, truncated = truncate_output(text, 5000)
    assert truncated is True
    assert out == "A" * 2500 + "\n... [2000 chars truncated] ...\n" + "A" * 2500


def test_cap_result_truncates_and_sets_flag():
    """_cap_result cuts an oversized stream and sets truncated when cut."""
    result = BashResult(stdout="A" * 7000, stderr="ok", exit_code=0)
    capped = _cap_result(result, 5000)
    assert capped.truncated is True
    assert len(capped.stdout) < 7000
    assert "truncated]" in capped.stdout
    assert capped.stderr == "ok"
    assert capped.exit_code == 0
    assert capped.timed_out is False


def test_cap_result_no_truncation_preserves_fields():
    """A within-cap result passes through unchanged, truncated=False."""
    result = BashResult(stdout="hi", stderr="err", exit_code=3, timed_out=True)
    capped = _cap_result(result, 5000)
    assert capped.stdout == "hi"
    assert capped.stderr == "err"
    assert capped.exit_code == 3
    assert capped.timed_out is True
    assert capped.truncated is False


def test_format_bash_result_success():
    """A clean success fences only stdout, with no status notes."""
    from tkt.mcp_server import format_bash_result

    res = format_bash_result(BashResult(stdout="hello out", stderr="", exit_code=0))
    assert res == "```text\nhello out\n```"


def test_format_bash_result_fences_stderr_and_notes_status():
    """Fence stderr separately; note exit code, timeout and truncation."""
    from tkt.mcp_server import format_bash_result

    res = format_bash_result(
        BashResult(stdout="", stderr="boom", exit_code=2, timed_out=True, truncated=True)
    )
    assert res == ("stderr:\n```text\nboom\n```\n\nexit code 2\ntimed out\noutput truncated")


def test_format_bash_result_empty_is_empty():
    """No output and clean status yields an empty string."""
    from tkt.mcp_server import format_bash_result

    assert format_bash_result(BashResult(stdout="", stderr="", exit_code=0)) == ""


def _run_driver(tmp_path, command, timeout_ms="0"):
    """Run build_driver_script([]) under host bash; return the parsed frame."""
    script = tmp_path / "driver.sh"
    script.write_text(build_driver_script([]), encoding="utf-8")
    proc = sp.Popen(
        ["bash", str(script)],
        stdin=sp.PIPE,
        stdout=sp.PIPE,
        stderr=sp.PIPE,
        text=True,
    )
    payload = f"{encode_field(str(tmp_path))}\n{encode_field(command)}\n{encode_field(timeout_ms)}\n"
    out, err = proc.communicate(payload)
    assert proc.returncode == 0, err
    # result line may begin with a space when stdout is empty; only strip the
    # trailing newline.
    return parse_result_line(out.rstrip("\n"))


def test_driver_hard_caps_oversized_stdout_and_kills_producer(tmp_path):
    """A multi-MB stdout is capped at _DRIVER_OUT_CAP; producer killed."""
    frame = _run_driver(tmp_path, "seq 1 1000000")
    assert len(frame["stdout"]) <= 50_000
    assert "output hard-capped" in frame["stderr"]
    assert frame["exit_code"] != 0  # killed by SIGPIPE when head closed the pipe


def test_driver_preserves_small_output_and_rc(tmp_path):
    """Within-cap output passes through unchanged; the command's rc is kept."""
    frame = _run_driver(tmp_path, "printf 'hi'")
    assert frame["stdout"] == "hi"
    assert frame["stderr"] == ""
    assert frame["exit_code"] == 0

    frame = _run_driver(tmp_path, "exit 7")
    assert frame["stdout"] == ""
    assert frame["exit_code"] == 7


def test_build_ls_command_quotes_path_and_lists():
    """build_ls_command lists with -laF and quotes the path."""
    cmd = build_ls_command("/a b")
    assert "ls -laF --" in cmd
    assert "'/a b'" in cmd


def test_build_glob_command_globstar_nullglob_and_quotes():
    """build_glob_command sets globstar/nullglob and quotes path+pattern."""
    cmd = build_glob_command("**/*.py", "/a b")
    assert "cd '/a b'" in cmd
    assert "shopt -s globstar nullglob" in cmd
    assert "for f in $pattern" in cmd
    assert '[ -e "$f" ]' in cmd
    assert "**/*.py" in cmd
    # pattern is assigned to a quoted var (injection-safe), not inlined
    assert "'**/*.py'" in cmd


def test_build_grep_command_defaults_content():
    """build_grep_command content mode has -rEIH and --exclude-dir=.git."""
    cmd = build_grep_command("foo", "src")
    assert "grep -r -E -I -H" in cmd
    assert "--exclude-dir=.git" in cmd
    assert "-e foo" in cmd
    assert " -- src" in cmd


def test_build_grep_command_output_modes_and_flags():
    """output_mode/ignore_case/line_number map to grep flags; glob to
    --include.
    """
    cmd = build_grep_command("x", "src", glob="*.py", output_mode="files", ignore_case=True)
    assert "-l" in cmd and "-i" in cmd
    assert "--include='*.py'" in cmd
    cmd = build_grep_command("x", "src", output_mode="matches")
    assert "-o" in cmd
    cmd = build_grep_command("x", "src", line_number=True)
    assert "-n" in cmd


def test_ls_tool_success():
    """ls_tool fences stdout as markdown, not truncated."""
    warm = mock.Mock()
    warm.run.return_value = BashResult(stdout="a\nb\n", stderr="", exit_code=0)
    res = ls_tool(warm, path=".")
    assert res == "```text\na\nb\n```"


def test_ls_tool_error():
    """ls_tool surfaces stderr on nonzero exit."""
    warm = mock.Mock()
    warm.run.return_value = BashResult(stdout="", stderr="ls: cannot access nope\n", exit_code=2)
    res = ls_tool(warm, path="nope")
    assert res.startswith("ls: ")
    assert "cannot access" in res


def test_glob_tool_success():
    """glob_tool fences one path per line as markdown."""
    warm = mock.Mock()
    warm.run.return_value = BashResult(stdout="a.py\nb.py\n", stderr="", exit_code=0)
    res = glob_tool(warm, pattern="*.py", path=".")
    assert res == "```text\na.py\nb.py\n```"


def test_glob_tool_no_match_is_empty_success():
    """A nullglob no-match yields empty content with rc 0, not an error."""
    warm = mock.Mock()
    warm.run.return_value = BashResult(stdout="", stderr="", exit_code=0)
    res = glob_tool(warm, pattern="*.zzz", path=".")
    assert res == ""


def test_grep_tool_content():
    """grep_tool content mode fences the match lines as markdown."""
    warm = mock.Mock()
    warm.run.return_value = BashResult(stdout="src/a.py:3: foo\n", stderr="", exit_code=0)
    res = grep_tool(warm, pattern="foo", path=".")
    assert res == "```text\nsrc/a.py:3: foo\n```"


def test_grep_tool_no_matches_normalized():
    """Grep rc 1 (no matches) becomes empty content, not an error."""
    warm = mock.Mock()
    warm.run.return_value = BashResult(stdout="", stderr="", exit_code=1)
    res = grep_tool(warm, pattern="none", path=".")
    assert res == ""


def test_grep_tool_error():
    """Grep rc >1 surfaces stderr as an error."""
    warm = mock.Mock()
    warm.run.return_value = BashResult(stdout="", stderr="grep: bad\n", exit_code=2)
    res = grep_tool(warm, pattern="x", path=".")
    assert res.startswith("grep: ")
    assert "bad" in res


def test_ls_tool_truncation():
    """ls_tool caps oversized content and flags truncation."""
    warm = mock.Mock()
    warm.run.return_value = BashResult(stdout="A" * 100000 + "\n", stderr="", exit_code=0)
    res = ls_tool(warm, path=".")
    assert "chars truncated" in res


def test_todo_item_defaults_status_pending():
    """TodoItem defaults to status='pending' and no activeForm."""
    item = TodoItem(content="Do the thing")
    assert item.status == "pending"
    assert item.activeForm is None


def test_todo_item_fields_passthrough():
    """content/status/activeForm round-trip through TodoItem."""
    item = TodoItem(content="Build", status="in_progress", activeForm="Building")
    assert item.content == "Build"
    assert item.status == "in_progress"
    assert item.activeForm == "Building"


def test_todo_store_replace_returns_markdown_checklist():
    """replace() stores the list and returns it as a markdown checklist."""
    store = TodoStore()
    result = store.replace([TodoItem(content="a"), TodoItem(content="b")])
    assert result == "- [ ] a\n- [ ] b"


def test_todo_store_replace_overwrites_previous():
    """A later replace() fully replaces the prior list."""
    store = TodoStore()
    store.replace([TodoItem(content="a"), TodoItem(content="b")])
    result = store.replace([TodoItem(content="c")])
    assert result == "- [ ] c"


def test_todo_store_empty_clears():
    """Replacing with an empty list clears the stored list."""
    store = TodoStore()
    store.replace([TodoItem(content="a")])
    result = store.replace([])
    assert result == ""


def test_todo_store_renders_all_statuses():
    """Each status maps to a distinct checklist glyph; activeForm is shown."""
    store = TodoStore()
    result = store.replace(
        [
            TodoItem(content="done", status="completed"),
            TodoItem(content="doing", status="in_progress", activeForm="Building"),
            TodoItem(content="plain", status="in_progress"),
            TodoItem(content="scrapped", status="cancelled"),
        ]
    )
    assert result == ("- [x] done\n- [ ] doing (Building)\n- [ ] plain\n- [~] ~~scrapped~~")


def test_build_write_command_quotes_path_and_b64_content():
    """build_write_command embeds the quoted path and base64 content."""
    cmd = build_write_command("/a b/f.py", "x y\n")
    assert "python -m tkt.mcp_files write" in cmd
    assert "'/a b/f.py'" in cmd
    assert base64.b64encode(b"x y\n").decode("ascii") in cmd


def test_build_edit_command_flags_replace_all():
    """build_edit_command encodes old/new and appends the replace_all flag."""
    cmd = build_edit_command("f.py", "old", "new", True)
    assert "python -m tkt.mcp_files edit" in cmd
    assert base64.b64encode(b"old").decode("ascii") in cmd
    assert base64.b64encode(b"new").decode("ascii") in cmd
    assert cmd.endswith(" 1")


def test_write_tool_success():
    """write_tool returns the module's stdout confirmation on success."""
    warm = mock.Mock()
    warm.run.return_value = BashResult(stdout="Wrote [`/x`](/x)\n", stderr="", exit_code=0)
    res = write_tool(warm, file_path="f.py", content="hi")
    assert res == "Wrote [`/x`](/x)"


def test_write_tool_surfaces_module_error():
    """A graceful module failure is returned verbatim from stdout."""
    warm = mock.Mock()
    warm.run.return_value = BashResult(stdout="Write failed: boom\n", stderr="", exit_code=1)
    res = write_tool(warm, file_path="f.py", content="hi")
    assert "Write failed" in res


def test_write_tool_stderr_fallback_on_empty_stdout():
    """Empty stdout falls back to stderr when the run fails."""
    warm = mock.Mock()
    warm.run.return_value = BashResult(stdout="", stderr="boom", exit_code=1)
    res = write_tool(warm, file_path="f.py", content="hi")
    assert "boom" in res


def test_write_tool_too_large_does_not_run():
    """Oversized content is rejected without invoking the sandbox."""
    warm = mock.Mock()
    res = write_tool(warm, file_path="f.py", content="x" * (MAX_CONTENT_BYTES + 1))
    assert "content too large" in res
    warm.run.assert_not_called()


def test_edit_tool_success():
    """edit_tool returns the module's edit summary on success."""
    warm = mock.Mock()
    warm.run.return_value = BashResult(stdout="Edited [`/x`](/x)\n", stderr="", exit_code=0)
    res = edit_tool(warm, file_path="f.py", old_string="a", new_string="b")
    assert res == "Edited [`/x`](/x)"


def test_edit_tool_too_large_does_not_run():
    """Oversized old/new content is rejected without invoking the sandbox."""
    warm = mock.Mock()
    big = "x" * (MAX_CONTENT_BYTES + 1)
    res = edit_tool(warm, file_path="f.py", old_string=big, new_string="b")
    assert "old_string too large" in res
    warm.run.assert_not_called()
