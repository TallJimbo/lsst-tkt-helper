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

__all__ = ("OpenSpec",)

import logging
import os
import re
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any, NamedTuple

from ._environment import Environment, Tool
from ._workspace import Workspace

# Claude Code tool name -> OpenCode tool name.  Order matters: replace the
# longest (with "tool") first so bare names left after don't double-replace.
_TOOL_RENAMES: tuple[tuple[str, str], ...] = (
    ("AskUserQuestion tool", "question tool"),
    ("AskUserQuestion", "question"),
    ("TodoWrite tool", "todowrite tool"),
    ("TodoWrite", "todowrite"),
)

# Claude Code restriction line to drop from the frontmatter.
_ALLOWED_TOOLS_RE = re.compile(r"^allowed-tools:\s*Bash\([^)]*\).*\n?", re.MULTILINE)

# Question-tool suggestions injected into decision points of openspec-explore.
# Each entry targets an exact anchor line/string that must exist; if it is gone
# (upstream changed the skill), a warning is emitted.  Idempotent because the
# injected text itself is used as the "already applied" guard.
_QUESTION_TOOL_INJECTIONS: tuple[dict[str, str], ...] = (
    {
        "skill": "openspec-explore",
        "anchor": "**Open questions**: [if any remain]",
        "inject": (
            "\n   Use the **question tool** to ask when the user must decide "
            "among options (e.g. which next step to pursue)."
        ),
    },
    {
        "skill": "openspec-explore",
        "anchor": "4. **The user decides** - Offer and move on. Don't pressure. Don't auto-capture.",
        "inject": (
            "\n   If a decision needs the user's input, offer the options with "
            "the **question tool** rather than open prose."
        ),
    },
)


def _fix_content(content: str) -> str:
    """Apply the tool renames + frontmatter removal.  Pure string transform."""
    for bad, good in _TOOL_RENAMES:
        content = content.replace(bad, good)
    return _ALLOWED_TOOLS_RE.sub("", content)


def _apply_injections(path: Path, content: str) -> tuple[str, list[str]]:
    """Return (content with question-tool injections applied, warnings)."""
    skill_name = path.parent.name
    warnings: list[str] = []
    for injection in _QUESTION_TOOL_INJECTIONS:
        if injection["skill"] != skill_name:
            continue
        anchor = injection["anchor"]
        if anchor not in content:
            warnings.append(
                f"{path}: expected insertion point disappeared (skill changed upstream?): {anchor!r}"
            )
            continue
        if injection["inject"] not in content:
            # Insert immediately after the anchor, preserving the next line.
            content = content.replace(anchor, anchor + injection["inject"], 1)
    return content, warnings


def _iter_skill_files(directory: Path) -> list[Path]:
    return sorted(directory.rglob("SKILL.md"))


class _FixSkillsResult(NamedTuple):
    """Result of running the skill fix on a directory tree."""

    files_changed: int
    warnings: list[str]
    files_found: int


class OpenSpec(Tool):
    """Tool that integrates the ``openspec`` CLI into a workspace.

    ``write`` runs ``openspec init --tools opencode`` (when ``.opencode`` is
    absent) and points the workspace's ``openspec/`` directory at the shared
    store.  The generated Claude-Code-oriented skills are then rewritten for
    OpenCode's harness via the :meth:`fix_skills` staticmethod.
    """

    def __init__(self, store: str):
        self.store = store

    @classmethod
    def from_json_data(cls, data: dict[str, Any]) -> Tool:
        store = data.pop("store")
        if data:
            raise ValueError(f"Unexpected entries in pre-commit configuration: {data}.")
        return cls(store)

    @staticmethod
    def fix_skills(directory: str | Path, *, dry_run: bool = False) -> _FixSkillsResult:
        """Rewrite OpenSpec skill files under ``directory`` for OpenCode.

        Recursively find every ``SKILL.md`` under ``directory`` and apply the
        tool renames, remove the ``allowed-tools`` frontmatter restriction, and
        inject ``question``-tool suggestions into ``openspec-explore`` at
        anchored decision points.  Missing anchors are logged as warnings and
        skipped, never raised.  Idempotent: safe to run on already-fixed files.

        If ``directory`` is not a directory, or contains no ``SKILL.md`` files,
        this returns a result with ``files_found == 0`` and no changes.
        """
        directory = Path(directory)
        warnings: list[str] = []
        files_changed = 0
        files = _iter_skill_files(directory) if directory.is_dir() else []
        for path in files:
            content = path.read_text(encoding="utf-8")
            new_content = _fix_content(content)
            new_content, path_warnings = _apply_injections(path, new_content)
            warnings.extend(path_warnings)
            if new_content == content:
                continue
            files_changed += 1
            if not dry_run:
                path.write_text(new_content, encoding="utf-8")
        for warning in warnings:
            logging.warning(warning)
        return _FixSkillsResult(
            files_changed=files_changed,
            warnings=warnings,
            files_found=len(files),
        )

    def write(
        self,
        ticket: str,
        directory: str,
        packages: Iterable[str],
        workspace: Workspace,
        environment: Environment,
    ) -> None:
        openspec_dir = os.path.join(directory, "openspec")
        if not os.path.exists(os.path.join(directory, ".opencode")):
            old_dir = os.curdir
            # We need to run 'openspec init' first to install skills, but then
            # we need to delete and recreate the openspec doc directory it
            # creates in favor of a pointer to a shared store.
            try:
                os.chdir(directory)
                subprocess.run(["openspec", "init", "--tools", "opencode"], capture_output=True, check=True)
            finally:
                os.chdir(old_dir)
            shutil.rmtree(openspec_dir)
        if not os.path.exists(openspec_dir):
            os.makedirs(openspec_dir, exist_ok=True)
            with open(os.path.join(openspec_dir, "config.yaml"), "w") as stream:
                stream.write(f"store: {self.store}\n")
        # Ensure freshly-installed (or re-invoked) skills are OpenCode-ready.
        # Idempotent and warn-only on missing anchors; never fails write().
        self.fix_skills(os.path.join(directory, ".opencode", "skills"))
