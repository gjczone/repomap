#!/usr/bin/env node

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { registerTools } from "./tools.js";
import { ensureRepomapInstalled } from "./repomap.js";

const server = new McpServer({
  name: "repomap-mcp-server",
  version: "2.1.2",
});

registerTools(server);

async function main() {
  await ensureRepomapInstalled();
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((err) => {
  console.error("Fatal error starting repomap-mcp-server:", err);
  process.exit(1);
});
