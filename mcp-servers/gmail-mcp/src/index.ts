#!/usr/bin/env node
/**
 * Gmail MCP server (Phase 0 stub).
 * Phase 5 adds check_idempotency, create_draft, send_email.
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

const server = new McpServer({
  name: "weekly-pulse-gmail-mcp",
  version: "0.1.0",
});

async function main(): Promise<void> {
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((error: unknown) => {
  console.error("gmail-mcp failed to start:", error);
  process.exit(1);
});
