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
from collections.abc import Iterable
from typing import TextIO

import click

from ._environment import Environment
from ._workspace import Workspace
from .openspec import OpenSpec


def _setup_logging(verbose: int) -> None:
    logging.basicConfig(
        level={0: logging.WARNING, 1: logging.INFO, 2: logging.DEBUG}[verbose],
        format="%(message)s",
    )


@click.group()
def cli() -> None:
    pass


@cli.command(
    "fix-openspec",
    help=(
        "Rewrite OpenSpec skill files under DIR for OpenCode's harness. "
        "Standalone (no environment required); DIR defaults to the current "
        "directory. Exits with status 2 if no SKILL.md is found."
    ),
)
@click.argument(
    "directory",
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
    required=False,
)
@click.option("-n", "--dry-run", is_flag=True)
@click.option("-v", "--verbose", count=True)
def fix_openspec(directory: str | None, *, dry_run: bool = False, verbose: int = 0) -> None:
    _setup_logging(verbose)
    if directory is None:
        directory = os.path.abspath(os.curdir)
    result = OpenSpec.fix_skills(directory, dry_run=dry_run)
    if result.files_found == 0:
        click.echo(f"No SKILL.md files found under {directory}.", err=True)
        raise SystemExit(2)
    verb = "would be" if dry_run else "were"
    click.echo(f"done: {result.files_changed} file(s) {verb} updated, {len(result.warnings)} warning(s)")


@cli.command()
@click.argument("ticket")
@click.argument("packages", nargs=-1)
@click.option(
    "-d",
    "--directory",
    type=click.Path(exists=True, file_okay=False, writable=True, resolve_path=True),
)
@click.option("-t", "--tag", type=str)
@click.option("--metapackage")
@click.option("--workspace-eups-product")
@click.option(
    "--environment",
    envvar="TKT_ENVIRONMENT",
    type=click.File(),
)
@click.option(
    "--add-tool",
    "add_tools",
    multiple=True,
    default=(),
    type=str,
    help="Add a tool to the new workspace in addition to the configured defaults.",
)
@click.option(
    "--remove-tool",
    "remove_tools",
    multiple=True,
    default=(),
    type=str,
    help="Remove a tool from the new workspace relative to the configured defaults.",
)
@click.option("-n", "--dry-run", is_flag=True)
@click.option("-v", "--verbose", count=True)
def new(
    ticket: str,
    packages: Iterable[str],
    *,
    directory: str | None,
    tag: str | None,
    metapackage: str | None,
    workspace_eups_product: str | None,
    environment: TextIO | None,
    add_tools: Iterable[str] = (),
    remove_tools: Iterable[str] = (),
    dry_run: bool = False,
    verbose: int = 0,
) -> None:
    _setup_logging(verbose)
    if environment is None:
        raise click.UsageError("No --environment and TKT_ENVIRONMENT not set.")
    else:
        env = Environment.from_file(environment)
    tools = env.default_tools
    for name in add_tools:
        if name not in tools:
            tools = (*tools, name)
    for name in remove_tools:
        tools = tuple(t for t in tools if t != name)
    Workspace.new(
        ticket=ticket,
        packages=packages,
        directory=directory,
        metapackage=metapackage,
        tag=tag,
        workspace_eups_product=workspace_eups_product,
        tools=tools,
        environment=env,
        dry_run=dry_run,
    )


@cli.command()
@click.argument("packages", nargs=-1)
@click.option(
    "-d",
    "--directory",
    type=click.Path(exists=True, file_okay=False, writable=True, resolve_path=True),
)
@click.option("--ticket")
@click.option(
    "--environment",
    envvar="TKT_ENVIRONMENT",
    type=click.File(),
)
@click.option("-n", "--dry-run", is_flag=True)
@click.option("-v", "--verbose", count=True)
def update(
    packages: Iterable[str],
    *,
    ticket: str | None,
    directory: str | None,
    environment: TextIO | None,
    dry_run: bool = False,
    verbose: int = 0,
) -> None:
    _setup_logging(verbose)
    if environment is None:
        raise click.UsageError("No --environment and TKT_ENVIRONMENT not set.")
    else:
        env = Environment.from_file(environment)
    workspace = Workspace.from_existing(ticket=ticket, directory=directory, environment=env)
    missing = [t for t in env.default_tools if t not in workspace.tools]
    stale = [t for t in workspace.tools if env.get_tool(t) is None]
    if dry_run:
        for t in missing:
            logging.warning(f"Would ask to add missing default tool {t}.")
        for t in stale:
            logging.warning(f"Would remove unconfigured tool {t}.")
        workspace.update(packages=packages, environment=env, dry_run=True)
        return
    for t in stale:
        logging.warning(f"Removing tool {t} because it is no longer configured.")
    workspace.remove_tools(stale)
    additions: list[str] = []
    if missing:
        if click.confirm("Missing default tools: " + ", ".join(missing) + ". Add them?"):
            additions = missing
    workspace.update(
        packages=packages,
        tools=additions,
        environment=env,
        dry_run=dry_run,
    )


@cli.command("upgrade-metapackage")
@click.option(
    "-d",
    "--directory",
    type=click.Path(exists=True, file_okay=False, writable=True, resolve_path=True),
)
@click.option("--ticket")
@click.option(
    "--environment",
    envvar="TKT_ENVIRONMENT",
    type=click.File(),
)
@click.option("-t", "--tag", type=str)
@click.option("--metapackage")
@click.option("-n", "--dry-run", is_flag=True)
@click.option("-v", "--verbose", count=True)
def upgrade_metapackage(
    *,
    ticket: str | None,
    directory: str | None,
    environment: TextIO | None,
    tag: str | None,
    metapackage: str | None,
    dry_run: bool = False,
    verbose: int = 0,
) -> None:
    _setup_logging(verbose)
    if environment is None:
        raise click.UsageError("No --environment and TKT_ENVIRONMENT not set.")
    else:
        env = Environment.from_file(environment)
    workspace = Workspace.from_existing(ticket=ticket, directory=directory, environment=env)
    workspace.upgrade_metapackage(
        metapackage=metapackage,
        tag=tag,
        environment=env,
        dry_run=dry_run,
    )


@cli.command("rm")
@click.argument("ticket")
@click.option(
    "-d",
    "--directory",
    type=click.Path(exists=True, file_okay=False, writable=True, resolve_path=True),
)
@click.option(
    "--environment",
    envvar="TKT_ENVIRONMENT",
    type=click.File(),
)
@click.option("-v", "--verbose", count=True)
def rm(
    *,
    ticket: str | None,
    directory: str | None,
    environment: TextIO | None,
    verbose: int = 0,
) -> None:
    _setup_logging(verbose)
    if environment is None:
        raise click.UsageError("No --environment and TKT_ENVIRONMENT not set.")
    else:
        env = Environment.from_file(environment)
    workspace = Workspace.from_existing(ticket=ticket, directory=directory, environment=env)
    workspace.remove()


@cli.command(
    "sandbox-run",
    help=(
        "Run the LLM agent sandbox. Autodetects mode from the current directory: "
        "if a `.agent` subdirectory is present it runs in workspace mode; otherwise "
        "it treats the current directory as a single git repository (single-repo mode)."
    ),
)
@click.option(
    "-d",
    "--directory",
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
)
@click.option("--ticket")
@click.option(
    "--environment",
    envvar="TKT_ENVIRONMENT",
    type=click.File(),
)
@click.option(
    "--shell",
    is_flag=True,
    help="Drop into an interactive shell in the sandbox instead of launching the agent command.",
)
@click.option(
    "--conda-env",
    type=str,
    help="Activate this conda environment inside the sandbox before anything else.",
)
@click.option(
    "--command",
    "cmd",
    type=str,
    help=(
        "Override the configured final command with this (shlex-split string). "
        "Mutually exclusive with --shell."
    ),
)
@click.option("-v", "--verbose", count=True)
def sandbox_run(
    *,
    ticket: str | None,
    directory: str | None,
    environment: TextIO | None,
    shell: bool = False,
    conda_env: str | None = None,
    cmd: str | None = None,
    verbose: int = 0,
) -> None:
    _setup_logging(verbose)
    if environment is None:
        raise click.UsageError("No --environment and TKT_ENVIRONMENT not set.")
    if cmd is not None and shell:
        raise click.UsageError("--command and --shell are mutually exclusive.")
    from .sandbox import Sandbox

    cwd = os.path.abspath(".")
    if os.path.isdir(os.path.join(cwd, ".agent")):
        # Workspace mode: existing behavior.
        env = Environment.from_file(environment)
        workspace = Workspace.from_existing(ticket=ticket, directory=directory, environment=env)
        tool = env.get_tool("sandbox")
        if tool is None:
            raise click.UsageError("No 'sandbox' tool configured in the tkt environment.")
        if not isinstance(tool, Sandbox):
            raise click.UsageError(
                f"Configured 'sandbox' tool is {type(tool).__name__}, not tkt.sandbox.Sandbox."
            )
        tool.run(workspace, shell=shell, command=cmd)
    else:
        # Single-repo mode: treat the CWD as the repository root.
        cls, data = Environment.load_config(environment)
        tools = cls.load_tools(data)
        sandbox = tools.get("sandbox")
        if sandbox is None:
            raise click.UsageError("No 'sandbox' tool configured in the tkt environment.")
        if not isinstance(sandbox, Sandbox):
            raise click.UsageError(
                f"Configured 'sandbox' tool is {type(sandbox).__name__}, not tkt.sandbox.Sandbox."
            )
        repo_dir = directory if directory is not None else cwd
        sandbox.run_single_repo(repo_dir, shell=shell, conda_env=conda_env, command=cmd)


@cli.command(
    "sandbox-reset",
    help=(
        "Reset all `.agent` worktrees to the state of the corresponding "
        "human-workspace branch. Uncommitted work is saved to the git stash; "
        "unmerged agent commits are saved to a timestamped backup branch "
        "(`<branch>-saved-<timestamp>`) before the reset."
    ),
)
@click.option(
    "-d",
    "--directory",
    type=click.Path(exists=True, file_okay=False, resolve_path=True),
)
@click.option("--ticket")
@click.option(
    "--environment",
    envvar="TKT_ENVIRONMENT",
    type=click.File(),
)
@click.option("-v", "--verbose", count=True)
def sandbox_reset(
    *,
    ticket: str | None,
    directory: str | None,
    environment: TextIO | None,
    verbose: int = 0,
) -> None:
    _setup_logging(verbose)
    if environment is None:
        raise click.UsageError("No --environment and TKT_ENVIRONMENT not set.")
    from .sandbox import Sandbox

    env = Environment.from_file(environment)
    workspace = Workspace.from_existing(ticket=ticket, directory=directory, environment=env)
    tool = env.get_tool("sandbox")
    if tool is None:
        raise click.UsageError("No 'sandbox' tool configured in the tkt environment.")
    if not isinstance(tool, Sandbox):
        raise click.UsageError(
            f"Configured 'sandbox' tool is {type(tool).__name__}, not tkt.sandbox.Sandbox."
        )
    tool.reset(workspace)
