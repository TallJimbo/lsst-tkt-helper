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

"""Host-side MCP proxy bridging a restricted sandbox's stdio MCP
connection to a remote streamable-HTTP MCP server, injecting an auth
header on the host so the token never enters the sandbox.

Spawned per connection by the sandbox bridge's socat listener
(``net-*/mcp-<name>.sock``); serves stdio MCP until EOF.  Tool schemas
are relayed verbatim: this module knows nothing about any particular
upstream server.  The proxy is tools-only: upstream ``prompts/*`` and
``resources/*`` are deliberately not relayed (spec D4).

Invariant: this process's stderr/stdout are inherited by socat and land
in ``<net_dir>/host-mcp-<name>-socat.log``, and ``net_dir`` is
bind-mounted read-write *into* the sandbox, so nothing token-bearing
(header values, request bodies, token file contents) may ever be logged
or printed here.  Tracebacks are fine: values are not interpolated into
exception messages.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any, NoReturn

import mcp.types as types
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from mcp.server import Server
from mcp.server.stdio import stdio_server


def _read_token(token_file: str) -> str:
    """Read the single-line token, stripped; exit nonzero if unusable."""
    path = os.path.expanduser(token_file)
    try:
        with open(path, encoding="utf-8") as f:
            token = f.read().strip()
    except OSError as exc:
        raise SystemExit(f"tkt.remote_mcp_proxy: cannot read token file {path}: {exc}")
    if not token:
        raise SystemExit(f"tkt.remote_mcp_proxy: token file {path} is empty")
    return token


async def _serve(url: str, headers: dict[str, str]) -> None:
    """Relay stdio JSON-RPC to the upstream, verbatim, with ``headers``."""
    # streamable_http_client does not close a caller-provided client, so
    # own its lifecycle here.
    async with create_mcp_http_client(headers=headers) as http:
        async with streamable_http_client(url, http_client=http) as (read, write, _get_session_id):
            async with ClientSession(read, write) as upstream:
                await upstream.initialize()
                server = Server("tkt-remote-mcp")

                @server.list_tools()
                async def list_tools() -> list[types.Tool]:
                    return (await upstream.list_tools()).tools

                @server.call_tool()
                async def call_tool(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
                    return await upstream.call_tool(name, arguments)

                async with stdio_server() as (client_read, client_write):
                    await server.run(client_read, client_write, server.create_initialization_options())


def main() -> NoReturn:
    """Parse arguments, read the token, and serve until EOF."""
    parser = argparse.ArgumentParser(prog="tkt.remote_mcp_proxy")
    parser.add_argument("--url", required=True, help="Remote streamable-HTTP MCP endpoint")
    parser.add_argument("--token-file", required=True, help="Host-side single-line token file")
    parser.add_argument(
        "--header",
        default="Bearer {token}",
        help="Authorization value template; {token} is replaced with the file contents",
    )
    args = parser.parse_args()
    headers = {"Authorization": args.header.format(token=_read_token(args.token_file))}
    asyncio.run(_serve(args.url, headers))
    raise SystemExit(0)


if __name__ == "__main__":
    main()
