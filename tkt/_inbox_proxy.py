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

"""Re-expose ``plannotator inbox mcp``'s tools as session tools, verbatim."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, ToolAnnotations

__all__ = ("register_inbox_tools",)

_UPSTREAM_COMMAND = "plannotator"
_UPSTREAM_ARGS = ("inbox", "mcp")
_LIST_TIMEOUT_S = 20.0
_CALL_TIMEOUT_S = 75.0  # > the upstream wait_for_reply ~50 s cursor

#: Properties ``submit_guide`` gains, because a sandbox-private path (a
#: restricted run's ``/tmp``) is invisible to the host process that would read
#: it.  Every other published property is upstream's own.
_TKT_EXTENSIONS: dict[str, dict[str, dict[str, Any]]] = {
    "submit_guide": {
        "patch_file": {
            "type": "string",
            "description": (
                "tkt extension: path to a unified-diff file inside the workspace, read on the "
                "host and forwarded as ``patch``."
            ),
            "x-tkt-extension": True,
        },
        "patch_spec": {
            "type": "object",
            "description": (
                "tkt extension: host-side git diff forwarded as ``patch`` — dir (repo inside the "
                "workspace, default its root), base (required ref), head (default HEAD), paths "
                "(optional pathspecs)."
            ),
            "x-tkt-extension": True,
        },
    }
}

#: ``(property name, value) -> unified diff text``; raises ValueError with an
#: agent-facing message when the source cannot be resolved.
PatchResolver = Callable[[str, Any], str]


def register_inbox_tools(
    mcp: MCPServer,
    *,
    workspace: str,
    patch_resolver: PatchResolver | None = None,
) -> int:
    """Publish every upstream Inbox tool under upstream's own name and schema.

    Called once per session-server connection, from the upstream
    ``tools/list``, so the surface follows upstream releases. Each published
    tool forwards its arguments to the upstream tool of the same name and
    returns upstream's content, structured content and error flag unchanged;
    each call is one short-lived ``plannotator inbox mcp`` connection, scoped
    to ``workspace`` because upstream derives the Inbox project from the
    invoking directory.

    An unreachable upstream costs only the Inbox tools: the rest of the session
    server starts. Returns the number of tools published.
    """
    try:
        upstream = _list_upstream_tools(workspace)
    except Exception:
        logging.warning("plannotator inbox listing failed; publishing no inbox tools", exc_info=True)
        return 0
    published = 0
    for tool in upstream:
        if not isinstance(tool.get("name"), str):
            continue
        try:
            if _publish(mcp, tool, workspace, patch_resolver):
                published += 1
        except Exception:
            logging.warning(f"could not publish inbox tool {tool['name']!r}", exc_info=True)
            continue
    return published


def _list_upstream_tools(cwd: str) -> list[dict[str, Any]]:
    """One-shot ``tools/list`` against ``plannotator inbox mcp``."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def _run() -> list[dict[str, Any]]:
        params = StdioServerParameters(command=_UPSTREAM_COMMAND, args=list(_UPSTREAM_ARGS), cwd=cwd)
        async with asyncio.timeout(_LIST_TIMEOUT_S), stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                res = await session.list_tools()
                return [t.model_dump(by_alias=True, mode="json", exclude_none=True) for t in res.tools]

    return asyncio.run(_run())


def _publish(mcp: MCPServer, tool: dict[str, Any], cwd: str, patch_resolver: PatchResolver | None) -> bool:
    """Register one upstream tool on ``mcp``; False when the name was taken."""
    name = str(tool["name"])
    schema = dict(tool.get("inputSchema") or {})
    extra = _TKT_EXTENSIONS.get(name, {}) if patch_resolver is not None else {}
    if extra:
        schema["properties"] = {**(schema.get("properties") or {}), **extra}
    names = [str(key) for key in (schema.get("properties") or {})]

    def forward(**kwargs: Any) -> Any:
        return _forward(name, kwargs, cwd, patch_resolver)

    # The SDK builds the advertised schema and its argument model from the
    # signature, so the parameters are synthesized from upstream's properties
    # (Any accepts whatever upstream actually declares) and the advertised
    # schema is then replaced wholesale.
    forward.__name__ = name
    forward.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        [inspect.Parameter(n, inspect.Parameter.KEYWORD_ONLY, annotation=Any, default=None) for n in names]
    )
    forward.__annotations__ = dict.fromkeys(names, Any)
    annotations = tool.get("annotations")
    mcp.add_tool(
        forward,
        name=name,
        title=tool.get("title"),
        description=tool.get("description") or f"Plannotator Inbox: {name}.",
        annotations=ToolAnnotations(**annotations) if isinstance(annotations, dict) else None,
        meta=tool.get("_meta"),
        structured_output=False,
    )
    registered = mcp._tool_manager.get_tool(name)
    # The SDK keeps the pre-existing tool when a name is already registered,
    # so only this call's own registration may have its schema replaced; a
    # colliding listing entry is dropped rather than rewriting tkt's surface.
    if registered is None or registered.fn is not forward:
        logging.warning(f"inbox tool name {name!r} is already registered; not publishing it")
        return False
    registered.parameters = schema
    return True


def _forward(name: str, kwargs: dict[str, Any], cwd: str, patch_resolver: PatchResolver | None) -> Any:
    arguments = {k: v for k, v in kwargs.items() if v is not None}
    if patch_resolver is not None:
        present = [source for source in ("patch", "patch_file", "patch_spec") if source in arguments]
        if len(present) > 1:
            names = ", ".join(present[:-1]) + " and " + present[-1]
            return f"plannotator inbox {name}: pass at most one of {names}."
        for source in present:
            if source != "patch":
                try:
                    patch_text = patch_resolver(source, arguments[source])
                except ValueError as exc:
                    return f"plannotator inbox {name}: {exc}"
                arguments.pop(source)
                arguments["patch"] = patch_text
    return asyncio.run(_call(name, arguments, cwd))


async def _call(name: str, arguments: dict[str, Any], cwd: str) -> Any:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=_UPSTREAM_COMMAND, args=list(_UPSTREAM_ARGS), cwd=cwd)
    try:
        async with asyncio.timeout(_CALL_TIMEOUT_S), stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool(name, arguments)
    except FileNotFoundError:
        return "plannotator inbox failed: plannotator binary not found on host."
    except Exception as exc:  # surface any upstream breakage as tool text
        return f"plannotator inbox failed: {exc}."
    return CallToolResult(
        content=res.content,
        structured_content=res.structured_content,
        is_error=res.is_error,
        _meta=res.meta,
    )
