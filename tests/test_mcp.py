import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_stdio_server_enforces_catalog_permissions(tmp_path):
    audit_path = tmp_path / "audit.log"

    async def check():
        params = StdioServerParameters(command=sys.executable, args=["-m", "agentguard.mcp_server"])
        with audit_path.open("w") as audit:
            async with stdio_client(params, errlog=audit) as streams, ClientSession(*streams) as session:
                await session.initialize()
                assert {t.name for t in (await session.list_tools()).tools} == {"search", "read_document"}
                valid = await session.call_tool("read_document", {"document_id": "rag"})
                assert not valid.isError and "retrieved documents" in valid.content[0].text
                for name, args in [("AG_PRIVATE_TEST", {}), ("search", {"query": "AG_PRIVATE_TEST"}),
                                   ("read_document", {"document_id": "two words"})]:
                    assert (await session.call_tool(name, args)).isError
    asyncio.run(check())
    audit = audit_path.read_text()
    assert '"decision": "allowed"' in audit and audit.count('"decision": "blocked"') == 3
    assert "AG_PRIVATE_TEST" not in audit
