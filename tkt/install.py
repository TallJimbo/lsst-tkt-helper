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
import re
import shutil
import tempfile
from collections.abc import Callable

__all__ = ("PLANNOTATOR_HOST_ONLY_SKILLS", "install_harness")

# tkt-owned plugin artifacts from older installs that live in the host plugin
# dir but must be *removed* on install (their feature was removed from tkt;
# a stale copy would otherwise keep loading).  A name may be a single file or
# a directory plugin (e.g. ``zed-bell``).
LEGACY_PLUGIN_NAMES: tuple[str, ...] = ("zed-bell.js", "zed-bell", "plannotator-wake.js")

# Patches applied to the vendored Plannotator skills' SKILL.md at install
# time, keyed by skill directory name; the files in harnesses/shared/skills
# stay verbatim upstream text.  Upstream only ever describes MCP tools by
# top-level names ("it can be listed with a prefix ... loaded through tool
# search"), which in a code-mode harness (tool catalog entries of the form
# ``tools["<server>"].<tool>``, callable only from inside the code-mode
# ``execute`` tool) leads agents to call the always-failing bare name and
# misread the error as a dead MCP server.  Each patch is anchored on unique
# upstream text; a pattern not matching exactly once is warned about, since
# that means upstream reworded the anchored passage.
PLANNOTATOR_SKILL_PATCHES: dict[str, tuple[tuple[re.Pattern[str], str], ...]] = {
    "plannotator": (
        (
            re.compile(re.escape("not `--gate --json`.")),
            "not `--gate --json`.\n\n"
            'In a harness whose tool catalog lists entries as `tools["<server>"].<tool>` (a code-mode '
            "harness), that path is the call form: invoke the tool from code mode (the `execute` tool), "
            "never as a top-level tool name. There, for example, "
            '`tools["tkt-session"].plannotator({ action: "open", kind: "review", target: "<dir>", '
            'base: "<ref>" })` opens a review. A failure of the bare name `plannotator` in such a harness '
            "is not evidence the server is down — try the catalog path before reporting anything.",
        ),
        (
            re.compile(re.escape("Use the CLI only when you have no such tool, or")),
            "Use the CLI only when you have no such tool (having it means your catalog lists it in a "
            'callable spelling; in a code-mode harness a bare-name failure means call the `tools["..."]` '
            "path the catalog gives, not that the tool is gone), or",
        ),
        (
            re.compile(re.escape("use the stdio MCP server `plannotator inbox mcp`.")),
            "use the stdio MCP server `plannotator inbox mcp`. In a code-mode catalog listing it as "
            '`tools["<server>"].plannotator_inbox`, that path is the call form (invoke it from `execute`).',
        ),
    ),
    "plannotator-annotate": (
        (
            re.compile(re.escape("reviewing it inline in chat.\n")),
            "reviewing it inline in chat.\n\n"
            "If you have a `plannotator` tool — including a tool catalog entry "
            '`tools["<server>"].plannotator` callable only from code mode — use it instead of the commands '
            'below: `{ "action": "open", "target": "<path-or-url>", "kind": "annotate" }` (plus '
            '`"gate": true` for an approval gate). It runs the session where the reviewer\'s browser reaches '
            "it; a CLI session started in a sandbox is unreachable by design.",
        ),
    ),
    "plannotator-review": (
        (
            re.compile(re.escape("reading a diff inline.\n")),
            "reading a diff inline.\n\n"
            "If you have a `plannotator` tool — including a tool catalog entry "
            '`tools["<server>"].plannotator` callable only from code mode — use it instead of the command '
            'below: `{ "action": "open", "kind": "review", "target": "<directory-or-pr-url>", '
            'base": "<ref>" }`. It runs the session where the reviewer\'s browser reaches it; a CLI session '
            "started in a sandbox is unreachable by design.",
        ),
    ),
}

# Plannotator skills that tkt deliberately does not vendor: their instructions
# shell out to the `plannotator` CLI or read host data directories, which a
# restricted sandbox cannot use.  The Plannotator installer drops them into
# ~/.agents/skills and reinstalls them on every upgrade, so install_harness
# removes them and sandbox runs deny them to agents
# (tkt.sandbox.opencode_config_content).  A Plannotator skill present in
# ~/.agents/skills but *not* named here or vendored is warned about and left
# in place: whether it belongs vendored, host-only, or nowhere depends on
# reading it.
PLANNOTATOR_HOST_ONLY_SKILLS: tuple[str, ...] = ("plannotator-inbox", "plannotator-last")


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


def _apply_skill_patches(name: str, content: str) -> str:
    """Return ``content`` with the tkt patches for skill ``name`` applied,
    warning about any patch whose anchor text is missing or ambiguous.
    """
    for pattern, repl in PLANNOTATOR_SKILL_PATCHES.get(name, ()):
        content, count = pattern.subn(repl, content)
        if count != 1:
            logging.warning(
                f"tkt patch for skill {name} matched {count} times (expected 1); upstream reworded the"
                " anchored passage — update PLANNOTATOR_SKILL_PATCHES"
            )
    return content


def _install_patched_skill(
    name: str,
    src: str,
    dst: str,
    *,
    dry_run: bool,
    confirm: Callable[[str], bool],
    unattended: bool,
) -> None:
    """Install a vendored upstream skill dir into ``dst`` with the tkt
    patches applied to ``SKILL.md``.

    When the installed ``SKILL.md`` differs from both the verbatim vendored
    copy and its patched form — a Plannotator upgrade past the vendored
    revision, or a local edit — offer to adopt it as the new vendored
    upstream copy (overwriting the repo's ``SKILL.md``, after which the
    patches apply to the adopted text).  Adoption only ever covers
    ``SKILL.md``; the rest of the skill dir stays tkt's copy.  An
    ``unattended`` run (CLI ``--yes``) never adopts: rewriting the repo
    without a human is not on offer, so it just overwrites the installed copy
    with tkt's patched text.
    """
    src_md = os.path.join(src, "SKILL.md")
    dst_md = os.path.join(dst, "SKILL.md")
    with open(src_md, encoding="utf-8") as f:
        vendored = f.read()
    patched = _apply_skill_patches(name, vendored)
    if os.path.isfile(dst_md):
        with open(dst_md, encoding="utf-8") as f:
            installed = f.read()
        # installed == vendored is a fresh Plannotator install of exactly the
        # vendored upstream text: no drift, the patched copy just goes on top.
        if installed not in (vendored, patched):
            logging.warning(
                f"Installed skill {name} differs from tkt's patched copy (updated by Plannotator or edited)"
            )
            adopt = (not unattended) and confirm(
                f"Adopt the installed {name} as tkt's vendored upstream copy, overwriting {src_md}"
                " (tkt patches re-apply at install)?"
            )
            if adopt:
                logging.info(f"Adopting installed {name} into {src_md}")
                if not dry_run:
                    with open(src_md, "w", encoding="utf-8") as f:
                        f.write(installed)
                vendored = installed
    _copy_skill(src, dst, dry_run=dry_run)
    if dry_run:
        logging.info(f"Would apply tkt patches to {dst_md}")
        return
    with open(dst_md, "w", encoding="utf-8") as f:
        f.write(_apply_skill_patches(name, vendored))


def _install_skill_dirs(
    skills_src: str,
    skills_dst: str,
    managed: set[str],
    *,
    dry_run: bool,
    confirm: Callable[[str], bool],
    unattended: bool,
) -> None:
    """Copy each skill dir (containing ``SKILL.md``) under ``skills_src``
    into ``skills_dst``.

    Names already in ``managed`` (copied from an earlier source) are skipped
    with a warning.  Skills with tkt patches go through
    :func:`_install_patched_skill` instead of a plain copy.  ``managed`` is
    updated in place.
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
        dst = os.path.join(skills_dst, name)
        if name in PLANNOTATOR_SKILL_PATCHES:
            _install_patched_skill(name, src, dst, dry_run=dry_run, confirm=confirm, unattended=unattended)
        else:
            _copy_skill(src, dst, dry_run=dry_run)


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


def _clean_skill_entries(skills_dst: str, managed: set[str], *, dry_run: bool) -> None:
    """Handle ``~/.agents/skills`` entries this tkt run did not install.

    Entries named in ``PLANNOTATOR_HOST_ONLY_SKILLS`` — reinstalled into the
    shared skill home by Plannotator's own updater — are removed
    unconditionally.  Every other skill entry is warned about and left in
    place: tkt cannot tell a stale tkt-installed skill from a new
    Plannotator-installed or hand-written one, and the right action (delete,
    vendor, or list as host-only) requires reading it.
    """
    if not os.path.isdir(skills_dst):
        return
    for name in sorted(os.listdir(skills_dst)):
        if name in managed:
            continue
        path = os.path.join(skills_dst, name)
        if not _is_skill_entry(path):
            continue
        if name in PLANNOTATOR_HOST_ONLY_SKILLS:
            logging.warning(f"Removing host-only Plannotator skill reinstalled outside tkt: {path}")
            if not dry_run:
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            continue
        logging.warning(f"Unrecognized skill entry, left in place for review: {path}")


def install_harness(
    repo_root: str | None = None,
    home: str | None = None,
    *,
    dry_run: bool = False,
    confirm: Callable[[str], bool] | None = None,
    unattended: bool = False,
) -> None:
    """Install the Zed and OpenCode harness content by copying.

    Copies every skill (directory containing ``SKILL.md``) in
    ``harnesses/zed/skills``, ``harnesses/shared/skills`` and
    ``superpowers/skills`` (earlier sources win on name collisions) into
    ``~/.agents/skills``, which both harnesses discover; vendored Plannotator
    skills get the tkt patches applied and offer to adopt an externally
    updated copy back into the repo (never when ``unattended``).  Copies the
    ``harnesses/opencode/agents`` markdown files into a real
    ``~/.config/opencode/agents`` directory.  Generates
    ``~/.config/zed/AGENTS.md`` and ``~/.config/opencode/AGENTS.md`` by
    concatenating ``harnesses/shared/AGENTS.md`` with each harness's own
    file.  Removes legacy tkt plugin copies (LEGACY_PLUGIN_NAMES) from
    ``~/.config/opencode/plugin/``.  In ``~/.agents/skills``, removes
    host-only Plannotator skills reinstalled by Plannotator
    (PLANNOTATOR_HOST_ONLY_SKILLS) and warns about any other unrecognized
    skill entry without touching it; warns about (and, when confirmed)
    removes stale tkt-installed agent files no longer shipped.
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
        _install_skill_dirs(
            os.path.join(repo_root, *parts),
            skills_dst,
            managed_skills,
            dry_run=dry_run,
            confirm=confirm,
            unattended=unattended,
        )
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
    _clean_skill_entries(skills_dst, managed_skills, dry_run=dry_run)
    _clean_stale(
        os.path.join(opencode_cfg, "agents"),
        managed_agents,
        candidate=lambda path: os.path.isfile(path) and path.endswith(".md"),
        dry_run=dry_run,
        confirm=confirm,
    )
