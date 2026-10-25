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
    with open(os.path.join(root, "harnesses", "shared", "AGENTS.md"), "w") as f:
        f.write("# shared\n\nshared rule\n\n")
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
    with open(os.path.join(root, "harnesses", "opencode", "AGENTS.md"), "w") as f:
        f.write("# OpenCode\n\nopencode rule\n")


def test_install_zed_agent_creates_skill_symlinks(tmp_path):
    """Verify skill symlinks are created under the home dir."""
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


def test_install_zed_agent_merges_agents_md(tmp_path):
    """Zed's global AGENTS.md is a generated file: shared then rules."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_zed_agent(repo_root=str(tmp_path / "repo"), home=home)
    dest = os.path.join(home, ".config", "zed", "AGENTS.md")
    assert os.path.isfile(dest)
    assert not os.path.islink(dest)
    with open(dest, encoding="utf-8") as f:
        assert f.read() == "# shared\n\nshared rule\n\n# rules\n"
    assert oct(os.stat(dest).st_mode & 0o777) == "0o644"


def test_install_zed_agent_replaces_agents_md_symlink(tmp_path, caplog):
    """A legacy install's rules.md symlink becomes the generated file."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    zed_cfg = os.path.join(home, ".config", "zed")
    os.makedirs(zed_cfg)
    dest = os.path.join(zed_cfg, "AGENTS.md")
    os.symlink(os.path.join(str(tmp_path / "repo"), "harnesses", "zed", "rules.md"), dest)
    with caplog.at_level(logging.WARNING):
        install_zed_agent(repo_root=str(tmp_path / "repo"), home=home)
    assert os.path.isfile(dest)
    assert not os.path.islink(dest)
    assert any("replacing symlink" in r.getMessage().lower() for r in caplog.records)


def test_install_zed_agent_merges_agents_md_idempotent(tmp_path, caplog):
    """A second run leaves an up-to-date merged file untouched."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_zed_agent(repo_root=str(tmp_path / "repo"), home=home)
    dest = os.path.join(home, ".config", "zed", "AGENTS.md")
    before = os.stat(dest).st_ino
    with caplog.at_level(logging.INFO):
        install_zed_agent(repo_root=str(tmp_path / "repo"), home=home)
    # inode, not mtime: "left untouched" is the behavior under test.
    assert os.stat(dest).st_ino == before
    assert any("up to date" in r.getMessage() for r in caplog.records)


def test_install_zed_agent_replaces_stale_agents_md_copy(tmp_path, caplog):
    """A hand-edited installed copy is replaced, with a warning."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    zed_cfg = os.path.join(home, ".config", "zed")
    os.makedirs(zed_cfg)
    dest = os.path.join(zed_cfg, "AGENTS.md")
    with open(dest, "w", encoding="utf-8") as f:
        f.write("# hand edited\n")
    with caplog.at_level(logging.WARNING):
        install_zed_agent(repo_root=str(tmp_path / "repo"), home=home)
    with open(dest, encoding="utf-8") as f:
        assert f.read() == "# shared\n\nshared rule\n\n# rules\n"
    assert any("stale" in r.getMessage().lower() for r in caplog.records)


def test_install_zed_agent_missing_shared_source_still_writes(tmp_path):
    """One absent source is skipped; dest is written from the present one."""
    repo = str(tmp_path / "repo")
    os.makedirs(os.path.join(repo, "harnesses", "zed"))
    with open(os.path.join(repo, "harnesses", "zed", "rules.md"), "w") as f:
        f.write("# rules\n")
    home = str(tmp_path / "home")
    install_zed_agent(repo_root=repo, home=home)
    dest = os.path.join(home, ".config", "zed", "AGENTS.md")
    # not a symlink: the old code's symlink-to-rules.md would read the same.
    assert os.path.isfile(dest)
    assert not os.path.islink(dest)
    with open(dest, encoding="utf-8") as f:
        assert f.read() == "# rules\n"


def test_install_zed_agent_no_sources_writes_nothing(tmp_path):
    """With no source present at all, dest is never created."""
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    home = str(tmp_path / "home")
    install_zed_agent(repo_root=repo, home=home)
    assert not os.path.lexists(os.path.join(home, ".config", "zed", "AGENTS.md"))


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
    assert not os.path.exists(os.path.join(home, ".config", "zed", "AGENTS.md"))
    assert not os.path.exists(os.path.join(home, ".config"))


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


def test_install_opencode_agent_merges_agents_md(tmp_path):
    """~/.config/opencode/AGENTS.md is shared then the OpenCode extras."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home)
    dest = os.path.join(home, ".config", "opencode", "AGENTS.md")
    assert os.path.isfile(dest)
    assert not os.path.islink(dest)
    with open(dest, encoding="utf-8") as f:
        assert f.read() == "# shared\n\nshared rule\n\n# OpenCode\n\nopencode rule\n"


def test_install_opencode_agent_replaces_broken_agents_md_symlink(tmp_path, caplog):
    """The broken sandbox-agents.md symlink on real hosts becomes the file."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    dst_dir = os.path.join(home, ".config", "opencode")
    os.makedirs(dst_dir)
    dest = os.path.join(dst_dir, "AGENTS.md")
    # Mirrors the real host state: a symlink to a file deleted upstream.
    os.symlink(os.path.join(str(tmp_path / "repo"), "harnesses", "shared", "sandbox-agents.md"), dest)
    assert os.path.islink(dest) and not os.path.exists(dest)
    with caplog.at_level(logging.WARNING):
        install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home)
    assert os.path.isfile(dest)
    assert not os.path.islink(dest)
    with open(dest, encoding="utf-8") as f:
        assert f.read() == "# shared\n\nshared rule\n\n# OpenCode\n\nopencode rule\n"
    assert any("replacing symlink" in r.getMessage().lower() for r in caplog.records)


def _plugin_dst(home: str, name: str) -> str:
    return os.path.join(home, ".config", "opencode", "plugin", name)


def test_install_opencode_agent_removes_legacy_zed_bell_file(tmp_path, caplog):
    """Verify the legacy single-file zed-bell.js (Task-1 artifact) is
    removed from the host plugin dir, so it cannot keep loading.
    """
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    legacy = _plugin_dst(home, "zed-bell.js")
    os.makedirs(os.path.dirname(legacy))
    with open(legacy, "w") as f:
        f.write("// legacy single-file bell\n")
    with caplog.at_level(logging.WARNING):
        install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home)
    assert not os.path.lexists(legacy)
    assert any("legacy" in record.getMessage().lower() for record in caplog.records)


def test_install_opencode_agent_removes_legacy_zed_bell_dir(tmp_path, caplog):
    """Verify the legacy zed-bell directory plugin copy is removed entirely,
    and a symlink named zed-bell is unlinked without touching its target.
    """
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    bell = _plugin_dst(home, "zed-bell")
    os.makedirs(bell)
    with open(os.path.join(bell, "index.js"), "w") as f:
        f.write("// zed bell server half\n")
    with open(os.path.join(bell, "tui.js"), "w") as f:
        f.write("// zed bell tui half\n")
    with caplog.at_level(logging.WARNING):
        install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home)
    assert not os.path.lexists(bell)
    assert any("legacy" in record.getMessage().lower() for record in caplog.records)
    # A symlink named zed-bell pointing at a directory elsewhere: the link
    # is unlinked, the target directory survives.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.symlink(str(elsewhere), bell)
    install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home)
    assert not os.path.lexists(bell)
    assert elsewhere.is_dir()


def test_install_opencode_agent_removes_legacy_wake_plugin(tmp_path, caplog):
    """Verify the wake plugin's host copy (removed feature) is deleted on
    install, so the OpenCode plugin autoload cannot keep loading it.
    """
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    wake = _plugin_dst(home, "plannotator-wake.js")
    os.makedirs(os.path.dirname(wake))
    with open(wake, "w") as f:
        f.write("// wake plugin v1\n")
    with caplog.at_level(logging.WARNING):
        install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home)
    assert not os.path.lexists(wake)
    assert any("legacy" in record.getMessage().lower() for record in caplog.records)
    # A user-made symlink at the destination is a lexists() entry too: gone.
    elsewhere = tmp_path / "elsewhere.js"
    elsewhere.write_text("// somewhere else\n")
    os.symlink(str(elsewhere), wake)
    install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home)
    assert not os.path.lexists(wake)


def test_install_opencode_agent_legacy_dry_run_writes_nothing(tmp_path):
    """Verify dry run removes nothing: the legacy single files and the
    legacy zed-bell directory are all left in place.
    """
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    plugin_dir = os.path.dirname(_plugin_dst(home, "zed-bell.js"))
    os.makedirs(plugin_dir)
    legacy = _plugin_dst(home, "zed-bell.js")
    with open(legacy, "w") as f:
        f.write("// legacy single-file bell\n")
    bell = _plugin_dst(home, "zed-bell")
    os.makedirs(bell)
    with open(os.path.join(bell, "index.js"), "w") as f:
        f.write("// zed bell server half\n")
    wake = _plugin_dst(home, "plannotator-wake.js")
    with open(wake, "w") as f:
        f.write("// wake plugin v1\n")
    install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home, dry_run=True)
    assert os.path.isfile(legacy)
    assert os.path.isdir(bell)
    assert os.path.isfile(wake)


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
