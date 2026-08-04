"""The same ten tools, spoken over MCP — cahier §9, the second half of gate C6.

§9's rule is that each capability is written once and exposed *twice*: as a
LangChain tool the graph calls, and over MCP for anything outside this process.
So this module registers no capability of its own. It asks
``registry.build_toolset()`` for the list and reflects it — name, description and
JSON schema all derived from the ``BaseTool`` objects the agents already use.

That is the whole security argument, and it is why wrapping is worth insisting on.
``read_file`` and ``list_files`` confine every path through ``check_path`` against
the session root; the three sandbox tools are bound to one worktree at
construction. An MCP server that reimplemented those calls would have to re-derive
those guarantees, and the tests covering the LangChain surface would not cover it.
Because this one delegates, an escape refused there is refused here — and
``tests/test_mcp.py`` asserts exactly that over a real client session rather than
trusting the argument.

Two consequences of reflecting the registry, both deliberate:

- **The tool count follows the workspace.** Without one there are seven tools, not
  ten, because the sandbox trio cannot exist without a worktree to run in. ``forge
  mcp`` therefore opens a throwaway worktree for its lifetime, the same thing
  ``forge tools`` does, and tears it down on the way out.
- **Results are serialised, not stringified.** The registry promises a string or a
  plain dict; the sandbox tools return an ``ExecutionReport``. A caller needs the
  exit code as a number, so models are dumped as JSON and carried in
  ``structured_content`` beside the text, and only genuine strings are passed
  through untouched.

Transport is stdio: one process, one client, no port and no listening socket. That
is the right default for a tool server that can read files and execute tests, and
it is what an MCP client launches anyway.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool
from mcp import types
from mcp.server.lowlevel import Server

from forge import __version__
from forge.config import Settings, get_settings
from forge.core.workspace import Workspace
from forge.tools.registry import build_toolset

SERVER_NAME = "forge"

_INSTRUCTIONS = (
    "FORGE's knowledge and execution tools over one indexed repository. "
    "search_code is the general way in; ripgrep_search when you know the literal "
    "string; find_definitions/find_references for symbols. File reads are confined "
    "to the repository root and a path that escapes it is refused and logged. "
    "run_pytest/run_python/run_linter execute inside a network-less container."
)


def _input_schema(tool: BaseTool) -> dict[str, Any]:
    """The tool's own argument model, as JSON Schema.

    Derived rather than declared: the schema an MCP client validates against is
    generated from the same pydantic model the LangChain tool validates against, so
    the two surfaces cannot drift into disagreeing about what an argument means.
    """
    try:
        schema = tool.get_input_schema().model_json_schema()
    except Exception:  # noqa: BLE001 — a tool with no schema is callable with none
        schema = {}
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    return schema


def describe_mcp_tools(tools: list[BaseTool]) -> list[types.Tool]:
    """The MCP tool descriptors for a LangChain toolset."""
    return [
        types.Tool(
            name=tool.name,
            description=" ".join((tool.description or "").split()),
            input_schema=_input_schema(tool),
        )
        for tool in tools
    ]


def _as_content(result: Any) -> tuple[str, dict[str, Any] | None]:
    """Tool output as (text, structured). A report's exit code must survive as a number."""
    if isinstance(result, str):
        return result, None
    if hasattr(result, "model_dump"):
        payload = result.model_dump(mode="json")
        return json.dumps(payload, indent=2), payload
    if isinstance(result, dict):
        return json.dumps(result, indent=2, default=str), result
    return str(result), None


def build_mcp_server(
    *,
    settings: Settings | None = None,
    workspace: Workspace | None = None,
    session_id: str = "",
    include_sandbox: bool = True,
    **resources: Any,
) -> Server:
    """An MCP server over the §9 toolset, built for one workspace.

    ``resources`` forwards the shared ``client``/``embedder``/``encoder`` to
    ``build_toolset`` so a long-lived server holds one Qdrant client rather than
    opening one per call — embedded Qdrant allows exactly one per path per process.
    """
    settings = settings or get_settings()
    tools = build_toolset(
        settings=settings,
        workspace=workspace,
        session_id=session_id,
        include_sandbox=include_sandbox,
        **resources,
    )
    by_name = {tool.name: tool for tool in tools}
    descriptors = describe_mcp_tools(tools)

    async def on_list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=descriptors)

    async def on_call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        tool = by_name.get(params.name)
        if tool is None:
            return _error(f"no such tool: {params.name}")
        try:
            result = await tool.ainvoke(params.arguments or {})
        except Exception as error:  # noqa: BLE001
            # A refusal and a crash must not look alike to a client. Both come back
            # as is_error, but the message is the exception's own text — the caller
            # is a model, and "refused: path escapes the repository root" is a
            # sentence it can act on where a traceback is not.
            return _error(f"{type(error).__name__}: {error}")
        text, structured = _as_content(result)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            structured_content=structured,
        )

    return Server(
        SERVER_NAME,
        version=__version__,
        title="FORGE",
        instructions=_INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)], is_error=True
    )


async def serve_stdio(server: Server) -> None:
    """Serve on stdin/stdout until the client disconnects.

    Nothing may be printed to stdout by anything else while this runs — stdout *is*
    the protocol channel. Every diagnostic in this path goes to stderr for that
    reason.
    """
    import mcp

    async with mcp.stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


__all__ = ["SERVER_NAME", "build_mcp_server", "describe_mcp_tools", "serve_stdio"]
