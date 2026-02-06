import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import {
  memorySearchInputSchema,
  memoryReadInputSchema,
  memoryWriteInputSchema,
  handleMemorySearch,
  handleMemoryRead,
  handleMemoryWrite,
  closeDb,
} from './tools.js';

// --- Logging (stderr only — stdout is for MCP protocol) ---

function log(msg: string): void {
  process.stderr.write(`[claude-memory] ${msg}\n`);
}

// --- MCP Server Setup ---

const server = new McpServer(
  { name: 'claude-memory', version: '1.0.0' },
);

// --- Register Tools ---

server.tool(
  'memory_search',
  `Search memories using hybrid semantic + keyword search.

Indexes both curated memory files (~/.claude-memory/) and conversation archives (~2,700 past sessions).

Search tips — start broad, then narrow:
- Use 2-3 key terms, not full sentences: "gradle build failure" not "debugging the gradle build failure we had last week"
- Start with the most distinctive term: "webpack config" or "migration script" rather than "AWS" or "deploy"
- If too few results, drop terms: "gradle" alone finds more than "debugging build failure gradle"
- If too many results, add a specific term to narrow: "gradle shadow" or "gradle artifact"
- Conversation results typically score 0.02-0.05; memory entries score higher

Filtering:
- after/before: YYYY-MM-DD date range (e.g. after="2026-02-01", before="2026-02-05")
- project: match conversation results by project directory (e.g. "my-app", "side-project")
- source: "curated" for memory files only, "conversations" for session history only, empty for both`,
  memorySearchInputSchema,
  async (args) => {
    try {
      const result = await handleMemorySearch(args);
      return {
        content: [{ type: 'text' as const, text: JSON.stringify(result, null, 2) }],
      };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      log(`memory_search error: ${message}`);
      return {
        content: [{ type: 'text' as const, text: JSON.stringify({ error: message }) }],
        isError: true,
      };
    }
  },
);

server.tool(
  'memory_read',
  'Read a specific memory file from ~/.claude-memory/. Pass a session UUID from search results to read full conversation text with pagination.',
  memoryReadInputSchema,
  async (args) => {
    try {
      const result = await handleMemoryRead(args);
      return {
        content: [{ type: 'text' as const, text: JSON.stringify(result, null, 2) }],
      };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      log(`memory_read error: ${message}`);
      return {
        content: [{ type: 'text' as const, text: JSON.stringify({ error: message }) }],
        isError: true,
      };
    }
  },
);

server.tool(
  'memory_write',
  'Write or append content to a memory file',
  memoryWriteInputSchema,
  async (args) => {
    try {
      const result = await handleMemoryWrite(args);
      return {
        content: [{ type: 'text' as const, text: JSON.stringify(result, null, 2) }],
      };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      log(`memory_write error: ${message}`);
      return {
        content: [{ type: 'text' as const, text: JSON.stringify({ error: message }) }],
        isError: true,
      };
    }
  },
);

// --- Graceful Shutdown ---

let isShuttingDown = false;

async function shutdown(reason: string): Promise<void> {
  if (isShuttingDown) return;
  isShuttingDown = true;
  log(`Shutting down: ${reason}`);
  try {
    await server.close();
  } catch {
    // Server may already be closed
  }
  closeDb();
  log('Cleanup complete, exiting');
  process.exit(0);
}

// --- Start Server ---

async function main(): Promise<void> {
  log('Starting MCP server...');
  const transport = new StdioServerTransport();
  await server.connect(transport);
  log('MCP server connected via stdio');

  // Detect parent process exit: stdin closes when Claude Code exits.
  // StdioServerTransport does NOT handle this — we must listen ourselves.
  process.stdin.on('end', () => shutdown('stdin closed (parent exited)'));
  process.stdin.on('error', () => shutdown('stdin error'));

  // Handle OS signals
  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT', () => shutdown('SIGINT'));
}

main().catch((err) => {
  log(`Fatal error: ${err instanceof Error ? err.message : String(err)}`);
  process.exit(1);
});
