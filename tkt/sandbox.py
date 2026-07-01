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

import shutil

__all__ = ("Sandbox",)

import logging
import os
import stat
import subprocess
from collections.abc import Iterable
from typing import Any

import git

from ._environment import Environment, Tool
from ._workspace import Workspace


class Sandbox(Tool):
    """Tool specialization for container sandboxes."""

    def __init__(self):
        pass

    @classmethod
    def from_json_data(cls, data: dict[str, Any]) -> Tool:
        if data:
            raise ValueError(f"Unexpected entries in nested container configuration: {data}.")
        return cls()

    def write(
        self,
        ticket: str,
        directory: str,
        packages: Iterable[str],
        workspace: Workspace,
        environment: Environment,
    ) -> None:
        packages = list(packages)
        script = os.path.join(directory, "run-sandbox")
        sandbox_dir = os.path.join(directory, ".sandbox")
        home_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "container", "home"))
        args = ["-it", "--rm", "--name", f"{workspace.ticket}"]
        for home_subdir in [".config", ".local", ".share", ".history"]:
            args.append("--mount")
            args.append(f"type=bind,src={os.path.join(home_dir, home_subdir)},dst=/sandbox/{home_subdir}")
        args.append("--mount")
        args.append(f"type=bind,source={os.path.abspath(sandbox_dir)},target=/sandbox/src")
        args.append(f"localdev:{workspace.metapackage_tag}")
        args.append("$@")
        with open(script, "w") as stream:
            stream.write("#!/bin/bash\npodman run " + " ".join(args))
        os.makedirs(sandbox_dir, exist_ok=True)
        os.chmod(script, stat.S_IXUSR | os.stat(script).st_mode)
        if os.path.exists(sandbox_dir):
            subprocess.run(f"podman unshare chown -R 0:0 {sandbox_dir}", shell=True)
        shutil.copytree(os.path.join(directory, "ups"), os.path.join(sandbox_dir, "ups"), dirs_exist_ok=True)
        for package in packages:
            package_dir = os.path.join(sandbox_dir, package)
            if not os.path.exists(package_dir):
                logging.info(f"cloning .sandbox/{package}.")
                sandbox_repo = git.Repo.clone_from(os.path.join(directory, package), package_dir)
                sandbox_repo.delete_remote(sandbox_repo.remotes["origin"])
                sandbox_repo.active_branch.rename(f"sandbox/{workspace.ticket}")
                sandbox_repo.config_writer("repository").add_value("receive.denyNonFastForwards", "false")
                git.Repo.init(package_dir, shared=True)
                direct_repo = git.Repo(os.path.join(directory, package))
                direct_repo.create_remote("sandbox", package_dir)
        subprocess.run(f"podman unshare chown -R 1001:1001 {sandbox_dir}", shell=True)

    def remove(
        self,
        ticket: str,
        directory: str,
        packages: Iterable[str],
        workspace: Workspace,
        environment: Environment,
    ) -> None:
        sandbox_dir = os.path.join(directory, ".sandbox")
        subprocess.run(f"podman unshare rm -rf {sandbox_dir}", shell=True)
