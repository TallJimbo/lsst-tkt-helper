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

__all__ = ("Superpowers",)

import logging
import os
import shutil
from collections.abc import Iterable
from typing import Any

import git

from ._environment import Environment, Tool
from ._workspace import Workspace
from .sandbox import AGENT_SUBDIR

# Name of the docs worktree directory under the workspace's agent dir.
WORKTREE_NAME = "superpowers-docs"

# Shared-repo branch new ticket branches are created from.
BASE_BRANCH = "main"


class Superpowers(Tool):
    """Tool that gives a workspace a git worktree of the shared docs repo.

    ``write`` attaches a worktree of the shared repo (``path``) at
    ``<workspace>/.agent/superpowers-docs``, checked out on the workspace's
    ticket branch (the same branch name the packages use, without the
    ``-agent`` suffix); new ticket branches are created from ``main``.  The
    agent writes design specs and plans under ``<ticket>/specs`` and
    ``<ticket>/plans`` in that worktree and commits them there, so the
    ticket's docs live durably on the shared repo's ticket branch; the human
    merges that branch into the shared repo's main branch when the ticket is
    done.  ``remove`` deregisters the worktree from the shared repo.

    The sandbox keeps the shared repo mounted read-write: the worktree's
    common git directory (refs, objects) lives there.
    """

    def __init__(self, path: str):
        self.path = path

    @classmethod
    def from_json_data(cls, data: dict[str, Any]) -> Tool:
        path = data.pop("path")
        if data:
            raise ValueError(f"Unexpected entries in superpowers configuration: {data}.")
        return cls(path)

    def _shared_repo(self) -> git.Repo | None:
        """Return the shared docs repo, or None if it is unavailable."""
        path = os.path.expanduser(self.path)
        if not os.path.isdir(path):
            logging.warning(f"Superpowers docs repo {path} does not exist; skipping docs worktree.")
            return None
        return git.Repo(path)

    def write(
        self,
        ticket: str,
        directory: str,
        packages: Iterable[str],
        workspace: Workspace,
        environment: Environment,
    ) -> None:
        repo = self._shared_repo()
        if repo is None:
            return
        # The docs branch matches the packages' ticket branch naming; the
        # package argument does not affect the name.
        branch = environment.get_default_branch(WORKTREE_NAME, ticket)
        worktree_dir = os.path.join(directory, AGENT_SUBDIR, WORKTREE_NAME)
        repo.git.worktree("prune")
        if os.path.exists(worktree_dir):
            logging.info(f"Superpowers docs worktree already exists at {worktree_dir}.")
            return
        logging.info(f"Creating superpowers docs worktree at {worktree_dir} on branch {branch}.")
        os.makedirs(os.path.join(directory, AGENT_SUBDIR), exist_ok=True)
        if branch in repo.heads:
            # Existing branch (e.g. from an earlier workspace for this
            # ticket): attach to it without moving the branch head.
            repo.git.worktree("add", worktree_dir, branch)
        else:
            repo.git.worktree("add", "-b", branch, worktree_dir, BASE_BRANCH)

    def remove(self, directory: str) -> None:
        """Remove the ``.agent/superpowers-docs`` worktree from the repo."""
        worktree_dir = os.path.join(directory, AGENT_SUBDIR, WORKTREE_NAME)
        if not os.path.exists(worktree_dir):
            return
        repo = self._shared_repo()
        if repo is None:
            shutil.rmtree(worktree_dir, ignore_errors=True)
            return
        logging.info(f"Removing superpowers docs worktree at {worktree_dir}.")
        try:
            repo.git.worktree("remove", "--force", worktree_dir)
        except git.GitCommandError:
            logging.info(f"git worktree remove failed; removing {worktree_dir} directly.")
            shutil.rmtree(worktree_dir, ignore_errors=True)
        repo.git.worktree("prune")
