import { createServer } from "node:http";
import { pathToFileURL } from "node:url";
import { createMcpHandler, Server } from "@modelcontextprotocol/server";
import { toNodeHandler } from "@modelcontextprotocol/node";

// This gateway and the API are two processes of the same private application.
// The original platform credential is checked by the API on EVERY request. No shared service identity.
export function createGateway({
  apiUrl = "http://127.0.0.1:8787",
  allowedHosts = ["localhost", "127.0.0.1", "[::1]"],
} = {}) {
  const base = new URL(apiUrl);
  if (
    !["http:", "https:"].includes(base.protocol) ||
    base.username ||
    base.password ||
    base.search ||
    base.hash
  )
    throw new Error("Invalid private API URL");
  async function backend(path, token, body) {
    const response = await fetch(new URL(path, base), {
      method: body === undefined ? "GET" : "POST",
      headers: { Authorization: token, "Content-Type": "application/json" },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: AbortSignal.timeout(35000),
      redirect: "error",
    });
    const value = await response.json();
    return { status: response.status, ok: response.ok, value };
  }
  const handler = createMcpHandler(
    ({ requestInfo }) => {
      const token = requestInfo.headers.get("authorization");
      const server = new Server(
        { name: "ontobridge", version: "1.0.0" },
        {
          capabilities: { tools: {} },
          cacheHints: { "tools/list": { ttlMs: 0, cacheScope: "private" } },
        },
      );
      server.setRequestHandler("tools/list", async () => {
        const response = await backend("/api/v1/tools", token);
        if (!response.ok) throw new Error("Capability catalog unavailable");
        return {
          tools: response.value.tools
            .map(({ version, ...tool }) => tool)
            .sort((a, b) => a.name.localeCompare(b.name)),
        };
      });
      server.setRequestHandler("tools/call", async (request) => {
        const name = request.params.name;
        if (!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(name))
          return {
            content: [{ type: "text", text: "Invalid capability name" }],
            isError: true,
          };
        const response = await backend(
          "/api/v1/mcp/invoke/" + encodeURIComponent(name),
          token,
          request.params.arguments ?? {},
        );
        if (!response.ok)
          return {
            content: [
              {
                type: "text",
                text: JSON.stringify({
                  status: response.status,
                  error: response.value.detail || "Capability unavailable",
                }),
              },
            ],
            isError: true,
          };
        return {
          content: [{ type: "text", text: JSON.stringify(response.value) }],
          structuredContent: response.value,
          isError: false,
        };
      });
      return server;
    },
    { legacy: "stateless", maxRequestBodySize: 1024 * 1024 },
  );
  const guarded = {
    async fetch(request) {
      const url = new URL(request.url);
      if (url.pathname !== "/mcp")
        return new Response("Not found", { status: 404 });
      if (!allowedHosts.includes(url.hostname))
        return new Response("Host rejected", { status: 403 });
      const origin = request.headers.get("origin");
      if (origin && origin !== url.origin)
        return new Response("Origin rejected", { status: 403 });
      const token = request.headers.get("authorization") || "";
      if (!token.startsWith("Bearer ") || token.length < 31)
        return new Response("Authentication required", { status: 401 });
      try {
        const identity = await backend("/api/me", token);
        if (!identity.ok)
          return new Response("Authentication rejected", {
            status: identity.status === 403 ? 403 : 401,
          });
        const response = await handler.fetch(request);
        response.headers.set("Cache-Control", "no-store");
        response.headers.set("X-Content-Type-Options", "nosniff");
        return response;
      } catch {
        return new Response("Private capability API unavailable", {
          status: 503,
        });
      }
    },
  };
  const nodeHandler = toNodeHandler(guarded);
  const server = createServer((req, res) => {
    void nodeHandler(req, res);
  });
  return {
    server,
    close: async () => {
      await handler.close();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  const host = process.env.ONTOBRIDGE_MCP_HOST || "127.0.0.1";
  const allowedHosts = (
    process.env.ONTOBRIDGE_MCP_ALLOWED_HOSTS || "localhost,127.0.0.1,[::1]"
  ).split(",");
  const gateway = createGateway({
    apiUrl: process.env.ONTOBRIDGE_API_URL,
    allowedHosts,
  });
  const port = Number(process.env.ONTOBRIDGE_MCP_PORT || 8790);
  gateway.server.listen(port, host, () =>
    process.stderr.write(`OntoBridge MCP listening on ${host}:${port}/mcp\n`),
  );
  for (const signal of ["SIGINT", "SIGTERM"])
    process.on(signal, () => gateway.close().then(() => process.exit(0)));
}
