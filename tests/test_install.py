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

import os

from tkt.install import install_opencode_agent, install_zed_agent

ZED_SKILLS = ("zed-primary-agent", "zed-explorer", "zed-implementer", "zed-reviewer")
SUPERPOWERS_SKILLS = ("sp-one", "sp-two")


def _make_repo(root: str) -> None:
    skills = os.path.join(root, "harnesses", "zed", "skills")
    for name in ZED_SKILLS:
        d = os.path.join(skills, name)
        os.makedirs(d)
        with open(os.path.join(d, "SKILL.md"), "w") as f:
            f.write(f"# {name}\n")
    superpowers = os.path.join(root, "superpowers", "skills")
    for name in SUPERPOWERS_SKILLS:
        d = os.path.join(superpowers, name)
        os.makedirs(d)
        with open(os.path.join(d, "SKILL.md"), "w") as f:
            f.write(f"# {name}\n")
    os.makedirs(os.path.join(root, "harnesses", "zed"), exist_ok=True)
    with open(os.path.join(root, "harnesses", "zed", "rules.md"), "w") as f:
        f.write("# rules\n")
    os.makedirs(os.path.join(root, "harnesses", "opencode", "agents"))
    with open(os.path.join(root, "harnesses", "opencode", "agents", "sp-build.md"), "w") as f:
        f.write("# sp-build\n")


def test_install_zed_agent_creates_symlinks(tmp_path):
    """Verify skill and rules symlinks are created under the home dir."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_zed_agent(repo_root=str(tmp_path / "repo"), home=home)
    for name in ZED_SKILLS:
        link = os.path.join(home, ".agents", "skills", name)
        assert os.path.islink(link), link
        assert os.readlink(link) == os.path.join(str(tmp_path / "repo"), "harnesses", "zed", "skills", name)
    for name in SUPERPOWERS_SKILLS:
        link = os.path.join(home, ".agents", "skills", name)
        assert os.path.islink(link), link
        assert os.readlink(link) == os.path.join(str(tmp_path / "repo"), "superpowers", "skills", name)
    rules = os.path.join(home, ".config", "zed", "AGENTS.md")
    assert os.path.islink(rules)
    assert os.readlink(rules) == os.path.join(str(tmp_path / "repo"), "harnesses", "zed", "rules.md")


def test_install_zed_agent_skips_missing_superpowers(tmp_path):
    """Verify install succeeds when superpowers/skills is absent (no error)."""
    repo = str(tmp_path / "repo")
    zed_skills = os.path.join(repo, "harnesses", "zed", "skills")
    os.makedirs(os.path.join(zed_skills, "zed-a"))
    with open(os.path.join(zed_skills, "zed-a", "SKILL.md"), "w") as f:
        f.write("# zed-a\n")
    os.makedirs(os.path.join(repo, "harnesses", "zed"), exist_ok=True)
    with open(os.path.join(repo, "harnesses", "zed", "rules.md"), "w") as f:
        f.write("# rules\n")
    home = str(tmp_path / "home")
    install_zed_agent(repo_root=repo, home=home)
    skills_dst = os.path.join(home, ".agents", "skills")
    assert os.path.islink(os.path.join(skills_dst, "zed-a"))
    assert not os.path.exists(os.path.join(skills_dst, "sp-one"))
    assert not os.path.exists(os.path.join(skills_dst, "sp-two"))


def test_install_zed_agent_dry_run_writes_nothing(tmp_path):
    """Verify dry run does not create any files."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_zed_agent(repo_root=str(tmp_path / "repo"), home=home, dry_run=True)
    assert not os.path.exists(os.path.join(home, ".agents"))


def test_install_zed_agent_removes_stale_symlink_when_confirmed(tmp_path):
    """Verify stale symlink is removed when confirm returns True."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    skills_dst = os.path.join(home, ".agents", "skills")
    os.makedirs(skills_dst)
    stale = os.path.join(skills_dst, "zed-old-name")
    os.symlink("/somewhere/old", stale)
    install_zed_agent(repo_root=str(tmp_path / "repo"), home=home, confirm=lambda m: True)
    assert not os.path.lexists(stale)
    assert os.path.islink(os.path.join(skills_dst, "zed-explorer"))


def test_install_zed_agent_keeps_stale_when_not_confirmed(tmp_path):
    """Verify stale symlink is kept when confirm returns False."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    skills_dst = os.path.join(home, ".agents", "skills")
    os.makedirs(skills_dst)
    stale = os.path.join(skills_dst, "zed-old-name")
    os.symlink("/somewhere/old", stale)
    install_zed_agent(repo_root=str(tmp_path / "repo"), home=home, confirm=lambda m: False)
    assert os.path.islink(stale)


def test_install_zed_agent_keeps_superpowers_skill(tmp_path):
    """Verify superpowers skill symlinks are managed, not flagged as stale."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    skills_dst = os.path.join(home, ".agents", "skills")
    os.makedirs(skills_dst)
    sp_link = os.path.join(skills_dst, "sp-one")
    os.symlink("/somewhere/stale", sp_link)
    install_zed_agent(repo_root=str(tmp_path / "repo"), home=home, confirm=lambda m: True)
    assert os.path.islink(sp_link)
    assert os.readlink(sp_link) == os.path.join(str(tmp_path / "repo"), "superpowers", "skills", "sp-one")


def test_install_zed_agent_idempotent(tmp_path):
    """Verify running install twice leaves correct symlinks."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_zed_agent(repo_root=str(tmp_path / "repo"), home=home)
    install_zed_agent(repo_root=str(tmp_path / "repo"), home=home)
    link = os.path.join(home, ".agents", "skills", "zed-explorer")
    assert os.path.islink(link)
    assert os.readlink(link) == os.path.join(
        str(tmp_path / "repo"), "harnesses", "zed", "skills", "zed-explorer"
    )


def test_install_opencode_agent_repoints_symlink(tmp_path):
    """Verify opencode agents symlink is repointed to the harness dir."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    dst_dir = os.path.join(home, ".config", "opencode")
    os.makedirs(dst_dir)
    dst = os.path.join(dst_dir, "agents")
    os.symlink(os.path.join(str(tmp_path / "repo"), "agents", "opencode"), dst)
    install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home)
    assert os.path.islink(dst)
    assert os.readlink(dst) == os.path.join(str(tmp_path / "repo"), "harnesses", "opencode", "agents")
