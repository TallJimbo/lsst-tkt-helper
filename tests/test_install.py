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

from tkt.install import (
    install_opencode_agent,
    install_zed_agent,
    plugin_dest,
    plugin_source,
    wake_plugin_dest,
    wake_plugin_source,
)

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
    bell_dir = os.path.join(plugin_dir, "zed-bell")
    os.makedirs(bell_dir)
    with open(os.path.join(bell_dir, "index.js"), "w") as f:
        f.write("// zed bell server half v1\n")
    with open(os.path.join(bell_dir, "tui.js"), "w") as f:
        f.write("// zed bell tui half v1\n")


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


def _plugin_dst(home: str, name: str = "plannotator-wake.js") -> str:
    return os.path.join(home, ".config", "opencode", "plugin", name)


def _wake_dst(home: str) -> str:
    return _plugin_dst(home)


def test_install_opencode_agent_copies_plugins(tmp_path):
    """Verify both autoloaded plugins are copied (real files, not symlinks)
    into the config dir: the wake single file and the zed-bell directory
    plugin with both of its entrypoint files byte-exact.
    """
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home)
    dst = _plugin_dst(home, "plannotator-wake.js")
    assert os.path.isfile(dst)
    assert not os.path.islink(dst)
    with open(dst) as f:
        assert f.read() == "// wake plugin v1\n"
    bell = _plugin_dst(home, "zed-bell")
    assert os.path.isdir(bell)
    assert not os.path.islink(bell)
    for name, contents in (
        ("index.js", "// zed bell server half v1\n"),
        ("tui.js", "// zed bell tui half v1\n"),
    ):
        entry = os.path.join(bell, name)
        assert os.path.isfile(entry)
        assert not os.path.islink(entry)
        with open(entry) as f:
            assert f.read() == contents


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


def test_wake_plugin_helpers_still_resolve(tmp_path):
    """Verify wake_plugin_source/dest (imported by sandbox.py) still
    resolve, and match the generic plugin helpers.
    """
    repo = str(tmp_path / "repo")
    home = str(tmp_path / "home")
    assert wake_plugin_source(repo) == plugin_source("plannotator-wake.js", repo)
    assert wake_plugin_dest(home) == plugin_dest("plannotator-wake.js", home)
    assert wake_plugin_source(repo).endswith(
        os.path.join("harnesses", "opencode", "plugin", "plannotator-wake.js")
    )
    assert wake_plugin_dest(home).endswith(
        os.path.join(".config", "opencode", "plugin", "plannotator-wake.js")
    )


def test_install_opencode_agent_plugins_idempotent(tmp_path, caplog):
    """Verify a second run leaves identical copies (file and directory
    plugin) untouched and says so.
    """
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home)
    bell = _plugin_dst(home, "zed-bell")
    files = [
        _plugin_dst(home, "plannotator-wake.js"),
        os.path.join(bell, "index.js"),
        os.path.join(bell, "tui.js"),
    ]
    before = [os.stat(f).st_mtime_ns for f in files]
    with caplog.at_level(logging.INFO):
        install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home)
    assert [os.stat(f).st_mtime_ns for f in files] == before
    for dst in (_plugin_dst(home, "plannotator-wake.js"), bell):
        assert any(
            "up to date" in record.getMessage() and dst in record.getMessage() for record in caplog.records
        )


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


def test_install_opencode_agent_zed_bell_replaces_stale_directory(tmp_path, caplog):
    """Verify a stale installed zed-bell directory (differing bytes plus a
    stray extra file) is replaced so the copy ends byte-equal to the source
    tree.
    """
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    bell = _plugin_dst(home, "zed-bell")
    os.makedirs(bell)
    with open(os.path.join(bell, "index.js"), "w") as f:
        f.write("// stale old server half\n")
    with open(os.path.join(bell, "tui.js"), "w") as f:
        f.write("// zed bell tui half v1\n")
    with open(os.path.join(bell, "stray.js"), "w") as f:
        f.write("// not part of the plugin\n")
    with caplog.at_level(logging.WARNING):
        install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home)
    assert sorted(os.listdir(bell)) == ["index.js", "tui.js"]
    with open(os.path.join(bell, "index.js")) as f:
        assert f.read() == "// zed bell server half v1\n"
    with open(os.path.join(bell, "tui.js")) as f:
        assert f.read() == "// zed bell tui half v1\n"
    assert not os.path.lexists(os.path.join(bell, "stray.js"))
    assert any("stale" in record.getMessage().lower() for record in caplog.records)


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
    # The directory plugin itself still installs.
    assert os.path.isfile(os.path.join(_plugin_dst(home, "zed-bell"), "tui.js"))


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


def test_install_opencode_agent_plugins_dry_run_writes_nothing(tmp_path):
    """Verify dry run does not create the plugin dir, copy the plugins, or
    remove a legacy single file.
    """
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    legacy = _plugin_dst(home, "zed-bell.js")
    os.makedirs(os.path.dirname(legacy))
    with open(legacy, "w") as f:
        f.write("// legacy single-file bell\n")
    install_opencode_agent(repo_root=str(tmp_path / "repo"), home=home, dry_run=True)
    assert not os.path.lexists(_plugin_dst(home, "plannotator-wake.js"))
    assert not os.path.lexists(_plugin_dst(home, "zed-bell"))
    assert os.path.isfile(legacy)


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
