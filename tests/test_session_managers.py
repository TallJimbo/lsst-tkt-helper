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

"""Tests for the shared host-side session managers."""

from __future__ import annotations

import json
import os
import subprocess
import subprocess as sp
import time
from pathlib import Path
from unittest import mock

import pytest

from tkt._sessionmanagers import (
    PlannotatorManager,
    _match,
    _sessions_listing,
    cleanup_workspace_sessions,
    plannotator_result_dir,
    sweep_session_registry,
)


def test_brainstorm_manager_moved() -> None:
    """BrainstormManager lives in _sessionmanagers and is re-exported."""
    from tkt import _sessionmanagers, mcp_server

    assert _sessionmanagers.BrainstormManager is mcp_server.BrainstormManager


# ========== BrainstormManager (host-side visual-companion server) ==========


def _brainstorm_session(root, name, *, pid=None, stopped=False, info=None):
    """Create a fake session dir laid out like start-server.sh does.

    ``stopped`` writes the ``server-stopped`` marker: pass a dict for the
    JSON the server's shutdown writes (``{"reason": ..., "timestamp": ...}``).
    """
    base = root / ".superpowers" / "brainstorm" / name
    state = base / "state"
    content = base / "content"
    state.mkdir(parents=True)
    content.mkdir()
    if info is None:
        info = {
            "type": "server-started",
            "port": 52341,
            "url": "http://localhost:52341/?key=secretkey",
            "screen_dir": str(content),
            "state_dir": str(state),
        }
    (state / "server-info").write_text(json.dumps(info) + "\n")
    if pid is not None:
        (state / "server.pid").write_text(str(pid) + "\n")
    if stopped:
        payload = stopped if isinstance(stopped, dict) else {}
        (state / "server-stopped").write_text(json.dumps(payload) + "\n")
    return state


def test_brainstorm_rejects_project_outside_workspace(tmp_path):
    """project_dir must resolve inside the workspace root."""
    from tkt._sessionmanagers import BrainstormManager

    mgr = BrainstormManager(root=str(tmp_path))
    try:
        mgr.status("/etc")
        raise AssertionError("expected ValueError for absolute path outside root")
    except ValueError:
        pass
    outside = tmp_path.parent
    try:
        mgr.status(os.path.relpath(outside, tmp_path))
        raise AssertionError("expected ValueError for relative path escaping root")
    except ValueError:
        pass


def test_brainstorm_rejects_missing_project_dir(tmp_path):
    """A nonexistent project_dir is rejected."""
    from tkt._sessionmanagers import BrainstormManager

    mgr = BrainstormManager(root=str(tmp_path))
    try:
        mgr.status("does-not-exist")
        raise AssertionError("expected ValueError for nonexistent dir")
    except ValueError:
        pass


def test_brainstorm_start_reuses_live_server(tmp_path):
    """start() returns the live session's info without relaunching."""
    from tkt._sessionmanagers import BrainstormManager

    _brainstorm_session(tmp_path, "111-111", pid=os.getpid())
    mgr = BrainstormManager(root=str(tmp_path))
    with mock.patch("tkt._sessionmanagers.subprocess.run") as run:
        info = mgr.start(".")
    run.assert_not_called()
    assert info["type"] == "server-running"
    assert info["reused"] is True
    assert info["url"].endswith("?key=secretkey")


def test_brainstorm_start_launches_launcher(tmp_path):
    """start() launches detached, with owner monitoring and idle cap off."""
    from tkt._sessionmanagers import BrainstormManager

    mgr = BrainstormManager(root=str(tmp_path))
    started = {
        "type": "server-started",
        "port": 52999,
        "url": "http://localhost:52999/?key=k2",
        "screen_dir": str(tmp_path / ".superpowers" / "brainstorm" / "9" / "content"),
        "state_dir": str(tmp_path / ".superpowers" / "brainstorm" / "9" / "state"),
    }
    completed = sp.CompletedProcess(args=[], returncode=0, stdout=json.dumps(started) + "\n", stderr="")
    with (
        mock.patch("tkt._sessionmanagers.subprocess.run", return_value=completed) as run,
        mock.patch("tkt._sessionmanagers._find_brainstorm_script", return_value="/fake/start-server.sh"),
    ):
        info = mgr.start(".", port=52999)
    assert info == started
    argv = run.call_args[0][0]
    env = run.call_args.kwargs["env"]
    assert argv[:2] == ["bash", "/fake/start-server.sh"]
    assert argv[argv.index("--project-dir") + 1] == os.path.realpath(tmp_path)
    # The human's browser tab owns the lifecycle: no owner-PID anchor, no
    # idle cap by default, and its own process group so the bridge's killpg
    # at sandbox teardown cannot reach it.
    assert argv[argv.index("--owner-pid") + 1] == "none"
    assert argv[argv.index("--idle-timeout-minutes") + 1] == "0"
    assert run.call_args.kwargs["start_new_session"] is True
    assert "--open" in argv
    assert env["BRAINSTORM_PORT"] == "52999"


def test_brainstorm_start_idle_timeout_passthrough(tmp_path):
    """An explicit idle_timeout_minutes replaces the disabled default."""
    from tkt._sessionmanagers import BrainstormManager

    mgr = BrainstormManager(root=str(tmp_path))
    completed = sp.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps({"type": "server-started", "state_dir": str(tmp_path)}) + "\n",
        stderr="",
    )
    with (
        mock.patch("tkt._sessionmanagers.subprocess.run", return_value=completed) as run,
        mock.patch("tkt._sessionmanagers._find_brainstorm_script", return_value="/fake/start-server.sh"),
    ):
        mgr.start(".", idle_timeout_minutes=30)
    argv = run.call_args[0][0]
    assert argv[argv.index("--idle-timeout-minutes") + 1] == "30"


def test_brainstorm_start_without_port_clears_env_and_no_open(tmp_path):
    """No port means no BRAINSTORM_PORT; open_browser=False drops --open."""
    from tkt._sessionmanagers import BrainstormManager

    mgr = BrainstormManager(root=str(tmp_path))
    completed = sp.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps({"type": "server-started", "state_dir": str(tmp_path)}) + "\n",
        stderr="",
    )
    with (
        mock.patch("tkt._sessionmanagers.subprocess.run", return_value=completed) as run,
        mock.patch("tkt._sessionmanagers._find_brainstorm_script", return_value="/fake/start-server.sh"),
        mock.patch.dict(os.environ, {"BRAINSTORM_PORT": "1234"}),
    ):
        mgr.start(".", open_browser=False)
    argv = run.call_args[0][0]
    env = run.call_args.kwargs["env"]
    assert "--open" not in argv
    assert "BRAINSTORM_PORT" not in env


def test_brainstorm_start_error_passthrough(tmp_path):
    """A launcher failure with no JSON raises RuntimeError with detail."""
    from tkt._sessionmanagers import BrainstormManager

    mgr = BrainstormManager(root=str(tmp_path))
    completed = sp.CompletedProcess(
        args=[], returncode=1, stdout="", stderr='{"error": "Server failed to start"}\n'
    )
    with (
        mock.patch("tkt._sessionmanagers.subprocess.run", return_value=completed),
        mock.patch("tkt._sessionmanagers._find_brainstorm_script", return_value="/fake/start-server.sh"),
    ):
        try:
            mgr.start(".")
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            assert "Server failed to start" in str(exc)


def test_brainstorm_start_returns_launcher_error_dict(tmp_path):
    """A launcher error JSON dict is returned to the caller."""
    from tkt._sessionmanagers import BrainstormManager

    mgr = BrainstormManager(root=str(tmp_path))
    completed = sp.CompletedProcess(
        args=[], returncode=1, stdout='{"error": "Server started but was killed. Retry"}\n', stderr=""
    )
    with (
        mock.patch("tkt._sessionmanagers.subprocess.run", return_value=completed),
        mock.patch("tkt._sessionmanagers._find_brainstorm_script", return_value="/fake/start-server.sh"),
    ):
        info = mgr.start(".")
    assert "error" in info


def test_brainstorm_status_picks_newest_session(tmp_path):
    """Status uses the most recently started session dir."""
    from tkt._sessionmanagers import BrainstormManager

    old = _brainstorm_session(
        tmp_path,
        "1-1",
        pid=999999,
        info={"type": "server-started", "port": 1111, "state_dir": "x"},
    )
    fresh = _brainstorm_session(
        tmp_path,
        "2-2",
        pid=os.getpid(),
        info={"type": "server-started", "port": 2222, "state_dir": "x"},
    )
    import time as _time

    _time.sleep(0.01)
    os.utime(old / "server-info", (0, 0))
    os.utime(fresh / "server-info")
    mgr = BrainstormManager(root=str(tmp_path))
    info = mgr.status(".")
    assert info["type"] == "server-running"
    assert info["port"] == 2222


def test_brainstorm_status_stopped_when_server_stopped_marker(tmp_path):
    """The server-stopped marker wins over a live pid."""
    from tkt._sessionmanagers import BrainstormManager

    _brainstorm_session(tmp_path, "1-1", pid=os.getpid(), stopped=True)
    mgr = BrainstormManager(root=str(tmp_path))
    assert mgr.status(".")["type"] == "server-stopped"


def test_brainstorm_status_stopped_when_pid_dead(tmp_path):
    """A dead pid reports as stopped, not running."""
    from tkt._sessionmanagers import BrainstormManager

    # PID 0x7FFFFFFF is beyond any valid pid on Linux; kill(pid, 0) fails.
    _brainstorm_session(tmp_path, "1-1", pid=0x7FFFFFFF)
    mgr = BrainstormManager(root=str(tmp_path))
    assert mgr.status(".")["type"] == "server-stopped"


def test_brainstorm_status_not_running(tmp_path):
    """No session files at all reports not-running."""
    from tkt._sessionmanagers import BrainstormManager

    mgr = BrainstormManager(root=str(tmp_path))
    assert mgr.status(".")["type"] == "not-running"


def test_brainstorm_stop_terminates_live_server(tmp_path):
    """Stop SIGTERMs the live server and reports stopped."""
    import signal as signal_mod

    from tkt._sessionmanagers import BrainstormManager

    state_dir = _brainstorm_session(tmp_path, "1-1", pid=os.getpid())
    mgr = BrainstormManager(root=str(tmp_path))

    calls = []
    polls = {"n": 0}

    def fake_kill(pid, sig):
        calls.append((pid, sig))
        if sig == signal_mod.SIGTERM:
            # A real server writes the marker on graceful shutdown.
            (state_dir / "server-stopped").write_text("{}\n")
        elif sig == 0:
            polls["n"] += 1
            if polls["n"] > 1:
                # First poll is the liveness pre-check; later ones are the
                # post-SIGTERM wait: the process is gone.
                raise OSError

    with mock.patch("tkt._sessionmanagers.os.kill", side_effect=fake_kill):
        info = mgr.stop(".")
    assert (os.getpid(), signal_mod.SIGTERM) in calls  # after the liveness poll
    assert info["type"] == "server-stopped"
    assert info["port"] == 52341
    # The dead session reports stopped, not running.
    assert mgr.status(".")["type"] == "server-stopped"


def test_brainstorm_stop_noop_when_not_running(tmp_path):
    """Stop on a project without a server is a no-op."""
    from tkt._sessionmanagers import BrainstormManager

    mgr = BrainstormManager(root=str(tmp_path))
    assert mgr.stop(".")["type"] == "not-running"


def test_find_brainstorm_script_prefers_bundled_submodule(monkeypatch):
    """Default discovery finds the pinned submodule launcher."""
    from tkt._sessionmanagers import _find_brainstorm_script

    monkeypatch.delenv("TKT_BRAINSTORM_SCRIPT", raising=False)
    script = _find_brainstorm_script()
    assert script.endswith(
        os.path.join("superpowers", "skills", "brainstorming", "scripts", "start-server.sh")
    )
    assert os.path.isfile(script)


def test_find_brainstorm_script_env_override(tmp_path, monkeypatch):
    """TKT_BRAINSTORM_SCRIPT overrides discovery."""
    from tkt._sessionmanagers import _find_brainstorm_script

    fake = tmp_path / "start-server.sh"
    fake.write_text("#!/bin/bash\nexit 0\n")
    monkeypatch.setenv("TKT_BRAINSTORM_SCRIPT", str(fake))
    assert _find_brainstorm_script() == os.path.realpath(fake)


def test_format_brainstorm_info_warns_about_key():
    """Rendered output warns not to strip the ?key= from the URL."""
    from tkt._sessionmanagers import _format_brainstorm_info

    out = _format_brainstorm_info({"type": "server-started", "url": "http://x/?key=abc"})
    assert "```json" in out
    assert "?key=" in out  # explicit warning not to strip the session key


def test_brainstorm_status_reports_reap_reason(tmp_path):
    """After a tab-close reap, a fresh manager finds the marker-only session
    and reports the reason the server recorded for its own shutdown.
    """
    from tkt._sessionmanagers import BrainstormManager

    state = _brainstorm_session(
        tmp_path, "1-1", pid=os.getpid(), stopped={"reason": "browser-closed", "timestamp": 1700000000}
    )
    (state / "server-info").unlink()  # shutdown() unlinks it; the marker is all that's left
    mgr = BrainstormManager(root=str(tmp_path))
    info = mgr.status(".")
    assert info["type"] == "server-stopped"
    assert info["reason"] == "browser-closed"
    assert info["timestamp"] == 1700000000


def test_brainstorm_status_not_running_without_markers(tmp_path):
    """A leftover session dir with neither lifecycle marker stays invisible."""
    from tkt._sessionmanagers import BrainstormManager

    (tmp_path / ".superpowers" / "brainstorm" / "1-1" / "state").mkdir(parents=True)
    mgr = BrainstormManager(root=str(tmp_path))
    assert mgr.status(".")["type"] == "not-running"


def test_format_brainstorm_info_reports_stop_reason():
    """A stopped render names the reap reason instead of the 'not ... yet'
    wording, which would invite a resurrection of a session the human closed.
    """
    from tkt._sessionmanagers import _format_brainstorm_info

    out = _format_brainstorm_info({"type": "server-stopped", "reason": "browser-closed"})
    assert "browser-closed" in out
    assert "No companion server session" not in out


# ========== PlannotatorManager (host-side review sessions) ==========


FAKE_CLI = """#!/usr/bin/env bash
# Fake plannotator for tests. State lives in $FAKE_STATE.
cmd="$1"; shift
case "$cmd" in
  sessions)
    if [ -f "$FAKE_STATE/sessions.json" ]; then cat "$FAKE_STATE/sessions.json"; else echo "[]"; fi
    ;;
  annotate|review)
    # Optional: the CLI rejects the target and exits before any session exists.
    if [ -n "$FAKE_EXIT_EARLY" ]; then printf '%s\n' "$FAKE_EXIT_EARLY" >&2; exit 1; fi
    # Optional: withhold registration past the first discovery poll.
    if [ -n "$FAKE_REGISTER_DELAY_S" ]; then sleep "$FAKE_REGISTER_DELAY_S"; fi
    printf '%s\\n' "$cmd" "$@" > "$FAKE_STATE/argv.txt"
    target="$1"
    port=$((41000 + ($$ % 1000)))
    # Self-register the way the real CLI does. $$ is the CLI's process-group
    # leader; the server Plannotator spawns shares the session entry.
    printf '[{"target":"%s","url":"http://localhost:%s/","port":%s,"pid":%s,"startedAt":"now"}]' \\
      "$target" "$port" "$port" "$$" > "$FAKE_STATE/sessions.json"
    echo "  Plannotator session ready: http://localhost:$port/" >&2
    if [ -n "$FAKE_DECISION" ]; then
      printf '%s' "$FAKE_DECISION"        # the human resolved it: decision JSON on stdout
    else
      sleep "${FAKE_SLEEP:-60}" &         # stand-in for a session still open
      echo $! > "$FAKE_STATE/sleep.pid"
      trap 'kill -TERM 0; exit 143' TERM INT
      wait
    fi
    ;;
  *) echo "unknown: $cmd" >&2; exit 2 ;;
esac
"""


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    """Fake `plannotator` on PATH, with HOME (and therefore
    ``plannotator_result_dir()``) relocated to tmp_path.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cli = bin_dir / "plannotator"
    cli.write_text(FAKE_CLI)
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("HOME", str(tmp_path))  # plannotator_result_dir lands here
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("FAKE_STATE", str(state))
    return tmp_path


def _sessions_json(tmp_path, entries):
    (tmp_path / "state" / "sessions.json").write_text(json.dumps(entries))


def test_open_discovers_existing_session(fake_cli):
    """open() attaches to an already-listed session and registers it."""
    _sessions_json(fake_cli, [{"target": str(fake_cli / "spec.md"), "url": "http://localhost:41234/"}])
    out = PlannotatorManager().open(str(fake_cli), str(fake_cli / "spec.md"))
    assert out["reused"] is True
    assert out["source"] == "discovered"
    assert out["url"].startswith("http://localhost:")
    assert out["result_path"] is None
    entry = json.loads((Path(plannotator_result_dir()) / f"{out['handle']}.registry.json").read_text())
    assert entry["pgid"] == 0 and entry["workspace"] == str(fake_cli)


def test_open_spawns_when_absent_and_registers(fake_cli):
    """open() spawns the CLI and records a registry entry when none is live."""
    (fake_cli / "spec.md").write_text("# plan")
    # No FAKE_DECISION: the fake registers itself and then blocks, like a
    # real session the human has not resolved yet.
    out = PlannotatorManager().open(str(fake_cli), str(fake_cli / "spec.md"))
    assert out["handle"].startswith("pna-spec-md-")
    assert out["source"] == "spawned" and out["reused"] is False
    entry = json.loads((Path(plannotator_result_dir()) / f"{out['handle']}.registry.json").read_text())
    assert entry["workspace"] == str(fake_cli)
    assert (Path(plannotator_result_dir()) / f"{out['handle']}.out").exists()


def _plan_target_outside(fake_cli):
    """Create a plan file under a sibling directory outside the workspace."""
    plan_root = fake_cli.parent / (fake_cli.name + "-plan")
    plan_root.mkdir()
    plan = plan_root / "plan.md"
    plan.write_text("# plan")
    return plan_root, plan


def test_open_extra_roots_allows_target_outside_workspace(fake_cli):
    """extra_roots (e.g. the host plan dir) accepts outside targets."""
    plan_root, plan = _plan_target_outside(fake_cli)
    out = PlannotatorManager().open(str(fake_cli), str(plan), extra_roots=[str(plan_root)])
    assert out["handle"].startswith("pna-plan-md-")


def test_open_target_outside_all_roots_raises(fake_cli):
    """Without the extra root, the same plan-dir target is still refused."""
    _, plan = _plan_target_outside(fake_cli)
    with pytest.raises(ValueError, match="outside workspace"):
        PlannotatorManager().open(str(fake_cli), str(plan))


def test_open_missing_binary_raises(fake_cli, monkeypatch):
    """A missing plannotator binary raises RuntimeError naming the CLI."""
    monkeypatch.setenv("PATH", "/nonexistent-bin-that-does-not-exist")
    (fake_cli / "spec.md").write_text("# p")
    with pytest.raises(RuntimeError, match="plannotator"):
        PlannotatorManager().open(str(fake_cli), str(fake_cli / "spec.md"))


def test_open_captures_stdout_as_result(fake_cli, monkeypatch):
    """The decision JSON is the CLI's stdout, already in place at open."""
    monkeypatch.setenv("FAKE_DECISION", '{"decision":"approved","annotationCount":0,"target":"/x"}')
    # Withhold registration past the first poll so discovery takes the
    # listing path (a registration that lands mid-poll lets the stderr
    # ready line win and records no server pid).
    monkeypatch.setenv("FAKE_REGISTER_DELAY_S", "0.35")
    ws = str(fake_cli)
    (fake_cli / "plan.md").write_text("# plan\n")
    out = PlannotatorManager().open(ws, str(fake_cli / "plan.md"))
    assert out["source"] == "spawned" and out["reused"] is False
    assert Path(out["result_path"]).read_text() == '{"decision":"approved","annotationCount":0,"target":"/x"}'
    assert out["result_path"] == str(Path(plannotator_result_dir()) / f"{out['handle']}.out")
    entry = json.loads((Path(plannotator_result_dir()) / f"{out['handle']}.registry.json").read_text())
    assert entry["pgid"] > 0 and entry["server_pid"] > 0 and entry["url"].startswith("http://localhost:")
    assert entry["listed_target"] == str((fake_cli / "plan.md").resolve())


def test_open_always_requests_json(fake_cli, monkeypatch):
    """Every spawned session asks the CLI for JSON output."""
    monkeypatch.setenv("FAKE_DECISION", "{}")
    ws = str(fake_cli)
    (fake_cli / "plan.md").write_text("# plan\n")
    PlannotatorManager().open(ws, str(fake_cli / "plan.md"), gate=True)
    argv = (fake_cli / "state" / "argv.txt").read_text().splitlines()
    assert argv[0] == "annotate" and "--json" in argv and "--gate" in argv


def test_open_review_passes_base(fake_cli, monkeypatch):
    """A review session forwards its compare ref to the CLI."""
    monkeypatch.setenv("FAKE_DECISION", "{}")
    ws = str(fake_cli)
    (fake_cli / "pkg").mkdir()
    PlannotatorManager().open(ws, str(fake_cli / "pkg"), kind="review", base="main")
    argv = (fake_cli / "state" / "argv.txt").read_text().splitlines()
    assert argv[0] == "review" and "--json" in argv and argv[argv.index("--base") + 1] == "main"


def test_open_reuses_live_session_and_its_capture(fake_cli, monkeypatch):
    """A restart re-attaches to the live session and its existing capture."""
    monkeypatch.setenv("FAKE_DECISION", '{"decision":"approved"}')
    # Registration past the first poll keeps the entry's server_pid recorded
    # (the stderr fallback would leave it None and the re-open unmatched).
    monkeypatch.setenv("FAKE_REGISTER_DELAY_S", "0.35")
    ws = str(fake_cli)
    (fake_cli / "plan.md").write_text("# plan\n")
    first = PlannotatorManager().open(ws, str(fake_cli / "plan.md"))
    second = PlannotatorManager().open(ws, str(fake_cli / "plan.md"))
    assert second["reused"] is True and second["source"] == "spawned"
    assert second["handle"] == first["handle"]
    assert second["result_path"] == first["result_path"]


def test_open_registers_human_started_session(fake_cli):
    """A session the human started is reattachable and reapeable,
    uncaptured.
    """
    ws = str(fake_cli)
    (fake_cli / "plan.md").write_text("# plan\n")
    target = str((fake_cli / "plan.md").resolve())
    server = subprocess.Popen(["sleep", "30"])
    try:
        _sessions_json(
            fake_cli,
            [{"target": target, "url": "http://localhost:4444/", "port": 4444, "pid": server.pid}],
        )
        out = PlannotatorManager().open(ws, target)
        assert out["result_path"] is None and out["source"] == "discovered"
        assert out["url"] == "http://localhost:4444/"
        entry = json.loads((Path(plannotator_result_dir()) / f"{out['handle']}.registry.json").read_text())
        assert entry["pgid"] == 0 and entry["server_pid"] == server.pid
    finally:
        server.kill()
        server.wait()


def test_open_reopen_human_started_stays_discovered(fake_cli):
    """Re-opening a human-started session keeps its discovered identity:
    no capture appears, and no second registry entry is minted.
    """
    ws = str(fake_cli)
    (fake_cli / "plan.md").write_text("# plan\n")
    target = str((fake_cli / "plan.md").resolve())
    server = subprocess.Popen(["sleep", "30"])
    try:
        _sessions_json(
            fake_cli,
            [{"target": target, "url": "http://localhost:4444/", "port": 4444, "pid": server.pid}],
        )
        first = PlannotatorManager().open(ws, target)
        second = PlannotatorManager().open(ws, target)
        assert second["source"] == "discovered" and second["result_path"] is None
        assert second["handle"] == first["handle"]
        assert len(list(Path(plannotator_result_dir()).glob("*.registry.json"))) == 1
    finally:
        server.kill()
        server.wait()


def test_open_reuses_entry_without_server_pid_via_live_group(fake_cli, monkeypatch):
    """A re-open reuses the entry whose URL came from the stderr line (and
    so has no server pid): its capture is there to read, and a second entry
    claiming "discovered" would hide it.
    """
    from tkt import _sessionmanagers

    ws = str(fake_cli)
    (fake_cli / "plan.md").write_text("# plan\n")
    # Blind the listing while the session spawns so discovery takes the
    # stderr ready line and records no server pid; the fake stays open
    # (no FAKE_DECISION), owning a live process group.
    real = _sessionmanagers._sessions_listing
    blind = {"yes": True}
    monkeypatch.setattr(_sessionmanagers, "_sessions_listing", lambda: None if blind["yes"] else real())
    first = PlannotatorManager().open(ws, str(fake_cli / "plan.md"))
    blind["yes"] = False
    entry = json.loads((Path(plannotator_result_dir()) / f"{first['handle']}.registry.json").read_text())
    assert entry["server_pid"] is None
    second = PlannotatorManager().open(ws, str(fake_cli / "plan.md"))
    assert second["reused"] is True and second["source"] == "spawned"
    assert second["result_path"] == first["result_path"]
    assert len(list(Path(plannotator_result_dir()).glob("*.registry.json"))) == 1


def test_open_does_not_reuse_entry_with_dead_group(fake_cli):
    """A same-target entry whose CLI group is dead belongs to a finished
    session: the re-open registers fresh rather than handing back its
    capture path.
    """
    from tkt._sessionmanagers import _write_entry

    ws = str(fake_cli)
    (fake_cli / "plan.md").write_text("# plan\n")
    target = str((fake_cli / "plan.md").resolve())
    server = subprocess.Popen(["sleep", "30"])
    try:
        _sessions_json(
            fake_cli,
            [{"target": target, "url": "http://localhost:4444/", "port": 4444, "pid": server.pid}],
        )
        gone = subprocess.Popen(["true"])
        gone.wait()
        stale_out = str(Path(plannotator_result_dir()) / "pna-plan-md-stale.out")
        _write_entry(
            "pna-plan-md-stale",
            {
                "handle": "pna-plan-md-stale",
                "workspace": ws,
                "target": target,
                "source": "spawned",
                "pgid": gone.pid,
                "out_path": stale_out,
            },
        )
        out = PlannotatorManager().open(ws, target)
        assert out["source"] == "discovered" and out["result_path"] is None
        assert out["handle"] != "pna-plan-md-stale"
        assert len(list(Path(plannotator_result_dir()).glob("*.registry.json"))) == 2
    finally:
        server.kill()
        server.wait()


def test_open_spawn_failure_raises_with_stderr_tail(fake_cli, monkeypatch):
    """A CLI that exits before any session exists fails the open, and the
    reason rides along on its stderr; a handle waiting on a human looks
    nothing like this.
    """
    monkeypatch.setenv(
        "FAKE_EXIT_EARLY",
        "File type not supported: .py\n\nFor code review, use: plannotator review [DIRECTORY]",
    )
    (fake_cli / "code.py").write_text("x = 1\n")
    with pytest.raises(RuntimeError, match="exited without opening a session") as exc:
        PlannotatorManager().open(str(fake_cli), str(fake_cli / "code.py"))
    assert "For code review, use: plannotator review" in str(exc.value)
    reg = Path(plannotator_result_dir())
    assert list(reg.glob("*.registry.json")) == []
    assert list(reg.glob("*.out")) == []
    assert list(reg.glob("*.err")) == []


def test_match_prefers_exact_target_and_reviews_match_by_ancestor(fake_cli):
    """The listing rule shared by every consumer: exact match wins; only a
    review session's target may be an ancestor of the requested one.
    """
    root = str(fake_cli.resolve())
    sub = str((fake_cli / "tkt").resolve())
    listing = [
        {"mode": "review", "target": root, "url": "http://localhost:1/"},
        {"mode": "review", "target": sub, "url": "http://localhost:2/"},
    ]
    assert _match(listing, sub)["url"] == "http://localhost:2/"  # exact wins
    assert _match(listing, str((fake_cli / "tests").resolve()))["url"] == "http://localhost:1/"
    annotate_root = [{"mode": "annotate", "target": root, "url": "http://localhost:3/"}]
    assert _match(annotate_root, sub) is None  # annotate targets stay exact
    assert _match(annotate_root, root)["url"] == "http://localhost:3/"


def test_open_attaches_subdirectory_review_to_root_session(fake_cli):
    """`review` registers at the git root, so a subdirectory request must
    re-attach to the root-registered session instead of spawning another.
    """
    ws = str(fake_cli)
    (fake_cli / "tkt").mkdir()
    sub = str((fake_cli / "tkt").resolve())
    server = subprocess.Popen(["sleep", "30"])
    try:
        _sessions_json(
            fake_cli,
            [
                {
                    "mode": "review",
                    "target": str(fake_cli.resolve()),
                    "url": "http://localhost:4444/",
                    "port": 4444,
                    "pid": server.pid,
                }
            ],
        )
        first = PlannotatorManager().open(ws, sub, kind="review")
        second = PlannotatorManager().open(ws, sub, kind="review")
        assert first["reused"] is True and first["source"] == "discovered"
        assert first["result_path"] is None  # discovered sessions have no capture
        assert first["url"] == "http://localhost:4444/"  # the root-registered session
        assert second["reused"] is True and second["handle"] == first["handle"]
        assert second["result_path"] == first["result_path"]
        assert not (fake_cli / "state" / "argv.txt").exists()  # attached, never spawned
        entry = json.loads((Path(plannotator_result_dir()) / f"{first['handle']}.registry.json").read_text())
        assert entry["target"] == sub  # what was asked for
        assert entry["listed_target"] == str(fake_cli.resolve())  # what was registered
    finally:
        server.kill()
        server.wait()


def test_open_annotate_at_root_does_not_swallow_subdirectory(fake_cli):
    """An annotate session registered at the root is not the session for a
    subdirectory request; the request spawns its own.
    """
    ws = str(fake_cli)
    (fake_cli / "tkt").mkdir()
    sub = str((fake_cli / "tkt").resolve())
    server = subprocess.Popen(["sleep", "30"])
    try:
        _sessions_json(
            fake_cli,
            [
                {
                    "mode": "annotate",
                    "target": str(fake_cli.resolve()),
                    "url": "http://localhost:4444/",
                    "port": 4444,
                    "pid": server.pid,
                }
            ],
        )
        out = PlannotatorManager().open(ws, sub)
        assert out["source"] == "spawned" and out["reused"] is False
    finally:
        server.kill()
        server.wait()


def test_sweep_and_attach_share_one_listing_rule(fake_cli):
    """The sweep keeps exactly the entries the attach path would call live:
    a subdirectory entry under a root-registered review survives, and a
    target nowhere in the listing does not.
    """
    from tkt._sessionmanagers import _write_entry

    root = str(fake_cli.resolve())
    _sessions_json(
        fake_cli,
        [{"mode": "review", "target": root, "url": "http://localhost:1/", "port": 1, "pid": 999999}],
    )
    _write_entry(
        "pna-sub-1",
        {"handle": "pna-sub-1", "workspace": root, "target": str(fake_cli / "tkt"), "pgid": 0},
    )
    _write_entry(
        "pna-gone-1",
        {"handle": "pna-gone-1", "workspace": "/elsewhere", "target": "/elsewhere/x.md", "pgid": 0},
    )
    assert _match(_sessions_listing(), str(fake_cli / "tkt")) is not None  # attach: live
    assert sweep_session_registry() == 1  # and so the sweep must agree
    assert (Path(plannotator_result_dir()) / "pna-sub-1.registry.json").exists()
    assert not (Path(plannotator_result_dir()) / "pna-gone-1.registry.json").exists()


def test_open_reports_session_that_ended_without_decision(fake_cli):
    """A spawn entry whose CLI group is dead with an empty capture is not
    reusable even when the listing still shows a live registration: no
    decision can arrive through it.
    """
    from tkt._sessionmanagers import _registry_entries, _write_entry

    ws = str(fake_cli)
    (fake_cli / "plan.md").write_text("# plan\n")
    target = str((fake_cli / "plan.md").resolve())
    server = subprocess.Popen(["sleep", "30"])
    try:
        _sessions_json(
            fake_cli,
            [{"target": target, "url": "http://localhost:4444/", "port": 4444, "pid": server.pid}],
        )
        gone = subprocess.Popen(["true"])
        gone.wait()
        out = Path(plannotator_result_dir()) / "pna-plan-md-dead.out"
        out.write_text("")
        _write_entry(
            "pna-plan-md-dead",
            {
                "handle": "pna-plan-md-dead",
                "workspace": ws,
                "target": target,
                "source": "spawned",
                "pgid": gone.pid,
                "server_pid": server.pid,
                "out_path": str(out),
            },
        )
        result = PlannotatorManager().open(ws, target)
        assert result["ended_without_decision"] is True
        assert result["reused"] is not True
        assert result["handle"] == "pna-plan-md-dead"
        assert result["source"] == "spawned"
        # The entry stays so workspace cleanup can still reap the server.
        assert "pna-plan-md-dead" in _registry_entries()
    finally:
        server.kill()
        server.wait()


def test_open_sequence_after_tab_close_reopens_fresh_then_attaches(fake_cli, monkeypatch):
    """Killing a session's CLI while its server stays listed makes the next
    open spawn a fresh session (the orphan's entry surviving for the
    reaper), and the open after that attaches to the fresh one.
    """
    import signal as signal_mod

    from tkt._sessionmanagers import _registry_entries

    # Registration past the first poll records each CLI's server pid.
    monkeypatch.setenv("FAKE_REGISTER_DELAY_S", "0.35")
    ws = str(fake_cli)
    (fake_cli / "plan.md").write_text("# plan\n")
    first = PlannotatorManager().open(ws, str(fake_cli / "plan.md"))  # stays open
    os.killpg(_registry_entries()[first["handle"]]["pgid"], signal_mod.SIGKILL)
    second = PlannotatorManager().open(ws, str(fake_cli / "plan.md"))
    assert second["reused"] is False and second["source"] == "spawned"
    assert second["handle"] != first["handle"]
    assert second["previous_ended_without_decision"] is True
    entry2 = _registry_entries()[second["handle"]]
    assert entry2["server_pid"] == entry2["pgid"]  # discovery adopted its own registration
    third = PlannotatorManager().open(ws, str(fake_cli / "plan.md"))
    assert third["reused"] is True and third["handle"] == second["handle"]
    assert set(_registry_entries()) == {first["handle"], second["handle"]}  # orphan kept


def test_open_attaches_the_live_registration_when_a_target_is_listed_twice(fake_cli):
    """Plannotator does not dedup: with a dead-pid orphan and a live server
    both listed for one target, open() attaches the live one and spawns
    nothing — not another orphan-chasing session.
    """
    from tkt._sessionmanagers import _registry_entries

    ws = str(fake_cli)
    (fake_cli / "plan.md").write_text("# plan\n")
    target = str((fake_cli / "plan.md").resolve())
    orphan = subprocess.Popen(["true"])
    orphan.wait()
    server = subprocess.Popen(["sleep", "30"])
    try:
        _sessions_json(
            fake_cli,
            [
                {"target": target, "url": "http://localhost:1111/", "port": 1111, "pid": orphan.pid},
                {"target": target, "url": "http://localhost:2222/", "port": 2222, "pid": server.pid},
            ],
        )
        first = PlannotatorManager().open(ws, target)
        second = PlannotatorManager().open(ws, target)
        assert first["reused"] is True and first["url"] == "http://localhost:2222/"
        assert second["reused"] is True and second["handle"] == first["handle"]
        assert list(_registry_entries()) == [first["handle"]]
        assert not (fake_cli / "state" / "argv.txt").exists()  # nothing was spawned
    finally:
        server.kill()
        server.wait()


def test_open_spawns_fresh_when_only_an_orphan_is_listed(fake_cli):
    """A listed session whose registering CLI is dead cannot produce a
    decision: open() spawns a fresh session and says the earlier attempt
    ended, instead of attaching.
    """
    ws = str(fake_cli)
    (fake_cli / "plan.md").write_text("# plan\n")
    target = str((fake_cli / "plan.md").resolve())
    orphan = subprocess.Popen(["true"])
    orphan.wait()
    _sessions_json(
        fake_cli,
        [{"target": target, "url": "http://localhost:1111/", "port": 1111, "pid": orphan.pid}],
    )
    out = PlannotatorManager().open(ws, target)
    assert out["reused"] is False and out["source"] == "spawned"
    assert out["previous_ended_without_decision"] is True


def test_open_hands_back_a_captured_decision_through_its_own_registration(fake_cli):
    """A resolved session whose listing registration outlived its CLI is
    reused with its decision flagged — not re-spawned over.
    """
    from tkt._sessionmanagers import _write_entry

    ws = str(fake_cli)
    (fake_cli / "plan.md").write_text("# plan\n")
    target = str((fake_cli / "plan.md").resolve())
    gone = subprocess.Popen(["true"])
    gone.wait()
    decision = Path(plannotator_result_dir()) / "pna-plan-md-done.out"
    decision.write_text('{"decision":"approved","annotationCount":0,"target":"/x"}')
    _write_entry(
        "pna-plan-md-done",
        {
            "handle": "pna-plan-md-done",
            "workspace": ws,
            "target": target,
            "source": "spawned",
            "pgid": gone.pid,
            "server_pid": gone.pid,
            "out_path": str(decision),
        },
    )
    _sessions_json(
        fake_cli,
        [{"target": target, "url": "http://localhost:4444/", "port": 4444, "pid": gone.pid}],
    )
    result = PlannotatorManager().open(ws, target)
    assert result["reused"] is True
    assert result["decision_captured"] is True
    assert result["result_path"] == str(decision)
    assert not (fake_cli / "state" / "argv.txt").exists()  # never spawned


def test_open_ignores_another_entrys_decision_when_the_listed_one_is_ended(fake_cli):
    """Entry A holds an earlier session's decision and B is the listed
    orphan with an empty capture: the listing resolves to B, so the target
    is open again — a fresh spawn, with both entries kept.
    """
    from tkt._sessionmanagers import _registry_entries, _write_entry

    ws = str(fake_cli)
    (fake_cli / "plan.md").write_text("# plan\n")
    target = str((fake_cli / "plan.md").resolve())
    gone_a = subprocess.Popen(["true"])
    gone_a.wait()
    gone_b = subprocess.Popen(["true"])
    gone_b.wait()
    decided = Path(plannotator_result_dir()) / "pna-plan-md-a.out"
    decided.write_text('{"decision":"approved"}')
    empty = Path(plannotator_result_dir()) / "pna-plan-md-b.out"
    empty.write_text("")
    _write_entry(
        "pna-plan-md-a",
        {
            "handle": "pna-plan-md-a",
            "workspace": ws,
            "target": target,
            "source": "spawned",
            "pgid": gone_a.pid,
            "server_pid": gone_a.pid,
            "out_path": str(decided),
        },
    )
    _write_entry(
        "pna-plan-md-b",
        {
            "handle": "pna-plan-md-b",
            "workspace": ws,
            "target": target,
            "source": "spawned",
            "pgid": gone_b.pid,
            "server_pid": gone_b.pid,
            "out_path": str(empty),
        },
    )
    _sessions_json(
        fake_cli,
        [{"target": target, "url": "http://localhost:1111/", "port": 1111, "pid": gone_b.pid}],
    )
    result = PlannotatorManager().open(ws, target)
    assert result["reused"] is False and result["source"] == "spawned"
    assert result["handle"] not in ("pna-plan-md-a", "pna-plan-md-b")
    assert result["previous_ended_without_decision"] is True
    assert {"pna-plan-md-a", "pna-plan-md-b"} <= set(_registry_entries())


def test_open_without_listing_still_captures(fake_cli, monkeypatch):
    """A broken `sessions` listing costs the server pid; the URL still
    arrives from the stderr ready line.
    """
    monkeypatch.setenv("FAKE_DECISION", '{"decision":"approved"}')
    monkeypatch.setattr("tkt._sessionmanagers._sessions_listing", lambda: None)
    ws = str(fake_cli)
    (fake_cli / "plan.md").write_text("# plan\n")
    out = PlannotatorManager().open(ws, str(fake_cli / "plan.md"))
    assert out["url"] is not None and out["url"].startswith("http://localhost:")
    assert Path(out["result_path"]).read_text() == '{"decision":"approved"}'
    assert "Plannotator session ready:" in Path(out["err_path"]).read_text()


def test_discover_without_url_sources_gives_up(fake_cli, monkeypatch):
    """No listing match and no stderr ready line, with the CLI still
    starting up, means no URL, ever.
    """
    monkeypatch.setattr("tkt._sessionmanagers._sessions_listing", lambda: None)
    monkeypatch.setattr("tkt._sessionmanagers._DISCOVER_BUDGET_S", 0.1)
    err = Path(fake_cli) / "silent.err"
    err.write_text("")
    proc = subprocess.Popen(["sleep", "30"])
    try:
        assert PlannotatorManager()._discover(str(fake_cli / "plan.md"), str(err), proc) == (
            None,
            None,
            None,
        )
    finally:
        proc.kill()
        proc.wait()


def test_discover_raises_when_cli_exits_unseen(fake_cli, monkeypatch):
    """A CLI that exits without registering or printing the ready line is a
    failed spawn, and the stderr tail names the reason.
    """
    monkeypatch.setattr("tkt._sessionmanagers._sessions_listing", lambda: None)
    err = Path(fake_cli) / "reject.err"
    err.write_text("File type not supported: .py\nFor code review, use: plannotator review [DIRECTORY]\n")
    proc = subprocess.Popen(["true"])
    with pytest.raises(RuntimeError, match="For code review, use: plannotator review"):
        PlannotatorManager()._discover(str(fake_cli / "code.py"), str(err), proc)


@pytest.mark.parametrize(
    "name,keys",
    [
        ("annotate-approved.json", {"decision", "feedback", "annotationCount", "target"}),
        ("annotate-annotated.json", {"decision", "feedback", "annotationCount", "target"}),
        ("annotate-approved-empty.json", {"decision", "annotationCount", "target"}),
        ("annotate-dismissed.json", {"decision", "target"}),
        ("review-approved.json", {"decision", "message"}),
        ("review-annotated.json", {"decision", "message"}),
    ],
)
def test_decision_fixture_shapes(name, keys):
    """The pinned stdout shapes the guidance and tests describe."""
    path = Path(__file__).parent / "fixtures" / "plannotator" / name
    decision = json.loads(path.read_text())
    assert decision["decision"] in {"approved", "annotated", "dismissed"}
    assert set(decision) == keys


def test_closed_session_capture_is_empty(fake_cli):
    """Closing without submitting leaves a complete, empty capture."""
    empty = Path(__file__).parent / "fixtures" / "plannotator" / "annotate-closed-empty.json"
    assert empty.read_text() == ""


# ========== result-dir registry: handles, sweep, cleanup ==========


def test_claim_handle_is_unique_per_call(fake_cli):
    """Two opens of the same target in the same second get distinct handles."""
    from tkt._sessionmanagers import _claim_handle

    first = _claim_handle("/ws/plan.md")
    second = _claim_handle("/ws/plan.md")
    assert first != second
    assert first.startswith("pna-plan-md-")
    assert (Path(plannotator_result_dir()) / f"{first}.registry.json").exists()


def test_claim_handle_slug_survives_odd_names(fake_cli):
    """Handle slugs survive names with no word characters at all."""
    from tkt._sessionmanagers import _handle_slug

    assert _handle_slug("My Plan (v2)!!") == "my-plan-v2"
    assert _handle_slug("!!!") == "session"


def test_sweep_drops_dead_entry_keeps_captures(fake_cli):
    """A finished session loses its entry but keeps its decision file."""
    from tkt._sessionmanagers import _write_entry

    out = Path(plannotator_result_dir()) / "pna-dead-1.out"
    out.write_text('{"decision":"approved"}')
    _write_entry("pna-dead-1", {"handle": "pna-dead-1", "workspace": "/ws", "pgid": 0, "server_pid": 0})
    assert sweep_session_registry() == 1
    assert out.exists()
    assert not (Path(plannotator_result_dir()) / "pna-dead-1.registry.json").exists()


def test_sweep_keeps_entry_with_live_server(fake_cli):
    """An entry with a live session server survives the sweep."""
    from tkt._sessionmanagers import _write_entry

    live = subprocess.Popen(["sleep", "30"])
    try:
        _write_entry(
            "pna-live-1", {"handle": "pna-live-1", "workspace": "/ws", "pgid": 0, "server_pid": live.pid}
        )
        assert sweep_session_registry() == 0
        assert (Path(plannotator_result_dir()) / "pna-live-1.registry.json").exists()
    finally:
        live.kill()
        live.wait()


def test_sweep_prunes_old_captures_and_legacy_dir(fake_cli):
    """Captures past retention and the pre-capture registry dir both go."""
    from tkt._sessionmanagers import _legacy_registry_dir, _write_entry

    old = Path(plannotator_result_dir()) / "pna-old-1.out"
    old.write_text("{}")
    stamp = time.time() - 31 * 24 * 3600
    os.utime(old, (stamp, stamp))
    legacy = Path(_legacy_registry_dir())
    legacy.mkdir(parents=True)
    (legacy / "pn-stale.json").write_text("{}")
    _write_entry("pna-old-1", {"handle": "pna-old-1", "workspace": "/ws", "pgid": 0, "server_pid": 0})
    sweep_session_registry()
    assert not old.exists()
    assert not legacy.exists()


def test_cleanup_terminates_server_pid(fake_cli):
    """``tkt rm`` reaches the server: killing only the CLI leaves it
    serving.
    """
    from tkt._sessionmanagers import _write_entry, cleanup_workspace_sessions

    server = subprocess.Popen(["sleep", "30"])
    _sessions_json(
        fake_cli,
        [{"target": "/ws/plan.md", "url": "http://localhost:4321/", "port": 4321, "pid": server.pid}],
    )
    _write_entry(
        "pna-plan-md-1",
        {
            "handle": "pna-plan-md-1",
            "workspace": str(fake_cli),
            "target": "/ws/plan.md",
            "server_pid": server.pid,
        },
    )
    assert cleanup_workspace_sessions(str(fake_cli)) == 1
    assert server.wait(timeout=5) < 0  # terminated by a signal
    assert not (Path(plannotator_result_dir()) / "pna-plan-md-1.registry.json").exists()


def test_cleanup_skips_server_pid_the_cli_no_longer_lists(fake_cli):
    """Pid reuse must not make us signal an unrelated process."""
    from tkt._sessionmanagers import _write_entry, cleanup_workspace_sessions

    unrelated = subprocess.Popen(["sleep", "30"])
    try:
        _sessions_json(fake_cli, [])  # listing works, and our pid is not in it
        _write_entry(
            "pna-plan-md-2",
            {
                "handle": "pna-plan-md-2",
                "workspace": str(fake_cli),
                "target": "/ws/plan.md",
                "server_pid": unrelated.pid,
            },
        )
        assert cleanup_workspace_sessions(str(fake_cli)) == 1
        assert unrelated.poll() is None
    finally:
        unrelated.kill()
        unrelated.wait()


def test_cleanup_terminates_server_resolved_from_listing(fake_cli):
    """An entry with no recorded server pid is torn down through the listing.

    The stderr ready line can win the discovery poll before `sessions
    --json` reports the session, leaving no pid in the entry; the session
    server is then found by the entry's target.
    """
    from tkt._sessionmanagers import _write_entry, cleanup_workspace_sessions

    server = subprocess.Popen(["sleep", "30"])
    target = "/ws/plan.md"
    _sessions_json(
        fake_cli,
        [{"target": target, "url": "http://localhost:4321/", "port": 4321, "pid": server.pid}],
    )
    _write_entry(
        "pna-plan-md-3",
        {"handle": "pna-plan-md-3", "workspace": str(fake_cli), "target": target, "pgid": 0},
    )
    assert cleanup_workspace_sessions(str(fake_cli)) == 1
    assert server.wait(timeout=5) < 0  # terminated by a signal


def test_cleanup_drops_target_the_listing_lacks(fake_cli):
    """No listing entry for the target: the entry goes, nothing is
    signalled.
    """
    from tkt._sessionmanagers import _write_entry, cleanup_workspace_sessions

    unrelated = subprocess.Popen(["sleep", "30"])
    try:
        _sessions_json(fake_cli, [])
        _write_entry(
            "pna-plan-md-4",
            {
                "handle": "pna-plan-md-4",
                "workspace": str(fake_cli),
                "target": "/ws/plan.md",
                "pgid": 0,
            },
        )
        assert cleanup_workspace_sessions(str(fake_cli)) == 1
        assert unrelated.poll() is None
    finally:
        unrelated.kill()
        unrelated.wait()


def test_sweep_uses_listing_for_entries_without_server_pid(fake_cli):
    """An entry with no server pid lives while the listing shows its target."""
    from tkt._sessionmanagers import _write_entry

    server = subprocess.Popen(["sleep", "30"])
    try:
        target = "/ws/live.md"
        _sessions_json(
            fake_cli,
            [{"target": target, "url": "http://localhost:4321/", "port": 4321, "pid": server.pid}],
        )
        _write_entry(
            "pna-live-md-1",
            {"handle": "pna-live-md-1", "workspace": "/ws", "target": target, "pgid": 0},
        )
        _write_entry(
            "pna-gone-md-1",
            {"handle": "pna-gone-md-1", "workspace": "/ws", "target": "/ws/gone.md", "pgid": 0},
        )
        assert sweep_session_registry() == 1
        reg = Path(plannotator_result_dir())
        assert (reg / "pna-live-md-1.registry.json").exists()
        assert not (reg / "pna-gone-md-1.registry.json").exists()
    finally:
        server.kill()
        server.wait()


def test_sweep_keeps_entry_when_listing_fails(fake_cli, monkeypatch):
    """An unusable listing must not make an entry look dead."""
    from tkt._sessionmanagers import _write_entry

    monkeypatch.setattr("tkt._sessionmanagers._sessions_listing", lambda: None)
    _write_entry(
        "pna-unknown-1",
        {"handle": "pna-unknown-1", "workspace": "/ws", "target": "/ws/plan.md", "pgid": 0},
    )
    assert sweep_session_registry() == 0
    assert (Path(plannotator_result_dir()) / "pna-unknown-1.registry.json").exists()


def test_sweep_drops_dead_keeps_live(fake_cli, monkeypatch):
    """The sweep drops entries whose processes are gone and keeps live ones."""
    # Registration past the first poll records the server pid, so the
    # post-kill sweep judges liveness from pids, not the stale listing.
    monkeypatch.setenv("FAKE_REGISTER_DELAY_S", "0.35")
    (fake_cli / "spec.md").write_text("# p")
    out = PlannotatorManager().open(str(fake_cli), str(fake_cli / "spec.md"))
    assert sweep_session_registry() == 0  # live fake session still running
    reg = Path(plannotator_result_dir()) / f"{out['handle']}.registry.json"
    pid = json.loads(reg.read_text())["pgid"]
    subprocess.run(["kill", str(pid)], check=False)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and reg.exists():
        assert sweep_session_registry() >= 0
        time.sleep(0.1)
    assert not reg.exists()


def test_cleanup_workspace_kills_sessions(fake_cli):
    """Workspace cleanup terminates and unregisters that workspace's
    sessions.
    """
    (fake_cli / "spec.md").write_text("# p")
    PlannotatorManager().open(str(fake_cli), str(fake_cli / "spec.md"))
    assert cleanup_workspace_sessions(str(fake_cli)) == 1
    assert list(Path(plannotator_result_dir()).glob("*.registry.json")) == []


def test_cleanup_workspace_never_signals_own_group(fake_cli):
    """A discovered (pgid 0) entry must be unregistered, never killpg'd:
    killpg(0) would SIGTERM the calling process's own group.
    """
    _sessions_json(fake_cli, [{"target": str(fake_cli / "spec.md"), "url": "http://localhost:8/"}])
    PlannotatorManager().open(str(fake_cli), str(fake_cli / "spec.md"))  # discovered -> pgid 0
    assert cleanup_workspace_sessions(str(fake_cli)) == 1
    assert list(Path(plannotator_result_dir()).glob("*.registry.json")) == []


# ========== Golden replay: real `sessions --json` capture shapes ==========
#
# Verbatim captures of real `plannotator sessions --json` output (real CLI,
# real workspace): empty, one live annotate session, and two simultaneous
# live annotate sessions. These are the golden fixtures closing the host
# probe: replayed byte-exactly through the listing and matching layer below,
# with ONLY `target` rewritten to the sandboxed test workspace (open()
# requires targets under its workspace). Every other captured key is
# untouched: `mode` (NOT `kind`), integer `index`, `url`/`port`/`pid`,
# `project`, `label`, and the ISO-8601 `startedAt` STRING. The PR-URL review
# entry shape was deliberately never captured (that surface is out of use),
# so nothing here guesses it.

P2_EMPTY = "[]\n"

P2_SINGLE = (
    '[{"index":1,"mode":"annotate",'
    '"target":"/home/jbosch/LSST/tkt2/docs/superpowers/specs/'
    '2026-10-12-host-session-services-design.md",'
    '"url":"http://localhost:39893","port":39893,"pid":2715462,'
    '"project":"tkt2",'
    '"label":"annotate-2026-10-12-host-session-services-design.md",'
    '"startedAt":"2026-10-14T22:49:43.469Z"}]\n'
)

P2_BOTH = (
    '[{"index":1,"mode":"annotate",'
    '"target":"/home/jbosch/LSST/tkt2/docs/superpowers/plans/'
    '2026-10-12-host-session-services.md",'
    '"url":"http://localhost:32963","port":32963,"pid":2716717,'
    '"project":"tkt2",'
    '"label":"annotate-2026-10-12-host-session-services.md",'
    '"startedAt":"2026-10-14T22:51:49.929Z"},'
    '{"index":2,"mode":"annotate",'
    '"target":"/home/jbosch/LSST/tkt2/docs/superpowers/specs/'
    '2026-10-12-host-session-services-design.md",'
    '"url":"http://localhost:40285","port":40285,"pid":2716495,'
    '"project":"tkt2",'
    '"label":"annotate-2026-10-12-host-session-services-design.md",'
    '"startedAt":"2026-10-14T22:51:43.369Z"}]\n'
)


def _p2(captured: str, targets: list[str]) -> list[dict]:
    """Load a golden capture and rewrite ONLY each entry's target."""
    entries = json.loads(captured)
    assert len(entries) == len(targets)
    for entry, target in zip(entries, targets):
        entry["target"] = target
    return entries


def test_p2_captures_are_byte_exact() -> None:
    """The embedded strings are the raw captures, keys and all (guarding
    against edits during fixture plumbing).
    """
    single = json.loads(P2_SINGLE)
    assert list(single[0]) == [
        "index",
        "mode",
        "target",
        "url",
        "port",
        "pid",
        "project",
        "label",
        "startedAt",
    ]
    assert single[0]["mode"] == "annotate" and not hasattr(single[0], "kind")
    assert "kind" not in single[0]  # the CLI key is `mode`, never `kind`
    both = json.loads(P2_BOTH)
    assert [e["index"] for e in both] == [1, 2]
    assert all(isinstance(e["startedAt"], str) and e["startedAt"].endswith("Z") for e in both)
    assert json.loads(P2_EMPTY) == []
    assert P2_EMPTY.endswith("]\n")  # the CLI newline is part of the capture


class TestP2GoldenReplay:
    """Replay the captured CLI listing shapes through the real listing
    and matching layer.
    """

    def test_empty_capture_tolerated(self, fake_cli):
        """`[]` parses to an empty listing, distinct from an unusable one."""
        _sessions_json(fake_cli, json.loads(P2_EMPTY))
        assert _sessions_listing() == []

    def test_single_capture_parses_and_matches(self, fake_cli):
        """The live single-entry capture survives parsing (mode/ISO/extra
        keys) and matches by absolute target.
        """
        _sessions_json(fake_cli, _p2(P2_SINGLE, [str(fake_cli / "spec.md")]))
        live = _sessions_listing()
        assert live is not None
        entry = live[0]
        assert entry["mode"] == "annotate" and "kind" not in entry
        assert entry["index"] == 1
        assert entry["port"] == 39893 and entry["pid"] == 2715462
        assert entry["project"] == "tkt2"
        assert entry["label"] == "annotate-2026-10-12-host-session-services-design.md"
        assert entry["startedAt"] == "2026-10-14T22:49:43.469Z"  # ISO string kept verbatim
        assert _match(live, str(fake_cli / "spec.md")) is not None
        assert _match(live, str(fake_cli / "other.md")) is None

    def test_both_captures_match_by_target_not_index(self, fake_cli):
        """Two simultaneous live sessions match by absolute target — never
        by listing index (the capture's index order contradicts the
        alphabetical order of the two targets).
        """
        _sessions_json(
            fake_cli,
            _p2(P2_BOTH, [str(fake_cli / "plan.md"), str(fake_cli / "spec.md")]),
        )
        live = _sessions_listing()
        assert live is not None
        assert _match(live, str(fake_cli / "spec.md"))["pid"] == 2716495  # index 2
        assert _match(live, str(fake_cli / "plan.md"))["pid"] == 2716717  # index 1


def _spawn_sleep():
    """Spawn a live process to stand in for a companion server pid."""
    return sp.Popen(["sleep", "30"])


def test_cleanup_workspace_brainstorm_kills_live_skips_dead(tmp_path):
    """Only sessions with a live pid and no stop marker are terminated."""
    from tkt._sessionmanagers import cleanup_workspace_brainstorm

    live = _spawn_sleep()
    _brainstorm_session(tmp_path, "1-1", pid=live.pid)
    dead = _spawn_sleep()
    dead.kill()
    dead.wait()
    _brainstorm_session(tmp_path, "2-2", pid=dead.pid)
    _brainstorm_session(tmp_path, "3-3", pid=dead.pid, stopped=True)
    _brainstorm_session(tmp_path, "4-4")  # no pid file at all

    killed = cleanup_workspace_brainstorm(str(tmp_path))
    try:
        assert killed == 1
        deadline = time.monotonic() + 5.0
        while live.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert live.poll() is not None, "the live companion must have been SIGTERMed"
    finally:
        live.kill()


def test_cleanup_workspace_brainstorm_kills_nested_project_server(tmp_path):
    """A session under a direct-child project dir (a package, the common
    `brainstorm_server` layout) is reaped along with the root's.
    """
    from tkt._sessionmanagers import cleanup_workspace_brainstorm

    live = _spawn_sleep()
    _brainstorm_session(tmp_path / "pkg", "1-1", pid=live.pid)
    try:
        assert cleanup_workspace_brainstorm(str(tmp_path)) == 1
        deadline = time.monotonic() + 5.0
        while live.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert live.poll() is not None, "the nested companion must have been SIGTERMed"
    finally:
        live.kill()


def test_cleanup_workspace_brainstorm_missing_workspace(tmp_path):
    """No .superpowers dir is a clean no-op."""
    from tkt._sessionmanagers import cleanup_workspace_brainstorm

    assert cleanup_workspace_brainstorm(str(tmp_path / "nope")) == 0
