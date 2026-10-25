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

__all__ = ("install_opencode_agent", "install_zed_agent")

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


def _ensure_link(link: str, target: str, *, dry_run: bool) -> None:
    """Make ``link`` a symlink to absolute ``target``.

    Idempotent: leaves a correct link alone, replaces a stale symlink, and
    refuses to clobber a real file or directory.
    """
    if os.path.islink(link):
        if os.path.realpath(link) == os.path.realpath(target):
            logging.info(f"OK: {link} -> {target}")
            return
        logging.warning(f"Replacing stale symlink {link} -> {os.readlink(link)}")
        if not dry_run:
            os.remove(link)
    elif os.path.lexists(link):
        logging.warning(f"Refusing to replace non-symlink {link}")
        return
    verb = "Would link" if dry_run else "Linking"
    logging.info(f"{verb} {link} -> {target}")
    if not dry_run:
        os.symlink(target, link)


def _clean_stale_links(
    directory: str, keep: set[str], *, dry_run: bool, confirm: Callable[[str], bool]
) -> None:
    """Warn about and remove unmanaged symlinks in ``directory``."""
    if not os.path.isdir(directory):
        return
    for name in sorted(os.listdir(directory)):
        if name in keep:
            continue
        path = os.path.join(directory, name)
        if not os.path.islink(path):
            logging.warning(f"Stale entry not managed by tkt (not a symlink; leaving it): {path}")
            continue
        logging.warning(f"Stale symlink not managed by tkt: {path}")
        if not dry_run and confirm(f"Remove stale symlink {path}?"):
            os.remove(path)


# Skill source roots linked into ~/.agents/skills, in precedence order:
# an earlier source wins on a name collision (warned about).
_SKILL_SOURCES: tuple[tuple[str, ...], ...] = (
    ("harnesses", "zed", "skills"),
    ("harnesses", "shared", "skills"),
    ("superpowers", "skills"),
)


def _link_skill_dirs(skills_src: str, skills_dst: str, managed: set[str], *, dry_run: bool) -> None:
    """Symlink each skill dir (containing ``SKILL.md``) under ``skills_src``
    into ``skills_dst``.

    Names already in ``managed`` (linked from an earlier source) are skipped
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
        _ensure_link(os.path.join(skills_dst, name), src, dry_run=dry_run)


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


def install_zed_agent(
    repo_root: str | None = None,
    home: str | None = None,
    *,
    dry_run: bool = False,
    confirm: Callable[[str], bool] | None = None,
) -> None:
    """Install the Zed harness skills and generate global agent
    instructions in the user's Zed config.

    Creates ``~/.agents/skills/<name>`` for each skill (directory containing
    ``SKILL.md``) in ``harnesses/zed/skills``, ``harnesses/shared/skills`` and
    ``superpowers/skills`` (earlier sources win on name collisions) and
    generates ``~/.config/zed/AGENTS.md`` by concatenating
    ``harnesses/shared/AGENTS.md`` and ``harnesses/zed/rules.md``. Warns about
    (and, when confirmed, removes) stale symlinks under ``~/.agents/skills``
    that this command no longer manages.
    """
    repo_root = repo_root or _repo_root()
    home = home or os.path.expanduser("~")
    confirm = confirm or (lambda msg: False)
    skills_dst = os.path.join(home, ".agents", "skills")
    zed_cfg = os.path.join(home, ".config", "zed")
    if not dry_run:
        os.makedirs(skills_dst, exist_ok=True)
        os.makedirs(zed_cfg, exist_ok=True)
    managed: set[str] = set()
    for parts in _SKILL_SOURCES:
        _link_skill_dirs(os.path.join(repo_root, *parts), skills_dst, managed, dry_run=dry_run)
    _install_merged_agents_md(
        [
            os.path.join(repo_root, "harnesses", "shared", "AGENTS.md"),
            os.path.join(repo_root, "harnesses", "zed", "rules.md"),
        ],
        os.path.join(zed_cfg, "AGENTS.md"),
        dry_run=dry_run,
    )
    _clean_stale_links(skills_dst, managed, dry_run=dry_run, confirm=confirm)


def install_opencode_agent(
    repo_root: str | None = None,
    home: str | None = None,
    *,
    dry_run: bool = False,
) -> None:
    """Symlink the OpenCode harness agents dir into the user's OpenCode config.

    Makes ``~/.config/opencode/agents`` a symlink to
    ``harnesses/opencode/agents``, replacing any stale symlink, and generates
    ``~/.config/opencode/AGENTS.md`` by concatenating
    ``harnesses/shared/AGENTS.md`` and ``harnesses/opencode/AGENTS.md``.
    Removes legacy tkt plugin copies (both the former bell plugin and the
    former wake plugin) from ``~/.config/opencode/plugin/``
    (LEGACY_PLUGIN_NAMES).
    """
    repo_root = repo_root or _repo_root()
    home = home or os.path.expanduser("~")
    src = os.path.join(repo_root, "harnesses", "opencode", "agents")
    dst_dir = os.path.join(home, ".config", "opencode")
    if not dry_run:
        os.makedirs(dst_dir, exist_ok=True)
    _ensure_link(os.path.join(dst_dir, "agents"), src, dry_run=dry_run)
    _install_merged_agents_md(
        [
            os.path.join(repo_root, "harnesses", "shared", "AGENTS.md"),
            os.path.join(repo_root, "harnesses", "opencode", "AGENTS.md"),
        ],
        os.path.join(dst_dir, "AGENTS.md"),
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
