"""Exercise official MCP initialization, discovery and tool invocation against a running demo API."""

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    url = os.environ.get("ONTOBRIDGE_API_URL", "http://127.0.0.1:8787")
    with httpx.Client() as client:
        token = os.environ.get("ONTOBRIDGE_API_TOKEN")
        if not token:
            response = client.post(url + "/api/demo-session/viewer")
            response.raise_for_status()
            token = response.json()["token"]
    params = StdioServerParameters(
        command=str(Path(sys.executable).parent / "ontobridge-mcp"),
        env={**os.environ, "ONTOBRIDGE_API_TOKEN": token, "ONTOBRIDGE_API_URL": url},
    )
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        await session.initialize()
        tools = await session.list_tools()
        assert len(tools.tools) == 7
        for name in [
            "list_due_orders",
            "get_order_360",
            "check_delivery_risk",
            "get_material_shortage",
            "get_production_progress",
            "simulate_schedule",
        ]:
            args = (
                {"days": 7}
                if name == "list_due_orders"
                else {"extra_hours": 2}
                if name == "simulate_schedule"
                else {"order_id": "SO-2026-0088"}
            )
            result = await session.call_tool(name, args)
            assert not result.isError, result
            payload = json.loads(result.content[0].text)
            assert payload["meta"]["provenance"]
        rejected = await session.call_tool(
            "propose_schedule_change",
            {
                "order_id": "SO-2026-0088",
                "note": "只读角色不得提交草案",
                "idempotency_key": "mcp-viewer-test",
            },
        )
        assert rejected.isError
        print("MCP PASS: initialized, 7 tools discovered, 6 read calls verified, viewer write denied.")


if __name__ == "__main__":
    asyncio.run(main())
