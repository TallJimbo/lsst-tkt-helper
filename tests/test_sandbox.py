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

import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import click
import git
import pytest

from tkt._workspace import Workspace
from tkt.sandbox import (
    SESSION_SOCKET_NAME,
    Sandbox,
    _bridge_net_dir,
    _socat_system_arg,
    cleanup_stale_bridges,
    opencode_config_content,
    render_agents_md,
)


def _inner_of(argv):
    """Return the inner bash script from a built bwrap argv."""
    return argv[argv.index("--") + 3]


def _find_slice(argv, needle):
    """Return the start index of contiguous ``needle`` within ``argv``.

    Raises ``AssertionError`` if the slice is not present.
    """
    needle = list(needle)
    for i in range(len(argv) - len(needle) + 1):
        if argv[i : i + len(needle)] == needle:
            return i
    raise AssertionError(f"Slice {needle!r} not found in argv.")


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace with a main clone and an agent worktree for ``pkg``.

    The main repo's package ``pkg`` lives at ``<dir>/pkg`` on branch
    ``tickets/X``, with an agent worktree at ``<dir>/.agent/pkg`` on branch
    ``tickets/X-agent``.
    """
    repo_dir = tmp_path / "pkg"
    repo_dir.mkdir()
    repo = git.Repo.init(repo_dir)
    repo.config_writer().set_value("user", "name", "test").release()
    repo.config_writer().set_value("user", "email", "test@test").release()
    (repo_dir / "file1.txt").write_text("file1\n")
    repo.git.add("file1.txt")
    repo.git.commit("-m", "base")
    repo.git.checkout("-b", "tickets/X")
    (repo_dir / "file1.txt").write_text("file1\nhuman change\n")
    repo.git.add("file1.txt")
    repo.git.commit("-m", "human work")
    agent_dir = tmp_path / ".agent" / "pkg"
    agent_dir.parent.mkdir()
    repo.git.worktree("add", "-b", "tickets/X-agent", str(agent_dir), "tickets/X")
    return Workspace(
        ticket="X",
        directory=str(tmp_path),
        metapackage_name="m",
        metapackage_tag="t",
        packages={"pkg": "tickets/X"},
        externals={},
        workspace_eups_product="x",
        tools=("sandbox",),
    )


@pytest.fixture
def sandbox():
    """Return a `Sandbox` instance with an empty command."""
    return Sandbox(command=[])


def _agent(workspace):
    return git.Repo(str(workspace.directory) + "/.agent/pkg")


def test_reset_saves_uncommitted_work_and_resets(workspace, sandbox):
    """Verify reset stashes work, backs up commits, and restores the branch."""
    agent_dir = Path(f"{workspace.directory}/.agent/pkg")
    # The agent makes a commit not on the human branch, then leaves uncommitted
    # work plus untracked and ignored files behind.
    agent = git.Repo(agent_dir)
    agent.config_writer().set_value("user", "name", "agent").release()
    agent.config_writer().set_value("user", "email", "agent@agent").release()
    (agent_dir / "file1.txt").write_text("file1\nhuman change\nagent commit\n")
    agent.git.add("file1.txt")
    agent.git.commit("-m", "agent commit")
    (agent_dir / "file1.txt").write_text("file1\nhuman change\nagent commit\nuncommitted\n")
    (agent_dir / "untracked.txt").write_text("untracked\n")
    (agent_dir / "ignored.log").write_text("ignored\n")
    (agent_dir / ".gitignore").write_text("*.log\n")
    agent.git.add(".gitignore")
    agent.git.commit("-m", "gitignore")
    (agent_dir / "ignored2.log").write_text("ignored2\n")
    human_head = agent.rev_parse("tickets/X")

    sandbox.reset(workspace)

    agent = git.Repo(agent_dir)
    # The worktree is clean, reset to the human branch, with no leftovers.
    assert not agent.is_dirty(untracked_files=True)
    assert (agent_dir / "file1.txt").read_text() == "file1\nhuman change\n"
    assert not (agent_dir / "untracked.txt").exists()
    assert not (agent_dir / "ignored2.log").exists()
    assert agent.head.commit == human_head
    # Uncommitted work was stashed (including untracked and ignored files via
    # `--all`).
    stash = agent.git.stash("list")
    assert "tkt reset backup: pkg" in stash
    stash_files = agent.git.stash("show", "--name-only", "--include-untracked", "stash@{0}")
    assert "untracked.txt" in stash_files
    assert "ignored2.log" in stash_files
    # Unmerged commits were saved to a timestamped backup branch.
    saved = [b.name for b in agent.heads if b.name.startswith("tickets/X-agent-saved-")]
    assert saved
    assert any(s.endswith("-") is False and "T" in s for s in saved)


def test_reset_clean_worktree_makes_no_backups(workspace, sandbox):
    """Verify a clean, in-sync worktree creates no stash or backup branch."""
    agent_dir = f"{workspace.directory}/.agent/pkg"
    agent = git.Repo(agent_dir)

    sandbox.reset(workspace)

    agent = git.Repo(agent_dir)
    assert not agent.is_dirty(untracked_files=True)
    assert not agent.git.stash("list").strip()
    assert not [b.name for b in agent.heads if "saved" in b.name]


def test_reset_skips_package_without_agent_worktree(workspace, sandbox):
    """Verify a package without an agent worktree is skipped without error."""
    # Remove the agent worktree so the package has none to reset.
    repo = git.Repo(f"{workspace.directory}/pkg")
    repo.git.worktree("remove", f"{workspace.directory}/.agent/pkg")
    sandbox.reset(workspace)


def test_restricted_argv_isolates_network_and_bridges(workspace, sandbox, tmp_path):
    """Restricted mode isolates net and bridges the LLM port."""
    net_dir = tmp_path / "net"
    net_dir.mkdir()
    argv = sandbox._build_bwrap_argv(
        workspace, shell=False, network=False, net_dir=str(net_dir), ports=(8080,)
    )
    assert "--unshare-net" in argv
    assert ("--bind", str(net_dir), str(net_dir)) in [tuple(argv[i : i + 3]) for i in range(len(argv) - 2)]
    inner = _inner_of(argv)
    assert "socat TCP4-LISTEN:8080,fork,reuseaddr UNIX-CONNECT:" in inner
    assert f"{net_dir}/llm-8080.sock" in inner
    # The host -> sandbox reverse bridge is gone (session sockets replaced it).
    assert "vc.sock" not in inner


def test_multiple_ports_emit_one_socat_each(workspace, sandbox, tmp_path):
    """A list of ports produces one in-sandbox socat per port."""
    net_dir = tmp_path / "net"
    net_dir.mkdir()
    argv = sandbox._build_bwrap_argv(
        workspace, shell=False, network=False, net_dir=str(net_dir), ports=(8080, 8000)
    )
    inner = _inner_of(argv)
    assert "socat TCP4-LISTEN:8080,fork,reuseaddr UNIX-CONNECT:" in inner
    assert "socat TCP4-LISTEN:8000,fork,reuseaddr UNIX-CONNECT:" in inner
    assert f"{net_dir}/llm-8080.sock" in inner
    assert f"{net_dir}/llm-8000.sock" in inner


def test_full_network_argv_shares_host_network(workspace, sandbox):
    """Full-network mode omits --unshare-net and any bridge socket mount."""
    argv = sandbox._build_bwrap_argv(workspace, shell=False, network=True)
    assert "--unshare-net" not in argv
    assert "llm.sock" not in argv
    assert "vc.sock" not in argv
    assert "socat TCP4-LISTEN" not in _inner_of(argv)


def test_no_remote_mcp_token_or_override_in_full_network_argv(tmp_path, monkeypatch):
    """Full-network runs are unsupported for remote MCP by design (D3):
    no token export, no config override — and restricted runs already
    carry only the socket override, never the token (Task 2 test).
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    tok = tmp_path / "tok"
    tok.write_text("ghp_secret\n")
    remote = {"github": {"url": "https://api.githubcopilot.com/mcp/", "token_file": str(tok)}}
    argv = Sandbox(command=[], remote_mcp=remote)._build_common_argv(
        home=str(tmp_path),
        mounts=[],
        inner="exec true",
        network=True,
    )
    joined = " ".join(argv)
    assert "ghp_secret" not in joined
    assert "GITHUB_TOKEN" not in joined
    assert "mcp-github.sock" not in joined  # no override without the bridge


def test_single_repo_network_modes(tmp_path, sandbox):
    """Single-repo restricted vs. full differ like the workspace modes."""
    net_dir = tmp_path / "net"
    net_dir.mkdir()
    restricted = sandbox._build_single_repo_argv(
        str(tmp_path), shell=False, network=False, net_dir=str(net_dir), ports=(8080,)
    )
    assert "--unshare-net" in restricted
    assert ("--bind", str(net_dir), str(net_dir)) in [
        tuple(restricted[i : i + 3]) for i in range(len(restricted) - 2)
    ]
    full = sandbox._build_single_repo_argv(str(tmp_path), shell=False, network=True)
    assert "--unshare-net" not in full
    assert "llm.sock" not in full
    assert "vc.sock" not in full
    assert "socat TCP4-LISTEN" not in _inner_of(full)


def test_bridge_port_default_and_override(workspace, tmp_path):
    """The bridge port defaults to 8080 and can be overridden via config."""
    net_dir = tmp_path / "net"
    net_dir.mkdir()

    default = Sandbox(command=[])
    inner = _inner_of(
        default._build_bwrap_argv(workspace, shell=False, network=False, net_dir=str(net_dir), ports=(8080,))
    )
    assert "TCP4-LISTEN:8080" in inner

    custom = Sandbox.from_json_data({"command": [], "port": 9999})
    assert custom._ports == (9999,)
    inner = _inner_of(
        custom._build_bwrap_argv(workspace, shell=False, network=False, net_dir=str(net_dir), ports=(9999,))
    )
    assert "TCP4-LISTEN:9999" in inner


def test_from_json_rejects_vc_port(tmp_path):
    """The removed vc_port configuration entry is now rejected."""
    with pytest.raises(ValueError, match="Unexpected entries"):
        Sandbox.from_json_data({"command": [], "vc_port": 9090})


def test_start_bridge_session_listener(workspace, tmp_path, monkeypatch, request):
    """The bridge spawns a session listener; session.sock exists and serves
    the spawn command over the socket.
    """
    # socat caps UNIX socket paths at 108 characters and the bridge's socket
    # directory lives under HOME, which pytest's default tmp_path base puts
    # right at that boundary; use a short HOME instead.
    short_home = tempfile.TemporaryDirectory(prefix="tktb", dir="/tmp")
    request.addfinalizer(short_home.cleanup)
    monkeypatch.setenv("HOME", short_home.name)
    sandbox = Sandbox(command=[], port=18089)
    net_dir, procs = sandbox._start_bridge(str(tmp_path / "ws"))
    try:
        sock = os.path.join(net_dir, SESSION_SOCKET_NAME)
        assert os.path.exists(sock)
        # No host-side TCP-LISTEN survives anywhere in the bridge.
        for p in procs:
            with open(f"/proc/{p.pid}/cmdline", "rb") as f:
                assert b"TCP-LISTEN" not in f.read()
        # The socket actually SERVES the session server (socat fork SYSTEM):
        # a real MCP initialize handshake must name us back.
        init = (
            '{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
            '{"protocolVersion":"2024-11-05","capabilities":{},'
            '"clientInfo":{"name":"probe","version":"0"}}}\n'
        )
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(10.0)
        try:
            conn.connect(sock)
            conn.sendall(init.encode())
            reply = b""
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and b"tkt-session" not in reply:
                try:
                    chunk = conn.recv(65536)
                except TimeoutError:
                    break
                if not chunk:
                    break
                reply += chunk
        finally:
            conn.close()
        assert b"tkt-session" in reply
    finally:
        sandbox._stop_bridge(net_dir, procs)
    assert not os.path.exists(net_dir)


def test_from_json_data_parses_remote_mcp():
    """remote_mcp config parses; missing keys and extra keys are rejected."""
    ok = Sandbox.from_json_data(
        {"command": [], "remote_mcp": {"github": {"url": "https://x/mcp/", "token_file": "~/tok"}}}
    )
    assert ok._remote_mcp == {"github": {"url": "https://x/mcp/", "token_file": "~/tok"}}
    # absent => empty
    assert Sandbox.from_json_data({"command": []})._remote_mcp == {}
    with pytest.raises(ValueError):
        Sandbox.from_json_data({"command": [], "remote_mcp": {"github": {"url": "x"}}})
    with pytest.raises(ValueError):
        Sandbox.from_json_data({"command": [], "remote_mcp": {"github": {"token_file": "t"}}})
    with pytest.raises(ValueError):
        Sandbox.from_json_data(
            {"command": [], "remote_mcp": {"github": {"url": "x", "token_file": "t", "bogus": 1}}}
        )
    with pytest.raises(ValueError):
        Sandbox.from_json_data({"command": [], "remote_mcp": ["github"]})


def test_bridged_remote_mcp_requires_token_file(tmp_path):
    """Only entries whose host token file exists are bridged."""
    tok = tmp_path / "tok"
    sb = Sandbox(command=[], remote_mcp={"github": {"url": "u", "token_file": str(tok)}})
    assert sb._bridged_remote_mcp() == {}
    tok.write_text("t\n")
    assert set(sb._bridged_remote_mcp()) == {"github"}


def test_start_bridge_remote_mcp_listener(workspace, tmp_path, monkeypatch, request):
    """A bridged entry gets an mcp-<name>.sock listener; an unbridged one
    does not and must not stall or fail bridge startup.
    """
    # Short HOME for the same 108-character socat socket-path reason as
    # test_start_bridge_session_listener.
    short_home = tempfile.TemporaryDirectory(prefix="tktb", dir="/tmp")
    request.addfinalizer(short_home.cleanup)
    monkeypatch.setenv("HOME", short_home.name)
    tok = tmp_path / "tok"
    tok.write_text("secret\n")
    sandbox = Sandbox(
        command=[],
        port=18089,
        remote_mcp={
            "github": {"url": "http://127.0.0.1:1/mcp", "token_file": str(tok)},
            "nope": {"url": "http://127.0.0.1:1/mcp", "token_file": str(tmp_path / "absent")},
        },
    )
    net_dir, procs = sandbox._start_bridge(str(tmp_path / "ws"))
    try:
        assert os.path.exists(os.path.join(net_dir, "mcp-github.sock"))
        assert not os.path.exists(os.path.join(net_dir, "mcp-nope.sock"))
    finally:
        sandbox._stop_bridge(net_dir, procs)
    assert not os.path.exists(net_dir)


def test_socat_system_arg_escapes_separators():
    """Colons/commas are socat parameter separators: escaped; clean
    commands unchanged.
    """
    arg = _socat_system_arg(["python", "-m", "pkg", "--url", "https://a.example/mcp"])
    assert arg.startswith("SYSTEM:")
    assert "\\:" in arg and "https\\://a.example/mcp" in arg
    arg2 = _socat_system_arg(["/bin/true", "plain"])
    assert arg2 == "SYSTEM:/bin/true plain"
    arg3 = _socat_system_arg(["sh", "-c", "echo a,b"])
    assert "\\," in arg3


def test_start_bridge_escapes_socat_system_args(workspace, tmp_path, monkeypatch):
    """Listener SYSTEM args must have ':' and ',' escaped so forked
    children can parse them (regression: URL with https:// broke
    parsing).
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    tok = tmp_path / "tok"
    tok.write_text("secret\n")
    captured: list[list[str]] = []
    real_popen = subprocess.Popen

    def fake_popen(argv, **kwargs):
        argv = list(argv)
        captured.append(argv)
        for a in argv:
            if a.startswith("UNIX-LISTEN:"):
                path = a[len("UNIX-LISTEN:") :].rsplit(",", 1)[0]
                open(path, "w").close()
        safe = {
            k: v for k, v in kwargs.items() if k in ("stdout", "stderr", "start_new_session", "preexec_fn")
        }
        return real_popen(["sleep", "3600"], **safe)

    monkeypatch.setattr("tkt.sandbox.subprocess.Popen", fake_popen)
    sandbox = Sandbox(
        command=[],
        port=18089,
        remote_mcp={"github": {"url": "https://api.githubcopilot.com/mcp/", "token_file": str(tok)}},
    )
    net_dir, procs = sandbox._start_bridge(str(tmp_path / "ws"))
    try:
        sys_args = [a[2] for a in captured if a[0] == "socat" and a[2].startswith("SYSTEM:")]
        assert sys_args, "no socat listeners captured"
        for s in sys_args:
            body = s[len("SYSTEM:") :]
            # every unescaped colon would be parsed as a parameter separator
            assert not re.search(r"(?<!\\):", body), s
        github_arg = next(s for s in sys_args if "remote_mcp_proxy" in s)
        assert "\\:" in github_arg
        assert "https\\://api.githubcopilot.com/mcp/" in github_arg
    finally:
        sandbox._stop_bridge(net_dir, procs)


def test_common_argv_injects_remote_mcp_override(workspace, tmp_path, monkeypatch):
    """Restricted runs redirect the bridged server key to its socket;
    unbridged entries get no override (host config entry stays).
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    tok = tmp_path / "tok"
    tok.write_text("secret\n")
    net_dir = tmp_path / "net-r"
    net_dir.mkdir()
    remote = {
        "github": {"url": "https://api.githubcopilot.com/mcp/", "token_file": str(tok)},
        "ghost": {"url": "https://example.invalid/mcp/", "token_file": str(tmp_path / "absent")},
    }
    argv = Sandbox(command=[], remote_mcp=remote)._build_common_argv(
        home=str(tmp_path),
        mounts=[],
        inner="exec true",
        network=False,
        net_dir=str(net_dir),
        ports=(18089,),
    )
    cfg = json.loads(argv[argv.index("OPENCODE_CONFIG_CONTENT") + 1])
    assert cfg["mcp"]["github"]["command"][-1] == f"UNIX-CONNECT:{net_dir / 'mcp-github.sock'}"
    assert "ghost" not in cfg["mcp"]
    # the token value never appears in restricted-mode argv
    assert "secret" not in " ".join(argv)


def test_common_argv_injects_session_env(workspace, tmp_path, monkeypatch):
    """The session socket env vars are injected after the configured env."""
    monkeypatch.setenv("HOME", str(tmp_path))
    net_dir = tmp_path / "net-x"
    net_dir.mkdir()
    argv = Sandbox(command=[], env={"FOO": "bar"})._build_common_argv(
        home=str(tmp_path),
        mounts=[],
        inner="exec true",
        network=False,
        net_dir=str(net_dir),
        ports=(18089,),
    )
    assert "--unsetenv" in argv
    i = argv.index("TKT_SESSION_SOCKET")
    assert argv[i + 1] == str(net_dir / "session.sock")
    j = argv.index("OPENCODE_CONFIG_CONTENT")
    cfg = json.loads(argv[j + 1])
    assert cfg["mcp"]["tkt-session"]["type"] == "local"
    assert cfg["mcp"]["tkt-session"]["command"][-1].endswith("session.sock")
    # the env-config plugin channel is dead in the installed OpenCode: no
    # `plugin` key
    assert "plugin" not in cfg
    # ours come after configured env so local.jsonc cannot clobber them
    assert argv.index("FOO") < argv.index("TKT_SESSION_SOCKET")
    joined = " ".join(argv)
    assert "TCP-LISTEN" not in joined  # in-sandbox vc socat is gone, too


def test_opencode_config_content_wires_bridged_mcp():
    """Session socket plus one local socat override per bridged remote MCP."""
    cfg = opencode_config_content("/x/net-1", ["github"])
    assert set(cfg) == {"mcp"}  # the env-config plugin channel is dead: mcp-only
    assert cfg["mcp"]["tkt-session"]["command"][-1] == "UNIX-CONNECT:/x/net-1/session.sock"
    assert cfg["mcp"]["github"]["type"] == "local"
    assert cfg["mcp"]["github"]["command"][-1] == "UNIX-CONNECT:/x/net-1/mcp-github.sock"
    # no names => session only (unchanged behavior)
    assert set(opencode_config_content("/x/net-1")["mcp"]) == {"tkt-session"}


def test_inner_script_has_no_vc_socat(workspace, tmp_path, monkeypatch):
    """The in-sandbox reverse-bridge socat is gone from the inner script."""
    monkeypatch.setenv("HOME", str(tmp_path))
    net_dir = tmp_path / "net-y"
    net_dir.mkdir()
    argv = Sandbox(command=[])._build_common_argv(
        home=str(tmp_path),
        mounts=[],
        inner="exec true",
        network=False,
        net_dir=str(net_dir),
        ports=(18089,),
    )
    inner = argv[-1]
    assert "vc.sock" not in inner and "UNIX-LISTEN" not in inner


def test_remove_cleans_workspace_sessions(tmp_path, monkeypatch):
    """Sandbox.remove is a no-op (no raise) when the registry is empty."""
    monkeypatch.setenv("HOME", str(tmp_path))
    Sandbox(command=[]).remove(str(tmp_path / "ws"))  # no registry -> no-op, no raise


def test_from_json_data_accepts_network_flag():
    """from_json_data parses the network boolean."""
    restricted = Sandbox.from_json_data({"command": []})
    assert restricted._network is False
    full = Sandbox.from_json_data({"command": [], "network": True})
    assert full._network is True
    with pytest.raises(ValueError):
        Sandbox.from_json_data({"command": [], "network": "yes"})
    with pytest.raises(ValueError):
        Sandbox.from_json_data({"command": [], "port": "8080"})
    with pytest.raises(ValueError):
        Sandbox.from_json_data({"command": [], "port": True})


def test_from_json_data_accepts_list_or_int_port():
    """from_json_data parses port as a single int or a list of ints."""
    single = Sandbox.from_json_data({"command": [], "port": 9999})
    assert single._ports == (9999,)
    multi = Sandbox.from_json_data({"command": [], "port": [8080, 8000]})
    assert multi._ports == (8080, 8000)
    with pytest.raises(ValueError):
        Sandbox.from_json_data({"command": [], "port": [8080, "8080"]})
    with pytest.raises(ValueError):
        Sandbox.from_json_data({"command": [], "port": []})


@pytest.mark.skipif(shutil.which("socat") is None, reason="socat not installed")
def test_bridge_lifecycle(tmp_path, monkeypatch):
    """The host-side bridges start one socat per port and tear down cleanly."""
    monkeypatch.setenv("HOME", str(tmp_path))  # state dir lands under tmp_path
    sandbox = Sandbox(command=[], port=[18089, 18088])

    net_dir, procs = sandbox._start_bridge(str(tmp_path / "ws"))
    try:
        assert os.path.isdir(net_dir)
        assert os.path.exists(os.path.join(net_dir, "llm-18089.sock"))
        assert os.path.exists(os.path.join(net_dir, "llm-18088.sock"))
        assert os.path.exists(os.path.join(net_dir, SESSION_SOCKET_NAME))
        assert len(procs) == 3  # one socat per LLM port + the session listener
        assert all(proc.poll() is None for proc in procs)  # all socats alive
    finally:
        sandbox._stop_bridge(net_dir, procs)

    assert all(proc.poll() is not None for proc in procs)  # socats terminated
    assert not os.path.exists(net_dir)  # socket dir removed


def test_bridge_net_dir_extracts_from_argv():
    """_bridge_net_dir parses the shared net dir out of bridge socat argv."""
    host = [
        "socat",
        "TCP4-LISTEN:8080,fork,reuseaddr",
        "UNIX-CONNECT:/home/jbosch/.local/state/tkt/net-abc123/llm.sock",
    ]
    assert _bridge_net_dir(host) == "net-abc123"
    sandbox_side = [
        "socat",
        "UNIX-LISTEN:/home/jbosch/.local/state/tkt/net-abc123/vc.sock,fork",
        "TCP4:127.0.0.1:8081",
    ]
    assert _bridge_net_dir(sandbox_side) == "net-abc123"
    # Non-bridge or unrelated processes carry no such dir.
    assert _bridge_net_dir(["socat", "TCP-LISTEN:80,reuseaddr", "TCP4:127.0.0.1:8080"]) is None
    assert _bridge_net_dir(["python3", "-m", "http.server"]) is None


@pytest.mark.skipif(shutil.which("socat") is None, reason="socat not installed")
def test_cleanup_stale_bridges_dry_run():
    """cleanup_stale_bridges flags only socats whose net dir is gone."""
    state_dir = os.path.join(os.path.expanduser("~"), ".local", "state", "tkt")

    # A live bridge keeps its net dir, so it must not be flagged.
    net_dir, procs = Sandbox(command=[], port=18089)._start_bridge(os.devnull)
    assert os.path.isdir(net_dir)

    # A stale socat: bind into a net dir, then remove the dir (the socat
    # survives holding the now-unlinked socket, mirroring a bad shutdown).
    stale_net = os.path.join(state_dir, "net-stale-test")
    os.makedirs(stale_net, exist_ok=True)
    stale = subprocess.Popen(
        ["socat", f"UNIX-LISTEN:{stale_net}/llm.sock,fork", "TCP4:127.0.0.1:18089"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 5.0
    while not os.path.exists(os.path.join(stale_net, "llm.sock")) and time.monotonic() < deadline:
        time.sleep(0.05)
    shutil.rmtree(stale_net, ignore_errors=True)
    try:
        killed, dirs = cleanup_stale_bridges(dry_run=True)
        assert "net-stale-test" in dirs
        # The live bridge is untouched.
        assert os.path.basename(net_dir) not in dirs
    finally:
        stale.terminate()
        stale.wait()
        Sandbox(command=[])._stop_bridge(net_dir, procs)


def test_warm_holder_argv_workspace(workspace):
    """Warm-holder argv: ``.agent`` writable, no LLM bridge."""
    tool = Sandbox(command=["opencode", "acp"])
    argv = tool.warm_holder_argv(
        workspace=workspace,
        inner='while read line; do echo "$line"; done',
    )
    # agent dir is writable
    assert "--bind" in argv
    assert os.path.join(workspace.directory, ".agent") in argv
    # restricted network, no socat bridge
    assert "--unshare-net" in argv
    assert "socat" not in " ".join(_inner_of(argv))
    assert _inner_of(argv) == 'while read line; do echo "$line"; done'


def test_warm_holder_argv_single_repo(tmp_path):
    """A warm-holder argv for a single repo bind-mounts the repo read-write."""
    tool = Sandbox(command=["opencode", "acp"])
    argv = tool.warm_holder_argv(
        repo_dir=str(tmp_path),
        inner="echo hi",
    )
    assert "--bind" in argv
    assert str(tmp_path) in argv


# ---------------------------------------------------------------------------
# OpenCode background-service credential masking
# ---------------------------------------------------------------------------


def _xdg_config_home(tmp_path, monkeypatch, *, with_service):
    """Point XDG_CONFIG_HOME at a tmp dir, with or without service.json.

    Returns the service.json path (whether or not it was created).
    """
    config_home = tmp_path / "xdg-config"
    service_json = os.path.join(str(config_home), "opencode", "service.json")
    if with_service:
        os.makedirs(os.path.dirname(service_json))
        Path(service_json).write_text('{"password": "s3cret"}\n')
    else:
        config_home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    return service_json


def test_service_config_masked_in_common_argv(workspace, tmp_path, monkeypatch):
    """The service credential file is masked after all mounts."""
    service_json = _xdg_config_home(tmp_path, monkeypatch, with_service=True)
    ro_dir = tmp_path / "ro-mount"
    rw_dir = tmp_path / "rw-mount"
    ro_dir.mkdir()
    rw_dir.mkdir()
    sandbox = Sandbox(command=[], mounts_ro=[str(ro_dir)], mounts_rw=[str(rw_dir)])
    argv = sandbox._build_bwrap_argv(workspace, shell=False, network=True)
    mask_idx = _find_slice(argv, ["--ro-bind", "/dev/null", service_json])
    # The /dev/null overlay is mounted after every (try) mount so nothing
    # later can re-expose the file...
    for flag in ("--ro-bind-try", "--bind-try"):
        for i in (j for j, token in enumerate(argv) if token == flag):
            assert i < mask_idx
    # ...and before the namespace/cleanup block.
    assert mask_idx < argv.index("--unshare-user")


def test_service_config_mask_skipped_when_absent(workspace, sandbox, tmp_path, monkeypatch):
    """No /dev/null overlay is added when the host has no service.json."""
    _xdg_config_home(tmp_path, monkeypatch, with_service=False)
    argv = sandbox._build_bwrap_argv(workspace, shell=False, network=True)
    assert "/dev/null" not in argv


def test_unsetenv_precedes_configured_setenv(workspace, tmp_path, monkeypatch):
    """Service-password vars are unset before the configured --setenv."""
    _xdg_config_home(tmp_path, monkeypatch, with_service=False)
    sandbox = Sandbox(command=[], env={"OPENCODE_PASSWORD": "x"})
    argv = sandbox._build_bwrap_argv(workspace, shell=False, network=True)
    unset_idx = _find_slice(argv, ["--unsetenv", "OPENCODE_PASSWORD"])
    _find_slice(argv, ["--unsetenv", "OPENCODE_SERVER_PASSWORD"])
    set_idx = _find_slice(argv, ["--setenv", "OPENCODE_PASSWORD", "x"])
    assert unset_idx < set_idx


def test_warm_holder_argv_masks_service_config(workspace, tmp_path, monkeypatch):
    """Warm-holder sandboxes mask the service credential file too."""
    service_json = _xdg_config_home(tmp_path, monkeypatch, with_service=True)
    tool = Sandbox(command=["opencode", "acp"])
    argv = tool.warm_holder_argv(workspace=workspace, inner="true")
    mask_idx = _find_slice(argv, ["--ro-bind", "/dev/null", service_json])
    assert mask_idx < argv.index("--unshare-user")


# ---------------------------------------------------------------------------
# Shared-worktree agent mode
# ---------------------------------------------------------------------------


def _make_pkg_repo(tmp_path):
    """Create <tmp_path>/pkg on branch tickets/X with one commit."""
    repo_dir = tmp_path / "pkg"
    repo_dir.mkdir()
    repo = git.Repo.init(repo_dir)
    repo.config_writer().set_value("user", "name", "test").release()
    repo.config_writer().set_value("user", "email", "test@test").release()
    (repo_dir / "file1.txt").write_text("file1\n")
    repo.git.add("file1.txt")
    repo.git.commit("-m", "base")
    repo.git.checkout("-b", "tickets/X")
    return repo_dir, repo


@pytest.fixture
def shared_workspace(tmp_path):
    """Create a shared-worktree workspace (no .agent package worktrees)."""
    repo_dir, _ = _make_pkg_repo(tmp_path)
    (tmp_path / "ups").mkdir()
    (tmp_path / "ups" / "x.table").write_text("envSet(TAB_TITLE, X)\n")
    return Workspace(
        ticket="X",
        directory=str(tmp_path),
        metapackage_name="m",
        metapackage_tag="t",
        packages={"pkg": "tickets/X"},
        externals={},
        workspace_eups_product="x",
        tools=("sandbox",),
        shared_worktree=True,
    )


def test_shared_write_skips_agent_worktrees(shared_workspace):
    """Shared mode writes no .agent directory (no worktrees, no ups copy)."""
    sandbox = Sandbox(command=[])
    sandbox.write("X", shared_workspace.directory, ["pkg"], shared_workspace, None)
    assert not os.path.exists(os.path.join(shared_workspace.directory, ".agent"))
    # No -agent branch was created.
    repo = git.Repo(os.path.join(shared_workspace.directory, "pkg"))
    assert "tickets/X-agent" not in [b.name for b in repo.heads]
    agents_md = Path(shared_workspace.directory, "AGENTS.md").read_text()
    assert "<!-- BEGIN" not in agents_md
    assert "work side by side" in agents_md


def test_worktree_write_still_copies_ups(workspace):
    """Default mode still creates the .agent worktree and ups copy."""
    (Path(workspace.directory) / "ups").mkdir()
    (Path(workspace.directory) / "ups" / "x.table").write_text("envSet(TAB_TITLE, X)\n")
    sandbox = Sandbox(command=[])
    sandbox.write("X", workspace.directory, ["pkg"], workspace, None)
    assert os.path.exists(os.path.join(workspace.directory, ".agent", "ups", "x.table"))


def test_inner_script_setup_lines(workspace, tmp_path):
    """Worktree mode setups .agent; shared mode setups the human ups."""
    tool = Sandbox(command=[])
    inner = _inner_of(tool._build_bwrap_argv(workspace, shell=True))
    assert "setup -r .agent" in inner
    shared = Workspace(
        ticket="X",
        directory=str(tmp_path),
        metapackage_name="m",
        metapackage_tag="t",
        packages={"pkg": "tickets/X"},
        externals={},
        workspace_eups_product="x",
        tools=(),
        shared_worktree=True,
    )
    inner = _inner_of(tool._build_bwrap_argv(shared, shell=True))
    assert f"setup -r {tmp_path}" in inner
    assert "setup -r .agent" not in inner


def test_shared_mode_mounts_whole_workspace_readwrite(shared_workspace):
    """Shared mode bind-mounts the entire workspace read-write."""
    docs = os.path.join(shared_workspace.directory, "superpowers-docs")
    os.makedirs(docs)
    mounts = Sandbox(command=[])._workspace_mounts(shared_workspace)
    pairs = list(zip(mounts[::3], mounts[1::3], mounts[2::3]))
    assert ("--bind", shared_workspace.directory, shared_workspace.directory) in pairs
    assert ("--ro-bind", shared_workspace.directory, shared_workspace.directory) not in pairs
    # Everything else in the workspace (packages, superpowers-docs, ups/) is
    # covered by the root bind and needs no bind of its own.
    assert os.path.join(shared_workspace.directory, "pkg") not in mounts
    assert docs not in mounts
    # No .agent in shared mode: not even an rw bind for it.
    assert os.path.join(shared_workspace.directory, ".agent") not in mounts


def test_worktree_mode_mounts_unchanged(workspace):
    """Default mode still ro-binds packages and rw-binds only .git."""
    mounts = Sandbox(command=[])._workspace_mounts(workspace)
    pairs = list(zip(mounts[::3], mounts[1::3], mounts[2::3]))
    pkg = os.path.join(workspace.directory, "pkg")
    assert ("--ro-bind", pkg, pkg) in pairs
    assert ("--bind", os.path.join(pkg, ".git"), os.path.join(pkg, ".git")) in pairs


def test_render_agents_md_block_selection():
    """Verify mode blocks are kept/dropped and markers stripped."""
    template = (
        "header\n"
        "<!-- BEGIN shared -->\nshared text\n<!-- END shared -->\n"
        "<!-- BEGIN worktree -->\nworktree text\n<!-- END worktree -->\n"
        "footer\n"
    )
    shared = render_agents_md(template, shared=True)
    assert "shared text" in shared
    assert "worktree text" not in shared
    worktree = render_agents_md(template, shared=False)
    assert "worktree text" in worktree
    assert "shared text" not in worktree
    for rendered in (shared, worktree):
        assert "BEGIN" not in rendered
        assert "END" not in rendered
        assert "header" in rendered and "footer" in rendered


def test_render_agents_md_no_vc_port_guidance() -> None:
    """Verify rendered AGENTS.md has host-session guidance, no vc_port."""
    from tkt.sandbox import _AGENTS_MD_TEMPLATE, render_agents_md

    with open(_AGENTS_MD_TEMPLATE, encoding="utf-8") as f:
        out = render_agents_md(f.read(), shared=False)
    assert "{{vc_port}}" not in out and "vc_port" not in out
    assert "start-server.sh" in out  # the prohibition sentence references it to forbid it
    assert "brainstorm_server" in out and "plannotator" in out


def test_render_agents_md_mismatched_block_raises():
    """Verify unterminated, mismatched, and nested blocks raise."""
    with pytest.raises(ValueError):
        render_agents_md("<!-- BEGIN shared -->\nx\n", shared=True)
    with pytest.raises(ValueError):
        render_agents_md("<!-- BEGIN shared -->\nx\n<!-- END worktree -->\n", shared=True)
    with pytest.raises(ValueError):
        render_agents_md("<!-- END shared -->\n", shared=True)
    with pytest.raises(ValueError):
        render_agents_md(
            "<!-- BEGIN shared -->\n<!-- BEGIN shared -->\nx\n<!-- END shared -->\n",
            shared=True,
        )


def test_real_template_renders_in_both_modes():
    """Verify the shipped AGENTS.md.in renders cleanly in both modes."""
    with open(os.path.join(os.path.dirname(__file__), "..", "tkt", "AGENTS.md.in"), encoding="utf-8") as f:
        template = f.read()
    shared = render_agents_md(template, shared=True)
    worktree = render_agents_md(template, shared=False)
    for rendered in (shared, worktree):
        assert "<!-- BEGIN" not in rendered
        assert "8081" not in rendered
    assert ".agent/<repo-name>/" in worktree
    assert ".agent/<repo-name>/" not in shared
    # Shared mode has no .agent directory at all: the rendered docs must not
    # send the agent there.
    assert ".agent" not in shared
    assert "work side by side" in shared
    assert "work side by side" not in worktree


def test_shared_worktree_tkt_json_roundtrip(tmp_path):
    """Persist shared_worktree to tkt.json; reads back, absent key is False."""
    ws = Workspace(
        ticket="X",
        directory=str(tmp_path),
        metapackage_name="m",
        metapackage_tag="t",
        packages={"pkg": "tickets/X"},
        externals={},
        workspace_eups_product="x",
        tools=(),
        shared_worktree=True,
    )
    ws._write_description()
    assert Workspace.from_directory(str(tmp_path)).shared_worktree is True
    import json as _json

    data = _json.loads((tmp_path / "tkt.json").read_text())
    del data["shared_worktree"]
    (tmp_path / "tkt.json").write_text(_json.dumps(data))
    assert Workspace.from_directory(str(tmp_path)).shared_worktree is False


def test_rubin_env_shared_worktree_default(tmp_path):
    """local.jsonc can set the tkt new default for shared_worktree."""
    from tkt.rubin import RubinEnvironment

    repos_yaml = tmp_path / "repos.yaml"
    repos_yaml.write_text("{}\n")
    base = {
        "workspace_path": str(tmp_path),
        "repos_yaml": str(repos_yaml),
        "default_tools": [],
    }
    assert RubinEnvironment.from_json_data(dict(base)).default_shared_worktree() is False
    env = RubinEnvironment.from_json_data({**base, "shared_worktree": True})
    assert env.default_shared_worktree() is True


def test_require_agent_worktrees_guard(workspace, tmp_path):
    """Verify the guard passes normal workspaces and rejects shared ones."""
    from tkt._cli import _require_agent_worktrees

    _require_agent_worktrees(workspace)  # normal mode: no raise
    shared = Workspace(
        ticket="X",
        directory=str(tmp_path),
        metapackage_name="m",
        metapackage_tag="t",
        packages={"pkg": "tickets/X"},
        externals={},
        workspace_eups_product="x",
        tools=(),
        shared_worktree=True,
    )
    with pytest.raises(click.UsageError):
        _require_agent_worktrees(shared)


def test_workspace_mode_detection(tmp_path):
    """Verify .agent or tkt.json in cwd selects workspace mode."""
    from tkt._cli import _workspace_mode

    assert _workspace_mode(str(tmp_path)) is False
    (tmp_path / ".agent").mkdir()
    assert _workspace_mode(str(tmp_path)) is True
    (tmp_path / ".agent").rmdir()
    (tmp_path / "tkt.json").write_text("{}")
    assert _workspace_mode(str(tmp_path)) is True
    (tmp_path / "tkt.json").unlink()
    assert _workspace_mode(str(tmp_path)) is False
