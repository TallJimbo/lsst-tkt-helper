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
from collections.abc import Callable

__all__ = ("install_opencode_agent", "install_zed_agent")


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def install_zed_agent(
    repo_root: str | None = None,
    home: str | None = None,
    *,
    dry_run: bool = False,
    confirm: Callable[[str], bool] | None = None,
) -> None:
    """Symlink the Zed harness skills and rules into the user's Zed config.

    Creates ``~/.agents/skills/<name>`` for each skill (directory containing
    ``SKILL.md``) in ``harnesses/zed/skills`` and ``superpowers/skills`` and
    ``~/.config/zed/AGENTS.md`` -> ``harnesses/zed/rules.md``. Warns about
    (and, when confirmed, removes) stale symlinks under ``~/.agents/skills``
    that this command no longer manages.
    """
    repo_root = repo_root or _repo_root()
    home = home or os.path.expanduser("~")
    confirm = confirm or (lambda msg: False)
    skills_src = os.path.join(repo_root, "harnesses", "zed", "skills")
    skills_dst = os.path.join(home, ".agents", "skills")
    zed_cfg = os.path.join(home, ".config", "zed")
    if not dry_run:
        os.makedirs(skills_dst, exist_ok=True)
        os.makedirs(zed_cfg, exist_ok=True)
    managed: set[str] = set()
    if os.path.isdir(skills_src):
        for name in sorted(os.listdir(skills_src)):
            src = os.path.join(skills_src, name)
            if not os.path.isdir(src):
                continue
            managed.add(name)
            _ensure_link(os.path.join(skills_dst, name), src, dry_run=dry_run)
    superpowers_src = os.path.join(repo_root, "superpowers", "skills")
    if os.path.isdir(superpowers_src):
        for name in sorted(os.listdir(superpowers_src)):
            src = os.path.join(superpowers_src, name)
            if not os.path.isdir(src) or not os.path.isfile(os.path.join(src, "SKILL.md")):
                continue
            managed.add(name)
            _ensure_link(os.path.join(skills_dst, name), src, dry_run=dry_run)
    _ensure_link(
        os.path.join(zed_cfg, "AGENTS.md"),
        os.path.join(repo_root, "harnesses", "zed", "rules.md"),
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
    ``harnesses/opencode/agents``, replacing any stale symlink.
    """
    repo_root = repo_root or _repo_root()
    home = home or os.path.expanduser("~")
    src = os.path.join(repo_root, "harnesses", "opencode", "agents")
    dst_dir = os.path.join(home, ".config", "opencode")
    if not dry_run:
        os.makedirs(dst_dir, exist_ok=True)
    _ensure_link(os.path.join(dst_dir, "agents"), src, dry_run=dry_run)
