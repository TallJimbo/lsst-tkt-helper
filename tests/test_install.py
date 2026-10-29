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

from tkt.install import install_harness

ZED_SKILLS = ("zed-primary-agent", "zed-explorer", "zed-implementer")
SUPERPOWERS_SKILLS = ("sp-one", "sp-two")
SHARED_SKILLS = ("shared-one",)
AGENTS = ("sp-build.md", "plan.md")


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
        with open(os.path.join(d, "reference.md"), "w") as f:
            f.write(f"# {name} reference\n")
    os.makedirs(os.path.join(root, "harnesses", "zed"), exist_ok=True)
    with open(os.path.join(root, "harnesses", "zed", "rules.md"), "w") as f:
        f.write("# rules\n")
    agents = os.path.join(root, "harnesses", "opencode", "agents")
    os.makedirs(agents)
    for name in AGENTS:
        with open(os.path.join(agents, name), "w") as f:
            f.write(f"# {name}\n")
    with open(os.path.join(root, "harnesses", "opencode", "AGENTS.md"), "w") as f:
        f.write("# OpenCode\n\nopencode rule\n")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_install_harness_copies_skills(tmp_path):
    """Every skill from all three sources lands in ~/.agents/skills as a
    copy, never a symlink.
    """
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_harness(repo_root=str(tmp_path / "repo"), home=home)
    skills_dst = os.path.join(home, ".agents", "skills")
    for name in (*ZED_SKILLS, *SHARED_SKILLS, *SUPERPOWERS_SKILLS):
        dst = os.path.join(skills_dst, name)
        assert os.path.isdir(dst), dst
        assert not os.path.islink(dst)
        assert _read(os.path.join(dst, "SKILL.md")) == f"# {name}\n"


def test_install_harness_copies_skill_subtree(tmp_path):
    """Files other than SKILL.md come along with the skill directory."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_harness(repo_root=str(tmp_path / "repo"), home=home)
    dst = os.path.join(home, ".agents", "skills", "sp-one")
    assert _read(os.path.join(dst, "reference.md")) == "# sp-one reference\n"


def test_install_harness_replaces_skill_symlinks(tmp_path, caplog):
    """A previous install's skill symlinks become copies."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    skills_dst = os.path.join(home, ".agents", "skills")
    os.makedirs(skills_dst)
    link = os.path.join(skills_dst, "zed-explorer")
    os.symlink(os.path.join(str(tmp_path / "repo"), "harnesses", "zed", "skills", "zed-explorer"), link)
    with caplog.at_level(logging.WARNING):
        install_harness(repo_root=str(tmp_path / "repo"), home=home)
    assert os.path.isdir(link)
    assert not os.path.islink(link)
    assert _read(os.path.join(link, "SKILL.md")) == "# zed-explorer\n"
    assert any("replacing symlink" in r.getMessage().lower() for r in caplog.records)


def test_install_harness_is_idempotent(tmp_path, caplog):
    """A second run over up-to-date copies changes nothing and flags
    nothing.
    """
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_harness(repo_root=str(tmp_path / "repo"), home=home)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        install_harness(repo_root=str(tmp_path / "repo"), home=home)
    assert not caplog.records


def test_install_harness_updates_changed_skill(tmp_path):
    """A source edit lands in the installed copy on the next run."""
    repo = str(tmp_path / "repo")
    _make_repo(repo)
    home = str(tmp_path / "home")
    install_harness(repo_root=repo, home=home)
    with open(os.path.join(repo, "harnesses", "zed", "skills", "zed-explorer", "SKILL.md"), "w") as f:
        f.write("# zed-explorer v2\n")
    install_harness(repo_root=repo, home=home)
    dst = os.path.join(home, ".agents", "skills", "zed-explorer", "SKILL.md")
    assert _read(dst) == "# zed-explorer v2\n"


def test_install_harness_collision_prefers_earlier_source(tmp_path, caplog):
    """A name in two sources installs from the earlier source; later is
    skipped with a warning.
    """
    repo = str(tmp_path / "repo")
    _make_repo(repo)
    dup = os.path.join(repo, "harnesses", "shared", "skills", "zed-explorer")
    os.makedirs(dup)
    with open(os.path.join(dup, "SKILL.md"), "w") as f:
        f.write("# dup\n")
    home = str(tmp_path / "home")
    with caplog.at_level(logging.WARNING):
        install_harness(repo_root=repo, home=home)
    dst = os.path.join(home, ".agents", "skills", "zed-explorer", "SKILL.md")
    assert _read(dst) == "# zed-explorer\n"
    assert any("zed-explorer" in r.getMessage() and "shadowed" in r.getMessage() for r in caplog.records)


def test_install_harness_shared_beats_superpowers(tmp_path, caplog):
    """A name in both shared and superpowers installs from shared."""
    repo = str(tmp_path / "repo")
    _make_repo(repo)
    dup = os.path.join(repo, "harnesses", "shared", "skills", "sp-one")
    os.makedirs(dup)
    with open(os.path.join(dup, "SKILL.md"), "w") as f:
        f.write("# dup\n")
    home = str(tmp_path / "home")
    with caplog.at_level(logging.WARNING):
        install_harness(repo_root=repo, home=home)
    dst = os.path.join(home, ".agents", "skills", "sp-one", "SKILL.md")
    assert _read(dst) == "# dup\n"
    assert any("sp-one" in r.getMessage() for r in caplog.records)


def test_install_harness_skips_missing_superpowers(tmp_path):
    """Install succeeds when superpowers/skills is absent (no error)."""
    repo = str(tmp_path / "repo")
    os.makedirs(os.path.join(repo, "harnesses", "zed", "skills", "zed-a"))
    with open(os.path.join(repo, "harnesses", "zed", "skills", "zed-a", "SKILL.md"), "w") as f:
        f.write("# zed-a\n")
    with open(os.path.join(repo, "harnesses", "zed", "rules.md"), "w") as f:
        f.write("# rules\n")
    home = str(tmp_path / "home")
    install_harness(repo_root=repo, home=home)
    skills_dst = os.path.join(home, ".agents", "skills")
    assert os.path.isdir(os.path.join(skills_dst, "zed-a"))
    assert not os.path.exists(os.path.join(skills_dst, "sp-one"))


def test_install_harness_copies_agents_dir(tmp_path):
    """Agent markdown files land in a real ~/.config/opencode/agents."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_harness(repo_root=str(tmp_path / "repo"), home=home)
    dst = os.path.join(home, ".config", "opencode", "agents")
    assert os.path.isdir(dst)
    assert not os.path.islink(dst)
    for name in AGENTS:
        assert _read(os.path.join(dst, name)) == f"# {name}\n"


def test_install_harness_replaces_agents_dir_symlink(tmp_path, caplog):
    """A previous install's agents symlink becomes a real directory, so
    OpenCode's live reload can watch it.
    """
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    dst_dir = os.path.join(home, ".config", "opencode")
    os.makedirs(dst_dir)
    dst = os.path.join(dst_dir, "agents")
    os.symlink(os.path.join(str(tmp_path / "repo"), "harnesses", "opencode", "agents"), dst)
    with caplog.at_level(logging.WARNING):
        install_harness(repo_root=str(tmp_path / "repo"), home=home)
    assert os.path.isdir(dst)
    assert not os.path.islink(dst)
    assert _read(os.path.join(dst, "sp-build.md")) == "# sp-build.md\n"
    assert any("replacing symlink" in r.getMessage().lower() for r in caplog.records)


def test_install_harness_removes_stale_skill_when_confirmed(tmp_path):
    """A leftover skill (renamed upstream) is removed when confirm allows
    it.
    """
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    skills_dst = os.path.join(home, ".agents", "skills")
    stale = os.path.join(skills_dst, "zed-old-name")
    os.makedirs(stale)
    with open(os.path.join(stale, "SKILL.md"), "w") as f:
        f.write("# old\n")
    stale_link = os.path.join(skills_dst, "sp-gone")
    os.symlink("/somewhere/gone", stale_link)
    install_harness(repo_root=str(tmp_path / "repo"), home=home, confirm=lambda m: True)
    assert not os.path.lexists(stale)
    assert not os.path.lexists(stale_link)
    assert os.path.isdir(os.path.join(skills_dst, "zed-explorer"))


def test_install_harness_keeps_stale_when_not_confirmed(tmp_path):
    """Declining the prompt leaves the stale skill in place."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    skills_dst = os.path.join(home, ".agents", "skills")
    stale = os.path.join(skills_dst, "zed-old-name")
    os.makedirs(stale)
    with open(os.path.join(stale, "SKILL.md"), "w") as f:
        f.write("# old\n")
    install_harness(repo_root=str(tmp_path / "repo"), home=home, confirm=lambda m: False)
    assert os.path.isdir(stale)


def test_install_harness_never_touches_foreign_entries(tmp_path, caplog):
    """User content that does not look tkt-installed is left alone without
    even a prompt.
    """
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    skills_dst = os.path.join(home, ".agents", "skills")
    foreign = os.path.join(skills_dst, "my-notes")
    os.makedirs(foreign)
    with open(os.path.join(foreign, "notes.txt"), "w") as f:
        f.write("mine\n")

    def confirm(msg: str) -> bool:
        raise AssertionError(f"unexpected prompt: {msg}")

    install_harness(repo_root=str(tmp_path / "repo"), home=home, confirm=confirm)
    assert _read(os.path.join(foreign, "notes.txt")) == "mine\n"


def test_install_harness_removes_stale_agent_when_confirmed(tmp_path):
    """A deleted- upstream agent file is removed from the agents dir."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    agents_dst = os.path.join(home, ".config", "opencode", "agents")
    os.makedirs(agents_dst)
    stale = os.path.join(agents_dst, "sp-retired.md")
    with open(stale, "w") as f:
        f.write("# retired\n")
    install_harness(repo_root=str(tmp_path / "repo"), home=home, confirm=lambda m: True)
    assert not os.path.lexists(stale)
    assert os.path.isfile(os.path.join(agents_dst, "sp-build.md"))


def test_install_harness_zed_agents_md(tmp_path):
    """Zed's global AGENTS.md is a generated file: shared then rules."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_harness(repo_root=str(tmp_path / "repo"), home=home)
    dest = os.path.join(home, ".config", "zed", "AGENTS.md")
    assert os.path.isfile(dest)
    assert not os.path.islink(dest)
    assert _read(dest) == "# shared\n\nshared rule\n\n# rules\n"
    assert oct(os.stat(dest).st_mode & 0o777) == "0o644"


def test_install_harness_opencode_agents_md(tmp_path):
    """~/.config/opencode/AGENTS.md is shared then the OpenCode extras."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_harness(repo_root=str(tmp_path / "repo"), home=home)
    dest = os.path.join(home, ".config", "opencode", "AGENTS.md")
    assert os.path.isfile(dest)
    assert not os.path.islink(dest)
    assert _read(dest) == "# shared\n\nshared rule\n\n# OpenCode\n\nopencode rule\n"


def test_install_harness_replaces_agents_md_symlink(tmp_path, caplog):
    """A legacy install's rules.md symlink becomes the generated file."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    zed_cfg = os.path.join(home, ".config", "zed")
    os.makedirs(zed_cfg)
    dest = os.path.join(zed_cfg, "AGENTS.md")
    os.symlink(os.path.join(str(tmp_path / "repo"), "harnesses", "zed", "rules.md"), dest)
    with caplog.at_level(logging.WARNING):
        install_harness(repo_root=str(tmp_path / "repo"), home=home)
    assert os.path.isfile(dest)
    assert not os.path.islink(dest)
    assert any("replacing symlink" in r.getMessage().lower() for r in caplog.records)


def test_install_harness_agents_md_idempotent(tmp_path, caplog):
    """A second run leaves an up-to-date merged file untouched."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_harness(repo_root=str(tmp_path / "repo"), home=home)
    dest = os.path.join(home, ".config", "zed", "AGENTS.md")
    before = os.stat(dest).st_ino
    with caplog.at_level(logging.INFO):
        install_harness(repo_root=str(tmp_path / "repo"), home=home)
    # inode, not mtime: "left untouched" is the behavior under test.
    assert os.stat(dest).st_ino == before
    assert any("up to date" in r.getMessage() for r in caplog.records)


def test_install_harness_replaces_stale_agents_md_copy(tmp_path, caplog):
    """A hand-edited installed copy is replaced, with a warning."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    zed_cfg = os.path.join(home, ".config", "zed")
    os.makedirs(zed_cfg)
    dest = os.path.join(zed_cfg, "AGENTS.md")
    with open(dest, "w", encoding="utf-8") as f:
        f.write("# hand edited\n")
    with caplog.at_level(logging.WARNING):
        install_harness(repo_root=str(tmp_path / "repo"), home=home)
    assert _read(dest) == "# shared\n\nshared rule\n\n# rules\n"
    assert any("stale" in r.getMessage().lower() for r in caplog.records)


def test_install_harness_missing_shared_source_still_writes(tmp_path):
    """One absent AGENTS.md source is skipped; dest is written from the
    present one.
    """
    repo = str(tmp_path / "repo")
    os.makedirs(os.path.join(repo, "harnesses", "zed"))
    with open(os.path.join(repo, "harnesses", "zed", "rules.md"), "w") as f:
        f.write("# rules\n")
    home = str(tmp_path / "home")
    install_harness(repo_root=repo, home=home)
    dest = os.path.join(home, ".config", "zed", "AGENTS.md")
    assert _read(dest) == "# rules\n"


def test_install_harness_no_sources_writes_no_agents_md(tmp_path):
    """With no AGENTS.md source present at all, dest is never created."""
    repo = str(tmp_path / "repo")
    os.makedirs(repo)
    home = str(tmp_path / "home")
    install_harness(repo_root=repo, home=home)
    assert not os.path.lexists(os.path.join(home, ".config", "zed", "AGENTS.md"))
    assert not os.path.lexists(os.path.join(home, ".config", "opencode", "AGENTS.md"))


def test_install_harness_dry_run_writes_nothing(tmp_path):
    """Verify dry run does not create or remove anything."""
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    install_harness(repo_root=str(tmp_path / "repo"), home=home, dry_run=True)
    assert not os.path.exists(os.path.join(home, ".agents"))
    assert not os.path.exists(os.path.join(home, ".config", "zed", "AGENTS.md"))
    assert not os.path.exists(os.path.join(home, ".config", "opencode", "agents"))


def _plugin_dst(home: str, name: str) -> str:
    return os.path.join(home, ".config", "opencode", "plugin", name)


def test_install_harness_removes_legacy_zed_bell_file(tmp_path, caplog):
    """Verify the legacy single-file zed-bell.js is removed from the host
    plugin dir, so it cannot keep loading.
    """
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    legacy = _plugin_dst(home, "zed-bell.js")
    os.makedirs(os.path.dirname(legacy))
    with open(legacy, "w") as f:
        f.write("// legacy single-file bell\n")
    with caplog.at_level(logging.WARNING):
        install_harness(repo_root=str(tmp_path / "repo"), home=home)
    assert not os.path.lexists(legacy)
    assert any("legacy" in record.getMessage().lower() for record in caplog.records)


def test_install_harness_removes_legacy_zed_bell_dir(tmp_path, caplog):
    """Verify the legacy zed-bell directory plugin copy is removed entirely,
    and a symlink named zed-bell is unlinked without touching its target.
    """
    _make_repo(str(tmp_path / "repo"))
    home = str(tmp_path / "home")
    bell = _plugin_dst(home, "zed-bell")
    os.makedirs(bell)
    with open(os.path.join(bell, "index.js"), "w") as f:
        f.write("// zed bell server half\n")
    with caplog.at_level(logging.WARNING):
        install_harness(repo_root=str(tmp_path / "repo"), home=home)
    assert not os.path.lexists(bell)
    assert any("legacy" in record.getMessage().lower() for record in caplog.records)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.symlink(str(elsewhere), bell)
    install_harness(repo_root=str(tmp_path / "repo"), home=home)
    assert not os.path.lexists(bell)
    assert elsewhere.is_dir()


def test_install_harness_removes_legacy_wake_plugin(tmp_path, caplog):
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
        install_harness(repo_root=str(tmp_path / "repo"), home=home)
    assert not os.path.lexists(wake)
    assert any("legacy" in record.getMessage().lower() for record in caplog.records)


def test_install_harness_legacy_dry_run_writes_nothing(tmp_path):
    """Verify dry run removes nothing: legacy plugin copies stay in place."""
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
    install_harness(repo_root=str(tmp_path / "repo"), home=home, dry_run=True)
    assert os.path.isfile(legacy)
    assert os.path.isdir(bell)
