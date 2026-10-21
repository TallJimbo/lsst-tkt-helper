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
import signal
import subprocess
import subprocess as sp
import time
from pathlib import Path
from unittest import mock

import pytest

from tkt._sessionmanagers import (
    PlannotatorManager,
    _status_ts,
    _write_entry,
    cleanup_workspace_sessions,
    session_registry_dir,
    sweep_session_registry,
)


def test_brainstorm_manager_moved() -> None:
    """BrainstormManager lives in _sessionmanagers and is re-exported."""
    from tkt import _sessionmanagers, mcp_server

    assert _sessionmanagers.BrainstormManager is mcp_server.BrainstormManager


# ========== BrainstormManager (host-side visual-companion server) ==========


def _brainstorm_session(root, name, *, pid=None, stopped=False, info=None):
    """Create a fake session dir laid out like start-server.sh does."""
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
        (state / "server-stopped").write_text("{}\n")
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
    """start() shells out to the launcher with args and no owner pid."""
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
        info = mgr.start(".", port=52999, idle_timeout_minutes=30)
    assert info == started
    argv = run.call_args[0][0]
    env = run.call_args.kwargs["env"]
    assert argv[:2] == ["bash", "/fake/start-server.sh"]
    assert argv[argv.index("--project-dir") + 1] == os.path.realpath(tmp_path)
    # Default owner_pid=None means the companion is not bound to this
    # process: it dies only via close / timeout / sweep, never by
    # the caller's exit.
    assert "--owner-pid" not in argv
    assert argv[argv.index("--idle-timeout-minutes") + 1] == "30"
    assert "--open" in argv
    assert env["BRAINSTORM_PORT"] == "52999"


def test_brainstorm_start_owner_pid_is_passed_through(tmp_path):
    """An explicit owner_pid appends --owner-pid with that pid."""
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
        mgr.start(".", owner_pid=4242)
    argv = run.call_args[0][0]
    assert argv[argv.index("--owner-pid") + 1] == "4242"


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


# ========== PlannotatorManager (host-side review sessions) ==========


FAKE_CLI = """#!/usr/bin/env bash
# Fake plannotator for tests. State lives in $FAKE_STATE.
cmd="$1"; shift
case "$cmd" in
  sessions)
    if [ -f "$FAKE_STATE/sessions.json" ]; then cat "$FAKE_STATE/sessions.json"; else echo "[]"; fi
    ;;
  annotate|review)
    # A well-behaved server cleans up its own group when TERMed. Needed here
    # because the sweep test signals only the leader; production close() uses
    # killpg, which reaches every group member regardless of any trap.
    trap 'kill -TERM 0; exit 143' TERM INT
    echo '{"launched": true}'
    mkdir -p "$FAKE_STATE"; sleep "${FAKE_SLEEP:-60}" &   # stand-in for the blocking session
    echo $! > "$FAKE_STATE/sleep.pid"
    if [ -n "${FAKE_SESSION:-}" ]; then
      # Opt-in: self-register like the real CLI so open() can discover it.
      # $$ is the session's process-group leader (the manager exec'd us).
      S="$FAKE_STATE/sessions.json"
      printf '[{"target":"%s",' "$1" > "$S"
      printf '"url":"http://localhost:4321/","port":4321,' >> "$S"
      printf '"pid":%s,"startedAt":%s}]' "$$" "$(date +%s)" >> "$S"
    fi
    wait
    ;;
  *) echo "unknown: $cmd" >&2; exit 2 ;;
esac
"""


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    """Fake `plannotator` on PATH, data dir + registry relocated
    to tmp_path.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cli = bin_dir / "plannotator"
    cli.write_text(FAKE_CLI)
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PLANNOTATOR_DATA_DIR", str(tmp_path / "plannotator-data"))
    monkeypatch.setenv("HOME", str(tmp_path))  # session_registry_dir lands here
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("FAKE_STATE", str(state))
    return tmp_path


def _sessions_json(tmp_path, entries):
    (tmp_path / "state" / "sessions.json").write_text(json.dumps(entries))


def _touch_record(tmp_path, workspace, name, *, age=0.0):
    rec_dir = tmp_path / "plannotator-data" / "feedback" / Path(workspace).name / "records"
    rec_dir.mkdir(parents=True, exist_ok=True)
    rec = rec_dir / name
    rec.write_text("Reviewer wanted X changed.")
    stamp = time.time() - age
    os.utime(rec, (stamp, stamp))
    return rec


def test_open_discovers_existing_session(fake_cli):
    """open() attaches to an already-listed session and registers it."""
    _sessions_json(
        fake_cli, [{"id": "pn-abc", "target": str(fake_cli / "spec.md"), "url": "http://localhost:41234/abc"}]
    )
    mgr = PlannotatorManager()
    out = mgr.open(str(fake_cli), str(fake_cli / "spec.md"))
    assert out["reused"] is True
    assert out["url"].startswith("http://localhost:")
    assert out["session"] == "pn-abc"


def test_open_spawns_when_absent_and_registers(fake_cli):
    """open() spawns the CLI and records a pidfile entry when none is live."""
    mgr = PlannotatorManager()
    (fake_cli / "spec.md").write_text("# plan")
    # Fake CLI never registers a session; open must still return promptly with
    # a local id and a registry entry (url None until discovered).
    out = mgr.open(str(fake_cli), str(fake_cli / "spec.md"))
    assert out["session"].startswith("pn-") or out["session"].startswith("local-")
    assert (Path(session_registry_dir())).exists()
    entries = [json.loads(p.read_text()) for p in Path(session_registry_dir()).glob("*.json")]
    assert len(entries) == 1 and entries[0]["workspace"] == str(fake_cli)


def test_open_missing_binary_raises(fake_cli, monkeypatch):
    """A missing plannotator binary raises RuntimeError naming the CLI."""
    monkeypatch.setenv("PATH", "/nonexistent-bin-that-does-not-exist")
    (fake_cli / "spec.md").write_text("# p")
    with pytest.raises(RuntimeError, match="plannotator"):
        PlannotatorManager().open(str(fake_cli), str(fake_cli / "spec.md"))


def test_result_scan_mtime_after_open(fake_cli):
    """result() reports feedback records newer than opened_at as feedback."""
    _sessions_json(fake_cli, [{"id": "pn-1", "target": "spec.md", "url": "http://localhost:1/"}])
    mgr = PlannotatorManager()
    (fake_cli / "spec.md").write_text("# p")
    out = mgr.open(str(fake_cli), str(fake_cli / "spec.md"))
    sid = out["session"]
    assert mgr.result(str(fake_cli), sid)["type"] == "pending"
    _touch_record(fake_cli, str(fake_cli), f"{sid}-annotate-feedback.md")
    res = mgr.result(str(fake_cli), sid)
    assert res["type"] == "feedback"
    assert "X changed" in res["text"]


def test_result_ignores_older_records(fake_cli):
    """A record older than opened_at must not count as this session's
    feedback.
    """
    mgr = PlannotatorManager()
    (fake_cli / "spec.md").write_text("# p")
    sid = mgr.open(str(fake_cli), str(fake_cli / "spec.md"))["session"]
    # Write the record with a 1-hour-old mtime, after opening the session.
    _touch_record(fake_cli, str(fake_cli), f"{sid}-annotate-feedback.md", age=3600)
    assert mgr.result(str(fake_cli), sid)["type"] == "pending"


def test_close_kills_process_group(fake_cli):
    """close() SIGTERMs a spawned session's process group and unregisters."""
    mgr = PlannotatorManager()
    (fake_cli / "spec.md").write_text("# p")
    out = mgr.open(str(fake_cli), str(fake_cli / "spec.md"))
    res = mgr.close(str(fake_cli), out["session"])
    assert res["type"] == "closed"
    assert mgr.status(str(fake_cli)) == []


def test_close_discovered_session_unregisters(fake_cli):
    """Host-started sessions (discovered, pgid 0) are un-killable by us: close
    drops our registration and reports 'unregistered', never kills a pgid.
    """
    _sessions_json(
        fake_cli, [{"id": "pn-7", "target": str(fake_cli / "spec.md"), "url": "http://localhost:7/"}]
    )
    mgr = PlannotatorManager()
    sid = mgr.open(str(fake_cli), str(fake_cli / "spec.md"))["session"]
    entry = json.loads((Path(session_registry_dir()) / f"{sid}.json").read_text())
    assert entry["pgid"] == 0 and entry["source"] == "discovered"
    assert mgr.result(str(fake_cli), sid)["type"] == "pending"  # CLI still lists pn-7
    assert mgr.close(str(fake_cli), sid)["type"] == "unregistered"
    assert list(Path(session_registry_dir()).glob("*.json")) == []
    _sessions_json(fake_cli, [])  # CLI truth: gone
    with pytest.raises(ValueError, match="unknown session"):
        mgr.result(str(fake_cli), sid)


def test_sweep_drops_dead_keeps_live(fake_cli):
    """The sweep drops dead pgids and keeps entries for live servers."""
    mgr = PlannotatorManager()
    (fake_cli / "spec.md").write_text("# p")
    sid = mgr.open(str(fake_cli), str(fake_cli / "spec.md"))["session"]
    assert sweep_session_registry() == 0  # live fake server still running
    pid = json.loads((Path(session_registry_dir()) / f"{sid}.json").read_text())["pgid"]
    subprocess.run(["kill", str(pid)], check=False)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and (Path(session_registry_dir()) / f"{sid}.json").exists():
        assert sweep_session_registry() >= 0
        time.sleep(0.1)
    assert not (Path(session_registry_dir()) / f"{sid}.json").exists()


def test_cleanup_workspace_kills_sessions(fake_cli):
    """Workspace cleanup kills and unregisters that workspace's sessions."""
    mgr = PlannotatorManager()
    (fake_cli / "spec.md").write_text("# p")
    mgr.open(str(fake_cli), str(fake_cli / "spec.md"))
    assert cleanup_workspace_sessions(str(fake_cli)) >= 1
    assert list(Path(session_registry_dir()).glob("*.json")) == []


def test_result_closed_after_group_killed(fake_cli):
    """Dismissed-session regression: once the spawned group is killed and
    reaped, result() must report closed — not hang on pending via an
    unreaped zombie group leader (killpg(pgid, 0) succeeds on zombies).
    """
    mgr = PlannotatorManager()
    (fake_cli / "spec.md").write_text("# p")
    sid = mgr.open(str(fake_cli), str(fake_cli / "spec.md"))["session"]
    pgid = json.loads((Path(session_registry_dir()) / f"{sid}.json").read_text())["pgid"]
    os.killpg(pgid, signal.SIGTERM)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if mgr.result(str(fake_cli), sid)["type"] == "closed":
            break
        time.sleep(0.25)
    else:
        pytest.fail("result() never reported closed after the group died")


def test_open_adopts_local_entry_after_late_registration(fake_cli):
    """Late-discovery race: our spawn keyed local-<pid>; once the CLI reports
    the real pn-* id, a re-open must adopt the pgid so close() still kills.
    """
    mgr = PlannotatorManager()
    (fake_cli / "spec.md").write_text("# p")
    sid = mgr.open(str(fake_cli), str(fake_cli / "spec.md"))["session"]
    assert sid.startswith("local-")
    pid = json.loads((Path(session_registry_dir()) / f"{sid}.json").read_text())["pgid"]
    _sessions_json(
        fake_cli, [{"id": "pn-late", "target": str(fake_cli / "spec.md"), "url": "http://localhost:9/"}]
    )
    out2 = mgr.open(str(fake_cli), str(fake_cli / "spec.md"))
    assert out2["session"] == "pn-late" and out2["reused"] is True
    reg = Path(session_registry_dir())
    assert not list(reg.glob("local-*.json"))
    entry = json.loads((reg / "pn-late.json").read_text())
    assert entry["pgid"] == pid and entry["source"] == "spawned"
    assert mgr.close(str(fake_cli), "pn-late")["type"] == "closed"


def test_cleanup_workspace_never_signals_own_group(fake_cli):
    """A discovered (pgid 0) entry must be unregistered, never killpg'd:
    killpg(0) would SIGTERM the calling process's own group.
    """
    _sessions_json(
        fake_cli, [{"id": "pn-8", "target": str(fake_cli / "spec.md"), "url": "http://localhost:8/"}]
    )
    mgr = PlannotatorManager()
    mgr.open(str(fake_cli), str(fake_cli / "spec.md"))  # discovered -> pgid 0
    assert cleanup_workspace_sessions(str(fake_cli)) == 1
    assert list(Path(session_registry_dir()).glob("*.json")) == []


# ========== PlannotatorManager.status (agent pull contract) ==========


def test_status_lists_spawned_session_with_id_and_live_fields(fake_cli, monkeypatch):
    """A spawned, CLI-listed session's status entry carries the registry id
    (the key result()/close() accept) merged with the CLI's live fields.
    """
    monkeypatch.setenv("FAKE_SESSION", "1")
    mgr = PlannotatorManager()
    (fake_cli / "spec.md").write_text("# p")
    sid = mgr.open(str(fake_cli), str(fake_cli / "spec.md"))["session"]
    # The real `sessions --json` carries no id, so our registry keys the
    # spawn local-<pid>; that key is what status must surface.
    assert sid.startswith("local-")
    st = mgr.status(str(fake_cli))
    assert len(st) == 1
    entry = st[0]
    assert entry["id"] == sid
    assert entry["state"] == "live"
    assert entry["url"] == "http://localhost:4321/"
    assert entry["port"] == 4321
    assert entry["target"] == str(fake_cli / "spec.md")


def test_status_lists_closed_registry_session(fake_cli, monkeypatch):
    """Post-close invisibility regression: a submitted review vanishes from
    `sessions --json` while its registry entry remains; status must still
    list it — with its id — so the agent can call result().
    """
    monkeypatch.setenv("FAKE_SESSION", "1")
    mgr = PlannotatorManager()
    (fake_cli / "spec.md").write_text("# p")
    sid = mgr.open(str(fake_cli), str(fake_cli / "spec.md"))["session"]
    _sessions_json(fake_cli, [])  # submitted: gone from the CLI listing
    st = mgr.status(str(fake_cli))
    assert len(st) == 1
    assert st[0]["id"] == sid
    assert st[0]["state"] == "closed"
    assert st[0]["target"] == str(fake_cli / "spec.md")


def test_status_passes_through_unregistered_cli_session(fake_cli):
    """A host-started CLI session with no registry match passes through
    unchanged: it legitimately has no id (``result`` routing covers
    registered, agent-started sessions only).
    """
    cli_entry = {
        "index": 0,
        "mode": "annotate",
        "target": str(fake_cli / "plan.md"),
        "url": "http://localhost:5/",
        "port": 5,
        "pid": 4242,
        "project": str(fake_cli),
        "label": None,
        "startedAt": 1.0,
    }
    _sessions_json(fake_cli, [cli_entry])
    st = PlannotatorManager().status(str(fake_cli))
    assert st == [cli_entry]
    assert "id" not in st[0]
    assert "state" not in st[0]


def test_status_ids_are_accepted_by_result(fake_cli, monkeypatch):
    """Plugin-contract pin: every registry-backed status entry's id is the
    exact key result() takes; a submitted session's feedback is readable
    even though the CLI no longer lists it.
    """
    monkeypatch.setenv("FAKE_SESSION", "1")
    mgr = PlannotatorManager()
    (fake_cli / "spec.md").write_text("# p")
    sid = mgr.open(str(fake_cli), str(fake_cli / "spec.md"))["session"]
    _sessions_json(fake_cli, [])  # submitted
    _touch_record(fake_cli, str(fake_cli), f"{sid}-annotate-feedback.md")
    st = mgr.status(str(fake_cli))
    ids = [e["id"] for e in st if "id" in e]
    assert ids == [sid]
    for session_id in ids:
        assert mgr.result(str(fake_cli), session_id)["type"] == "feedback"


# ========== P2 golden replay: real `sessions --json` capture shapes ==========
#
# Verbatim captures of real `plannotator sessions --json` output (real CLI,
# real workspace): empty, one live annotate session, and two simultaneous
# live annotate sessions. These are the golden fixtures closing the host
# probe: replayed byte-exactly through the manager below, with ONLY
# `target` rewritten to the sandboxed test workspace (the manager requires
# targets under its workspace). Every other captured key is untouched:
# `mode` (NOT `kind`), integer `index`, `url`/`port`/`pid`, `project`,
# `label`, and the ISO-8601 `startedAt` STRING. The PR-URL review entry
# shape was deliberately never captured (that surface is out of use), so
# nothing here guesses it.

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
    """Replay the captured CLI listing shapes through the real manager."""

    def test_empty_capture_tolerated(self, fake_cli):
        """`[]` parses; status is empty; result on an unknown id raises."""
        _sessions_json(fake_cli, json.loads(P2_EMPTY))
        mgr = PlannotatorManager()
        assert mgr._sessions() == []
        assert mgr.status(str(fake_cli)) == []
        with pytest.raises(ValueError, match="unknown session"):
            mgr.result(str(fake_cli), "local-4242")

    def test_single_capture_parses_matches_and_merges(self, fake_cli, monkeypatch):
        """The live single-entry capture survives parsing (mode/ISO/extra
        keys), matches by absolute target, and status enriches it with the
        registry id (local-<pid>) and live state.
        """
        monkeypatch.setenv("FAKE_SESSION", "1")
        mgr = PlannotatorManager()
        (fake_cli / "spec.md").write_text("# p")
        sid = mgr.open(str(fake_cli), str(fake_cli / "spec.md"))["session"]
        assert sid.startswith("local-")  # the real CLI listing carries no id
        _sessions_json(fake_cli, _p2(P2_SINGLE, [str(fake_cli / "spec.md")]))
        live = mgr._sessions()
        assert len(live) == 1
        entry = live[0]
        assert entry["mode"] == "annotate" and "kind" not in entry
        assert entry["index"] == 1
        assert entry["port"] == 39893 and entry["pid"] == 2715462
        assert entry["project"] == "tkt2"
        assert entry["label"] == "annotate-2026-10-12-host-session-services-design.md"
        assert entry["startedAt"] == "2026-10-14T22:49:43.469Z"  # ISO string kept verbatim
        assert mgr._match(live, str(fake_cli / "spec.md")) is not None
        assert mgr._match(live, str(fake_cli / "other.md")) is None
        st = mgr.status(str(fake_cli))
        assert len(st) == 1
        assert st[0]["id"] == sid
        assert st[0]["state"] == "live"
        assert st[0]["url"] == "http://localhost:39893"
        assert st[0]["mode"] == "annotate"  # merged CLI fields survive the merge

    def test_both_captures_distinct_ids_and_id_routing(self, fake_cli, monkeypatch):
        """Two simultaneous live sessions get distinct synthetic ids and
        route by absolute target/registry id — never by listing index.
        """
        monkeypatch.setenv("FAKE_SESSION", "1")
        mgr = PlannotatorManager()
        (fake_cli / "spec.md").write_text("# s")
        (fake_cli / "plan.md").write_text("# p")
        sid_spec = mgr.open(str(fake_cli), str(fake_cli / "spec.md"))["session"]
        sid_plan = mgr.open(str(fake_cli), str(fake_cli / "plan.md"))["session"]
        assert sid_spec != sid_plan  # keyed local-<pgid>: distinct spawns
        # Replay both captures; the capture's index order (1=plan, 2=spec)
        # deliberately contradicts the open() order.
        _sessions_json(
            fake_cli,
            _p2(P2_BOTH, [str(fake_cli / "plan.md"), str(fake_cli / "spec.md")]),
        )
        live = mgr._sessions()
        assert mgr._match(live, str(fake_cli / "spec.md"))["pid"] == 2716495  # index 2
        assert mgr._match(live, str(fake_cli / "plan.md"))["pid"] == 2716717  # index 1
        st = mgr.status(str(fake_cli))
        by_target = {e["target"]: e for e in st if e.get("state") == "live"}
        assert by_target[str(fake_cli / "spec.md")]["id"] == sid_spec
        assert by_target[str(fake_cli / "plan.md")]["id"] == sid_plan
        # Routing is by registry id (result/close keys), not by index:
        assert mgr.result(str(fake_cli), sid_plan)["type"] == "pending"
        assert mgr.close(str(fake_cli), sid_spec)["type"] == "closed"
        with pytest.raises(ValueError, match="unknown session"):
            mgr.result(str(fake_cli), sid_spec)
        st2 = mgr.status(str(fake_cli))
        live2 = [e for e in st2 if e.get("state") == "live"]
        assert [e["id"] for e in live2] == [sid_plan]  # only the other session
        assert mgr.result(str(fake_cli), sid_plan)["type"] == "pending"


# ========== status sorting: ISO-8601 startedAt strings ==========


def test_status_ts_numeric_passthrough() -> None:
    """Numeric startedAt/opened_at pass through as float."""
    assert _status_ts({"startedAt": 1.5}) == 1.5
    assert _status_ts({"startedAt": 7}) == 7.0
    assert _status_ts({"opened_at": 3}) == 3.0


def test_status_ts_iso_zulu_is_absolute_epoch() -> None:
    """A trailing-Z ISO-8601 string parses to its UTC epoch seconds."""
    assert _status_ts({"startedAt": "2026-10-14T22:51:49.929Z"}) == 1792018309.929


def test_status_ts_iso_naive_orders_within_same_zone() -> None:
    """Naive ISO strings parse; only relative order is asserted (a naive
    timestamp's epoch is locale-dependent).
    """
    early = _status_ts({"startedAt": "2026-10-14T10:00:00"})
    late = _status_ts({"startedAt": "2026-10-14T11:00:00"})
    assert early < late
    assert early != 0.0 and late != 0.0


def test_status_ts_garbage_and_missing_default_zero() -> None:
    """Unparseable or absent timestamps fall back to 0.0."""
    assert _status_ts({"startedAt": "yesterday-ish"}) == 0.0
    assert _status_ts({"startedAt": None}) == 0.0
    assert _status_ts({}) == 0.0
    assert _status_ts({"other": 5}) == 0.0


def test_status_sorts_iso_started_at_newest_first(fake_cli):
    """Two host-started (unregistered) sessions with ISO startedAt strings
    one hour apart must list the NEWER first.

    Replay of the two-entry golden capture (targets moved inside the test
    workspace so unregistered pass-through applies; NO tkt registry entries
    exist). The listing puts the OLDER session first, so the status sort has
    to reorder by the parsed ISO timestamps — a float()-only implementation
    reads both as 0.0 and keeps the input order.
    """
    older = "2026-10-14T21:51:49.929Z"  # capture time minus one hour
    newer = "2026-10-14T22:51:49.929Z"
    entries = _p2(P2_BOTH, [str(fake_cli / "host-a.md"), str(fake_cli / "host-b.md")])
    entries[0]["startedAt"] = older  # plan slot: older
    entries[1]["startedAt"] = newer  # spec slot: newer
    _sessions_json(fake_cli, entries)
    st = PlannotatorManager().status(str(fake_cli))
    assert [e["target"] for e in st] == [
        str(fake_cli / "host-b.md"),
        str(fake_cli / "host-a.md"),
    ]


# ========== result(): target-derived Plannotator project directory ==========
#
# The real Plannotator CLI keys its feedback store by a TARGET-derived
# project label (a package dir in a multi-package workspace lands under
# feedback/<package>/records/, sessions report project:<package>), NOT by
# tkt's workspace name. Single-repo workspaces make the two coincide.


def _touch_project_record(tmp_path, project_name, name, *, text="Reviewer wanted X changed.", age=0.0):
    rec_dir = tmp_path / "plannotator-data" / "feedback" / project_name / "records"
    rec_dir.mkdir(parents=True, exist_ok=True)
    rec = rec_dir / name
    rec.write_text(text)
    stamp = time.time() - age
    os.utime(rec, (stamp, stamp))
    return rec


def _registry_entry(ws, target, *, sid, pgid=0, opened_at=None):
    _write_entry(
        sid,
        {
            "pgid": pgid,
            "workspace": str(ws),
            "target": str(target),
            "kind": "review",
            "opened_at": opened_at if opened_at is not None else time.time() - 3600.0,
            "source": "spawned",
        },
    )
    return sid


def _multi_pkg_ws(fake_cli):
    """Create a tkt multi-package workspace: DM-56581 with a package dir
    inside it.
    """
    ws = fake_cli / "DM-56581"
    (ws / "images").mkdir(parents=True)
    return ws


def test_result_finds_records_under_target_derived_project_dir(fake_cli):
    """Feedback stored under the TARGET-derived name is found.

    The incident: review opened against <ws>/images; the record landed under
    feedback/images/ while the workspace basename is DM-56581. result()
    must not report closed just because the workspace-named directory is
    empty.
    """
    ws = _multi_pkg_ws(fake_cli)
    sid = _registry_entry(ws, ws / "images", sid="local-4242", pgid=0)
    _touch_project_record(
        fake_cli, "images", "2026-10-15T14-31-43-477Z-review-feedback.md", text="Target-keyed feedback"
    )
    res = PlannotatorManager().result(str(ws), sid)
    assert res["type"] == "feedback"
    assert res["text"] == "Target-keyed feedback"


def test_result_target_dir_stale_record_stays_invisible(fake_cli):
    """Freshness applies across candidates: a pre-opened_at record under the
    target-derived directory must not count as feedback.
    """
    ws = _multi_pkg_ws(fake_cli)
    now = time.time()
    keeper = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        sid = _registry_entry(ws, ws / "images", sid="local-4242", pgid=keeper.pid, opened_at=now)
        _touch_project_record(fake_cli, "images", "old-review-feedback.md", age=3600.0)
        res = PlannotatorManager().result(str(ws), sid)
        assert res["type"] == "pending"  # live pgid, nothing fresh
    finally:
        keeper.terminate()
        keeper.wait()


def test_result_merges_candidates_newest_wins(fake_cli):
    """Records under BOTH candidate directories merge into one set: the same
    record name in both must not double-count, and newest wins across the
    merged set regardless of which candidate it lives under.
    """
    ws = _multi_pkg_ws(fake_cli)
    sid = _registry_entry(ws, ws / "images", sid="local-4242", pgid=0)
    _touch_project_record(
        fake_cli, "DM-56581", "r-annotate-feedback.md", text="workspace copy (older)", age=60.0
    )
    _touch_project_record(fake_cli, "images", "r-annotate-feedback.md", text="target copy (newer)", age=5.0)
    res = PlannotatorManager().result(str(ws), sid)
    assert res["type"] == "feedback"
    assert res["text"] == "target copy (newer)"
    # Newest wins from the other side too.
    _touch_project_record(
        fake_cli, "DM-56581", "r2-annotate-feedback.md", text="workspace copy (newest)", age=0.0
    )
    res = PlannotatorManager().result(str(ws), sid)
    assert res["text"] == "workspace copy (newest)"


def test_result_url_target_uses_workspace_candidate(fake_cli):
    """A PR-URL target's name-derived directory is inert: records resolve via
    the workspace-name candidate.
    """
    ws = fake_cli / "DM-56581"
    ws.mkdir()
    sid = _registry_entry(ws, "https://host/o/r/pull/123", sid="local-4242", pgid=0)
    _touch_project_record(fake_cli, "DM-56581", "pr-review-123-feedback.md", text="PR feedback")
    res = PlannotatorManager().result(str(ws), sid)
    assert res["type"] == "feedback"
    assert res["text"] == "PR feedback"
