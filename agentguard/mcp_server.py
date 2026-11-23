"""Read-only, stdio MCP server. Audit events go to stderr, protocol frames to stdout."""
import asyncio
import json
import sys
from uuid import uuid4

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from agentguard.policy import CallBlocked, Read, Search, ToolFailure, ToolGuard
from agentguard.providers import local_read, local_search


def dispatch(name, arguments, *, guard, audit):
    trace = []
    request_id = uuid4().hex
    try:
        result = guard.call("mcp", name, arguments, trace)
        return [types.TextContent(type="text", text=json.dumps(result))]
    finally:
        for item in trace:
            audit({"run_id": request_id, **item})


def create_server():
    server = Server("agentguard")

    @server.list_tools()
    async def list_tools():
        return [types.Tool(name="search", description="Search the public sample catalog.",
                           inputSchema=Search.model_json_schema()),
                types.Tool(name="read_document", description="Read a sample by catalog ID.",
                           inputSchema=Read.model_json_schema())]

    guard = ToolGuard(local_search, local_read)

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        # Dispatch owns argument validation so rejected attempts receive audit events.
        try:
            return dispatch(name, arguments, guard=guard,
                            audit=lambda item: print(json.dumps(item), file=sys.stderr, flush=True))
        except (CallBlocked, ToolFailure) as exc:
            raise ValueError(str(exc)) from None

    return server


async def main():
    server = create_server()
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
