#!/usr/bin/env node
/**
 * Google Docs MCP server (Phase 0 stub).
 * Phase 4 adds find_section_by_anchor, append_section, get_document_url.
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

const server = new McpServer({
  name: "weekly-pulse-google-docs-mcp",
  version: "0.1.0",
});

async function main(): Promise<void> {
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

main().catch((error: unknown) => {
  console.error("google-docs-mcp failed to start:", error);
  process.exit(1);
});
