import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import {
  Client,
  StreamableHTTPClientTransport,
} from "@modelcontextprotocol/client";
import { Client as LegacyClient } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport as LegacyTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { createGateway } from "../server.mjs";
const token = "Bearer mocked-platform-token-long-enough";
const input = {
  type: "object",
  properties: { order_id: { type: "string" } },
  required: ["order_id"],
  additionalProperties: false,
};
const result = {
  result: { order: { id: "SO-2026-0088" }, progress: 72 },
  meta: {
    ontology_version: "ont:1",
    capability_version: "get_order_360:1",
    computed_at: "2026-10-29",
    provenance: [{ source_key: "SO-2026-0088" }],
  },
};
async function fixture() {
  let catalog = [
    {
      name: "get_order_360",
      title: "订单全景",
      description: "Published order view",
      inputSchema: input,
      outputSchema: { type: "object" },
      version: 1,
    },
  ];
  const calls = [];
  const api = createServer(async (req, res) => {
    if (req.headers.authorization !== token) {
      res.writeHead(401).end(JSON.stringify({ detail: "denied" }));
      return;
    }
    let body = "";
    for await (const chunk of req) body += chunk;
    res.setHeader("Content-Type", "application/json");
    if (req.url === "/api/me")
      res.end(
        JSON.stringify({ user: "viewer", tenant: "mock", role: "viewer" }),
      );
    else if (req.url === "/api/v1/tools")
      res.end(JSON.stringify({ tools: catalog }));
    else if (req.url === "/api/v1/mcp/invoke/get_order_360") {
      calls.push(JSON.parse(body));
      if (typeof calls.at(-1).order_id !== "string") {
        res.writeHead(422).end(JSON.stringify({ detail: "invalid arguments" }));
        return;
      }
      res.end(JSON.stringify(result));
    } else res.writeHead(404).end(JSON.stringify({ detail: "not published" }));
  });
  await new Promise((resolve) => api.listen(0, "127.0.0.1", resolve));
  const gateway = createGateway({
    apiUrl: `http://127.0.0.1:${api.address().port}`,
  });
  await new Promise((resolve) =>
    gateway.server.listen(0, "127.0.0.1", resolve),
  );
  const url = new URL(`http://127.0.0.1:${gateway.server.address().port}/mcp`);
  return {
    url,
    calls,
    setCatalog: (x) => {
      catalog = x;
    },
    close: async () => {
      await gateway.close();
      await new Promise((resolve) => api.close(resolve));
    },
  };
}

test("official v2 negotiates 2026-07-28, discovers dynamic tools and shares backend semantics", async () => {
  const f = await fixture();
  const client = new Client(
    { name: "acceptance", version: "1" },
    { versionNegotiation: { mode: { pin: "2026-07-28" } } },
  );
  try {
    await client.connect(
      new StreamableHTTPClientTransport(f.url, {
        requestInit: { headers: { Authorization: token } },
      }),
    );
    assert.equal(client.getProtocolEra(), "modern");
    assert.equal((await client.listTools()).tools[0].name, "get_order_360");
    assert.deepEqual(
      (
        await client.callTool({
          name: "get_order_360",
          arguments: { order_id: "SO-2026-0088" },
        })
      ).structuredContent,
      result,
    );
    assert.equal(
      (
        await client.callTool({
          name: "get_order_360",
          arguments: { order_id: 8 },
        })
      ).isError,
      true,
    );
    f.setCatalog([]);
    assert.deepEqual((await client.listTools()).tools, []);
    assert.equal(f.calls.length, 2);
  } finally {
    await client.close();
    await f.close();
  }
});

test("official v1 client initializes and calls the same governed tool", async () => {
  const f = await fixture(),
    client = new LegacyClient({ name: "legacy-acceptance", version: "1" });
  try {
    await client.connect(
      new LegacyTransport(f.url, {
        requestInit: { headers: { Authorization: token } },
      }),
    );
    assert.equal((await client.listTools()).tools[0].name, "get_order_360");
    assert.deepEqual(
      (
        await client.callTool({
          name: "get_order_360",
          arguments: { order_id: "SO-2026-0088" },
        })
      ).structuredContent,
      result,
    );
  } finally {
    await client.close();
    await f.close();
  }
});

test("no handshake required; rejects header mismatches, invalid identity and cross origin", async () => {
  const f = await fixture();
  const body = {
    jsonrpc: "2.0",
    id: 9,
    method: "tools/list",
    params: {
      _meta: {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientInfo": { name: "raw", version: "1" },
        "io.modelcontextprotocol/clientCapabilities": {},
      },
    },
  };
  const headers = {
    Authorization: token,
    "Content-Type": "application/json",
    Accept: "application/json, text/event-stream",
    "MCP-Protocol-Version": "2026-07-28",
    "Mcp-Method": "tools/list",
  };
  try {
    let response = await fetch(f.url, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
    assert.equal(response.status, 200, await response.clone().text());
    assert.equal(response.headers.get("Mcp-Session-Id"), null);
    const payload = await response.json();
    assert.equal(payload.result.tools[0].name, "get_order_360");
    response = await fetch(f.url, {
      method: "POST",
      headers: { ...headers, "Mcp-Method": "tools/call" },
      body: JSON.stringify(body),
    });
    assert.equal(response.status, 400);
    response = await fetch(f.url, {
      method: "POST",
      headers: {
        ...headers,
        Authorization: "Bearer wrong-but-long-token-xxxxxxxx",
      },
      body: JSON.stringify(body),
    });
    assert.equal(response.status, 401);
    response = await fetch(f.url, {
      method: "POST",
      headers: { ...headers, Origin: "https://evil.example" },
      body: JSON.stringify(body),
    });
    assert.equal(response.status, 403);
  } finally {
    await f.close();
  }
});
