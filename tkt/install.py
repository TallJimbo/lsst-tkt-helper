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

import logging
import os
import shutil
import tempfile
from collections.abc import Callable

__all__ = ("install_harness",)

# tkt-owned plugin artifacts from older installs that live in the host plugin
# dir but must be *removed* on install (their feature was removed from tkt;
# a stale copy would otherwise keep loading).  A name may be a single file or
# a directory plugin (e.g. ``zed-bell``).
LEGACY_PLUGIN_NAMES: tuple[str, ...] = ("zed-bell.js", "zed-bell", "plannotator-wake.js")


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def plugin_dest(name: str, home: str | None = None) -> str:
    """Host path of the plugin artifact ``name`` under
    ``~/.config/opencode/plugin/``.

    Used to purge legacy copies: the installed OpenCode's plugin loader
    autoloads real files and directories here (it skips symlinks), so a
    stale copy keeps loading until removed.
    """
    return os.path.join(home or os.path.expanduser("~"), ".config", "opencode", "plugin", name)


def _clear_dest(path: str, *, dry_run: bool) -> None:
    """Remove ``path`` (symlink, directory, or file) so a fresh copy can
    take its place.

    A pre-existing plain directory is the normal update path and passes
    silently; symlinks and non-directories are unexpected and warned about.
    """
    if os.path.islink(path):
        logging.warning(f"Replacing symlink {path} -> {os.readlink(path)}")
    elif os.path.lexists(path) and not os.path.isdir(path):
        logging.warning(f"Replacing non-directory {path}")
    if not os.path.lexists(path) or dry_run:
        return
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def _copy_skill(src: str, dst: str, *, dry_run: bool) -> None:
    """Copy the skill directory ``src`` over ``dst``, replacing any
    existing symlink, directory, or file at ``dst``.
    """
    _clear_dest(dst, dry_run=dry_run)
    logging.info(f"{'Would copy' if dry_run else 'Copying'} {src} -> {dst}")
    if not dry_run:
        shutil.copytree(src, dst)


def _copy_agent_file(src: str, dst: str, *, dry_run: bool) -> None:
    """Copy the agent markdown file ``src`` over ``dst``."""
    if os.path.lexists(dst) and not os.path.isfile(dst):
        logging.warning(f"Refusing to replace non-file {dst}")
        return
    logging.info(f"{'Would copy' if dry_run else 'Copying'} {src} -> {dst}")
    if not dry_run:
        shutil.copy2(src, dst)


def _clean_stale(
    directory: str,
    keep: set[str],
    *,
    candidate: Callable[[str], bool],
    dry_run: bool,
    confirm: Callable[[str], bool],
) -> None:
    """Warn about and remove tkt-managed entries in ``directory`` that this
    run did not install.

    ``candidate`` decides which unmanaged entries look like tkt-installed
    content (as opposed to the user's own files, which are left alone
    entirely).
    """
    if not os.path.isdir(directory):
        return
    for name in sorted(os.listdir(directory)):
        if name in keep:
            continue
        path = os.path.join(directory, name)
        if not candidate(path):
            continue
        logging.warning(f"Stale entry not installed by this tkt: {path}")
        if not dry_run and confirm(f"Remove stale entry {path}?"):
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.remove(path)


# Skill source roots installed into ~/.agents/skills, in precedence order:
# an earlier source wins on a name collision (warned about).  Both Zed and
# OpenCode discover ~/.agents/skills, so one install serves both harnesses;
# Zed-only skills are hidden from OpenCode by a ``zed-*`` skill permission
# deny in the user's OpenCode config.
_SKILL_SOURCES: tuple[tuple[str, ...], ...] = (
    ("harnesses", "zed", "skills"),
    ("harnesses", "shared", "skills"),
    ("superpowers", "skills"),
)


def _install_skill_dirs(skills_src: str, skills_dst: str, managed: set[str], *, dry_run: bool) -> None:
    """Copy each skill dir (containing ``SKILL.md``) under ``skills_src``
    into ``skills_dst``.

    Names already in ``managed`` (copied from an earlier source) are skipped
    with a warning. ``managed`` is updated in place.
    """
    if not os.path.isdir(skills_src):
        return
    for name in sorted(os.listdir(skills_src)):
        src = os.path.join(skills_src, name)
        if not os.path.isdir(src) or not os.path.isfile(os.path.join(src, "SKILL.md")):
            continue
        if name in managed:
            logging.warning(f"Skill {name} in {skills_src} is shadowed by an earlier source; skipping")
            continue
        managed.add(name)
        _copy_skill(src, os.path.join(skills_dst, name), dry_run=dry_run)


def _install_agents(agents_src: str, agents_dst: str, *, dry_run: bool) -> set[str]:
    """Copy ``*.md`` agent files from ``agents_src`` into the real
    directory ``agents_dst`` (replacing a symlink at that path).

    OpenCode's live config reload watches this directory, and symlinked
    roots are not watched, so the install is a copy, not a link.
    Returns the set of installed file names.
    """
    installed: set[str] = set()
    if os.path.islink(agents_dst):
        logging.warning(f"Replacing symlink {agents_dst} -> {os.readlink(agents_dst)}")
        if not dry_run:
            os.remove(agents_dst)
    elif os.path.lexists(agents_dst) and not os.path.isdir(agents_dst):
        logging.warning(f"Refusing to replace non-directory {agents_dst}")
        return installed
    if not dry_run:
        os.makedirs(agents_dst, exist_ok=True)
    if not os.path.isdir(agents_src):
        logging.info(f"No agents source at {agents_src}; leaving {agents_dst} alone")
        return installed
    for name in sorted(os.listdir(agents_src)):
        if not name.endswith(".md"):
            continue
        _copy_agent_file(os.path.join(agents_src, name), os.path.join(agents_dst, name), dry_run=dry_run)
        installed.add(name)
    return installed


def _install_merged_agents_md(sources: list[str], dest: str, *, dry_run: bool) -> None:
    """Concatenate the existing ``sources`` (in order) into ``dest``.

    Sources are stripped of surrounding whitespace, joined by one blank line,
    and terminated by exactly one newline.  A symlink at ``dest`` (correct,
    stale, or broken) is replaced; a real file with identical bytes is left
    untouched; a real file whose bytes differ is replaced with a warning; a
    non-file (e.g. a directory) is refused.  Writes nothing when no source
    exists.  The write is atomic (mkstemp in the dest dir + os.replace).
    """
    sections: list[str] = []
    for src in sources:
        if not os.path.isfile(src):
            logging.info(f"Skipping missing agents source {src}")
            continue
        with open(src, encoding="utf-8") as f:
            sections.append(f.read().strip())
    if not sections:
        logging.info(f"No agents sources present; leaving {dest} alone")
        return
    data = ("\n\n".join(sections) + "\n").encode("utf-8")
    if os.path.islink(dest):
        logging.warning(f"Replacing symlink {dest} -> {os.readlink(dest)}")
    elif os.path.lexists(dest):
        if not os.path.isfile(dest):
            logging.warning(f"Refusing to replace non-file {dest}")
            return
        with open(dest, "rb") as f:
            if f.read() == data:
                logging.info(f"OK: {dest} up to date")
                return
        logging.warning(f"Replacing stale generated copy {dest}")
    logging.info(f"{'Would write' if dry_run else 'Writing'} {dest}")
    if dry_run:
        return
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest), prefix=".AGENTS.md.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        # mkstemp yields 0600; this file is meant to be world-readable.
        os.chmod(tmp, 0o644)
        os.replace(tmp, dest)
    except BaseException:
        os.unlink(tmp)
        raise


def _is_skill_entry(path: str) -> bool:
    if os.path.islink(path):
        return True
    return os.path.isdir(path) and os.path.isfile(os.path.join(path, "SKILL.md"))


def install_harness(
    repo_root: str | None = None,
    home: str | None = None,
    *,
    dry_run: bool = False,
    confirm: Callable[[str], bool] | None = None,
) -> None:
    """Install the Zed and OpenCode harness content by copying.

    Copies every skill (directory containing ``SKILL.md``) in
    ``harnesses/zed/skills``, ``harnesses/shared/skills`` and
    ``superpowers/skills`` (earlier sources win on name collisions) into
    ``~/.agents/skills``, which both harnesses discover. Copies the
    ``harnesses/opencode/agents`` markdown files into a real
    ``~/.config/opencode/agents`` directory. Generates
    ``~/.config/zed/AGENTS.md`` and ``~/.config/opencode/AGENTS.md`` by
    concatenating ``harnesses/shared/AGENTS.md`` with each harness's own
    file. Removes legacy tkt plugin copies (LEGACY_PLUGIN_NAMES) from
    ``~/.config/opencode/plugin/``. Warns about (and, when confirmed)
    removes stale tkt-installed entries in the managed directories that
    this run no longer installs.
    """
    repo_root = repo_root or _repo_root()
    home = home or os.path.expanduser("~")
    confirm = confirm or (lambda msg: False)
    skills_dst = os.path.join(home, ".agents", "skills")
    zed_cfg = os.path.join(home, ".config", "zed")
    opencode_cfg = os.path.join(home, ".config", "opencode")
    if not dry_run:
        os.makedirs(skills_dst, exist_ok=True)
        os.makedirs(zed_cfg, exist_ok=True)
        os.makedirs(opencode_cfg, exist_ok=True)
    managed_skills: set[str] = set()
    for parts in _SKILL_SOURCES:
        _install_skill_dirs(os.path.join(repo_root, *parts), skills_dst, managed_skills, dry_run=dry_run)
    managed_agents = _install_agents(
        os.path.join(repo_root, "harnesses", "opencode", "agents"),
        os.path.join(opencode_cfg, "agents"),
        dry_run=dry_run,
    )
    _install_merged_agents_md(
        [
            os.path.join(repo_root, "harnesses", "shared", "AGENTS.md"),
            os.path.join(repo_root, "harnesses", "zed", "rules.md"),
        ],
        os.path.join(zed_cfg, "AGENTS.md"),
        dry_run=dry_run,
    )
    _install_merged_agents_md(
        [
            os.path.join(repo_root, "harnesses", "shared", "AGENTS.md"),
            os.path.join(repo_root, "harnesses", "opencode", "AGENTS.md"),
        ],
        os.path.join(opencode_cfg, "AGENTS.md"),
        dry_run=dry_run,
    )
    for name in LEGACY_PLUGIN_NAMES:
        legacy = plugin_dest(name, home)
        if not os.path.lexists(legacy):
            continue
        logging.warning(f"Removing legacy plugin copy {legacy} (removed from tkt)")
        if not dry_run:
            if os.path.isdir(legacy) and not os.path.islink(legacy):
                shutil.rmtree(legacy)
            else:
                os.remove(legacy)
    _clean_stale(skills_dst, managed_skills, candidate=_is_skill_entry, dry_run=dry_run, confirm=confirm)
    _clean_stale(
        os.path.join(opencode_cfg, "agents"),
        managed_agents,
        candidate=lambda path: os.path.isfile(path) and path.endswith(".md"),
        dry_run=dry_run,
        confirm=confirm,
    )
