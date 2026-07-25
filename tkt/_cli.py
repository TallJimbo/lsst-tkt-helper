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
from collections.abc import Iterable
from typing import TextIO

import click

from ._environment import Environment
from ._workspace import Workspace


def _setup_logging(verbose: int) -> None:
    logging.basicConfig(
        level={0: logging.WARNING, 1: logging.INFO, 2: logging.DEBUG}[verbose],
        format="%(message)s",
    )


@click.group()
def cli() -> None:
    pass


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
@click.option("--tool", "tools", multiple=True, default=("zed", "pyright", "sandbox", "precommit"), type=str)
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
    tools: Iterable[str] = (),
    dry_run: bool = False,
    verbose: int = 0,
) -> None:
    _setup_logging(verbose)
    if environment is None:
        raise click.UsageError("No --environment and TKT_ENVIRONMENT not set.")
    else:
        env = Environment.from_file(environment)
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
    workspace.update(
        packages=packages,
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
    workspace.remove(environment=env)


@cli.command("agent-run")
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
@click.option("-v", "--verbose", count=True)
def agent_run(
    *,
    ticket: str | None,
    directory: str | None,
    environment: TextIO | None,
    shell: bool = False,
    verbose: int = 0,
) -> None:
    _setup_logging(verbose)
    if environment is None:
        raise click.UsageError("No --environment and TKT_ENVIRONMENT not set.")
    env = Environment.from_file(environment)
    workspace = Workspace.from_existing(ticket=ticket, directory=directory, environment=env)
    tool = env.get_tool("sandbox")
    if tool is None:
        raise click.UsageError("No 'sandbox' tool configured in the tkt environment.")
    from .sandbox import Sandbox

    if not isinstance(tool, Sandbox):
        raise click.UsageError(
            f"Configured 'sandbox' tool is {type(tool).__name__}, not tkt.sandbox.Sandbox."
        )
    tool.run(workspace, shell=shell)
