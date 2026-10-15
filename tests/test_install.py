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

import logging
import os

from tkt.install import install_opencode_agent, install_zed_agent

ZED_SKILLS = ("zed-primary-agent", "zed-explorer", "zed-implementer")
SUPERPOWERS_SKILLS = ("sp-one", "sp-two")
SHARED_SKILLS = ("shared-one",)


def _make_repo(root: str) -> None:
    skills = os.path.join(root, "harnesses", "zed", "skills")
    for name in ZED_SKILLS:
        d = os.path.join(skills, name)
        os.makedirs(d)
        with open(os.path.join(d, "SKILL.md"), "w") as f:
            f.write(f"# {name}\n")
    shared = os.path.join(root, "harnesses", "shared", "skills")
    for name in SHARED_SKILLS:
        d = os.path.join(shared, name)
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
    plugin_dir = os.path.join(root, "harnesses", "opencode", "plugin")
    os.makedirs(plugin_dir, exist_ok=True)
    with open(os.path.join(plugin_dir, "plannotator-wake.js"), "w") as f:
        f.write("// wake plugin v1\n")


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


def _wake_dst(home: str) -> str:
    return os.path.join(home, ".config", "opencode", "plugin", "plannotator-wake.js")


def test_install_opencode_agent_copies_wake_plugin(tmp_path):
    """Verify the wake plugin is copied (real file) into the config dir."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home)
    dst = _wake_dst(home)
    assert os.path.isfile(dst)
    assert not os.path.islink(dst)
    with open(dst) as f:
        assert f.read() == "// wake plugin v1\n"


def test_install_opencode_agent_wake_plugin_idempotent(tmp_path, caplog):
    """Verify a second run leaves an identical copy untouched and says so."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home)
    dst = _wake_dst(home)
    before = os.stat(dst).st_mtime_ns
    with caplog.at_level(logging.INFO):
        install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home)
    assert os.stat(dst).st_mtime_ns == before
    assert any("up to date" in record.getMessage() for record in caplog.records)


def test_install_opencode_agent_wake_plugin_replaces_stale(tmp_path, caplog):
    """Verify a differing (stale) copy is replaced with the repo source."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    os.makedirs(os.path.dirname(_wake_dst(home)))
    with open(_wake_dst(home), "w") as f:
        f.write("// stale old version\n")
    with caplog.at_level(logging.WARNING):
        install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home)
    with open(_wake_dst(home)) as f:
        assert f.read() == "// wake plugin v1\n"
    assert any("stale" in record.getMessage().lower() for record in caplog.records)


def test_install_opencode_agent_wake_plugin_replaces_symlink(tmp_path, caplog):
    """Verify a user-made symlink is replaced by a real file (the loader
    skips symlinks).
    """
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    os.makedirs(os.path.dirname(_wake_dst(home)))
    elsewhere = str(tmp_path / "elsewhere.js")
    with open(elsewhere, "w") as f:
        f.write("// somewhere else\n")
    os.symlink(elsewhere, _wake_dst(home))
    with caplog.at_level(logging.WARNING):
        install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home)
    dst = _wake_dst(home)
    assert os.path.isfile(dst)
    assert not os.path.islink(dst)
    with open(dst) as f:
        assert f.read() == "// wake plugin v1\n"
    assert any("symlink" in record.getMessage().lower() for record in caplog.records)


def test_install_opencode_agent_wake_plugin_dry_run_writes_nothing(tmp_path):
    """Verify dry run does not create the plugin dir or copy the file."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home, dry_run=True)
    assert not os.path.lexists(_wake_dst(home))
    assert not os.path.exists(os.path.join(home, ".config", "opencode", "plugin"))


def test_install_zed_agent_links_shared_skills(tmp_path):
    """Verify shared skills are linked from harnesses/shared/skills."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_zed_agent(repo_root=str(tmp_path / "repo"), home=home)
    for name in SHARED_SKILLS:
        link = os.path.join(home, ".agents", "skills", name)
        assert os.path.islink(link), link
        assert os.readlink(link) == os.path.join(
            str(tmp_path / "repo"),
            "harnesses",
            "shared",
            "skills",
            name,
        )


def test_install_zed_agent_shared_collision_prefers_earlier_source(tmp_path, caplog):
    """A name in two sources links from the earlier source; later is
    skipped.
    """
    _make_repo(str(tmp_path / "repo"))
    dup = os.path.join(str(tmp_path / "repo"), "harnesses", "shared", "skills", "zed-explorer")
    os.makedirs(dup)
    with open(os.path.join(dup, "SKILL.md"), "w") as f:
        f.write("# dup\n")
    home = str(tmp_path / "home")
    with caplog.at_level(logging.WARNING):
        install_zed_agent(repo_root=str(tmp_path / "repo"), home=home)
    link = os.path.join(home, ".agents", "skills", "zed-explorer")
    assert os.readlink(link) == os.path.join(
        str(tmp_path / "repo"), "harnesses", "zed", "skills", "zed-explorer"
    )
    assert any("zed-explorer" in record.getMessage() for record in caplog.records)


def test_install_zed_agent_shared_beats_superpowers(tmp_path, caplog):
    """A name in both shared and superpowers links from shared; later is
    skipped with a warning.
    """
    _make_repo(str(tmp_path / "repo"))
    dup = os.path.join(str(tmp_path / "repo"), "harnesses", "shared", "skills", "sp-one")
    os.makedirs(dup)
    with open(os.path.join(dup, "SKILL.md"), "w") as f:
        f.write("# dup\n")
    home = str(tmp_path / "home")
    with caplog.at_level(logging.WARNING):
        install_zed_agent(repo_root=str(tmp_path / "repo"), home=home)
    link = os.path.join(home, ".agents", "skills", "sp-one")
    assert os.readlink(link) == os.path.join(
        str(tmp_path / "repo"), "harnesses", "shared", "skills", "sp-one"
    )
    assert any("sp-one" in record.getMessage() for record in caplog.records)
