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

import os
import shutil
from pathlib import Path

import git
import pytest

from tkt._workspace import Workspace
from tkt.sandbox import Sandbox


def _inner_of(argv):
    """Return the inner bash script from a built bwrap argv."""
    return argv[argv.index("--") + 3]


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
    """Restricted mode isolates net and bridges the port."""
    net_dir = tmp_path / "net"
    net_dir.mkdir()
    argv = sandbox._build_bwrap_argv(workspace, shell=False, network=False, net_dir=str(net_dir), port=8080)
    assert "--unshare-net" in argv
    assert ("--bind", str(net_dir), str(net_dir)) in [tuple(argv[i : i + 3]) for i in range(len(argv) - 2)]
    inner = _inner_of(argv)
    assert "socat TCP4-LISTEN:8080,fork,reuseaddr UNIX-CONNECT:" in inner
    assert f"{net_dir}/llm.sock" in inner


def test_full_network_argv_shares_host_network(workspace, sandbox):
    """Full-network mode omits --unshare-net and any bridge socket mount."""
    argv = sandbox._build_bwrap_argv(workspace, shell=False, network=True)
    assert "--unshare-net" not in argv
    assert "llm.sock" not in argv
    assert "socat TCP4-LISTEN" not in _inner_of(argv)


def test_single_repo_network_modes(tmp_path, sandbox):
    """Single-repo restricted vs. full differ like the workspace modes."""
    net_dir = tmp_path / "net"
    net_dir.mkdir()
    restricted = sandbox._build_single_repo_argv(
        str(tmp_path), shell=False, network=False, net_dir=str(net_dir), port=8080
    )
    assert "--unshare-net" in restricted
    assert ("--bind", str(net_dir), str(net_dir)) in [
        tuple(restricted[i : i + 3]) for i in range(len(restricted) - 2)
    ]
    full = sandbox._build_single_repo_argv(str(tmp_path), shell=False, network=True)
    assert "--unshare-net" not in full
    assert "llm.sock" not in full
    assert "socat TCP4-LISTEN" not in _inner_of(full)


def test_bridge_port_default_and_override(workspace, tmp_path):
    """The bridge port defaults to 8080 and can be overridden via config."""
    net_dir = tmp_path / "net"
    net_dir.mkdir()

    default = Sandbox(command=[])
    inner = _inner_of(
        default._build_bwrap_argv(workspace, shell=False, network=False, net_dir=str(net_dir), port=8080)
    )
    assert "TCP4-LISTEN:8080" in inner

    custom = Sandbox.from_json_data({"command": [], "port": 9999})
    assert custom._port == 9999
    inner = _inner_of(
        custom._build_bwrap_argv(workspace, shell=False, network=False, net_dir=str(net_dir), port=9999)
    )
    assert "TCP4-LISTEN:9999" in inner


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


@pytest.mark.skipif(shutil.which("socat") is None, reason="socat not installed")
def test_bridge_lifecycle(tmp_path, monkeypatch):
    """The host-side bridge starts a socat and tears it down cleanly."""
    monkeypatch.setenv("HOME", str(tmp_path))  # state dir lands under tmp_path
    sandbox = Sandbox(command=[], port=18089)

    net_dir, proc = sandbox._start_bridge()
    try:
        assert os.path.isdir(net_dir)
        assert os.path.exists(os.path.join(net_dir, "llm.sock"))
        assert proc.poll() is None  # socat is alive
    finally:
        sandbox._stop_bridge(net_dir, proc)

    assert proc.poll() is not None  # socat terminated
    assert not os.path.exists(net_dir)  # socket dir removed
