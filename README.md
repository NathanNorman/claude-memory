# claude-memory

A persistent memory system for Claude Code, implemented as an MCP server. Gives Claude long-term recall across sessions by indexing both curated notes and thousands of past conversation archives with hybrid semantic + keyword search.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.9-blue.svg)](https://www.typescriptlang.org/)
[![MCP](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io/)

## The Problem

Claude Code sessions are stateless. Every new conversation starts from scratch — no memory of past debugging sessions, architecture decisions, or learned preferences. You end up re-explaining context that Claude already helped you figure out last week.

## How It Works

claude-memory runs as an MCP server that Claude Code connects to automatically. It provides three tools:

- **`memory_search`** — Hybrid search (semantic embeddings + BM25 keyword) across your curated notes and ~2,700+ conversation archives
- **`memory_read`** — Read specific memory files or retrieve full past conversations by session UUID
- **`memory_write`** — Append to daily logs or long-term memory files, with automatic re-indexing

All data stays local in `~/.claude-memory/`. Search is powered by SQLite with [sqlite-vec](https://github.com/asg017/sqlite-vec) for vector similarity and FTS5 for keyword matching. Embeddings are generated locally using [Xenova/transformers](https://github.com/xenova/transformers.js) — no external API calls.

## Quick Start

### 1. Clone and build

```bash
git clone https://github.com/NathanNorman/claude-memory.git
cd claude-memory
npm install
npm run build
```

### 2. Add to Claude Code

Add to your MCP settings (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "claude-memory": {
      "command": "node",
      "args": ["/path/to/claude-memory/dist/server.js"]
    }
  }
}
```

### 3. Start using it

Claude Code will automatically have access to `memory_search`, `memory_read`, and `memory_write` tools. No additional configuration needed.

## Architecture

```
~/.claude-memory/
├── MEMORY.md              # Long-term curated knowledge
├── memory/
│   └── YYYY-MM-DD.md      # Daily structured logs
└── index/
    └── memory.db           # SQLite search index (auto-maintained)

~/.claude/projects/         # Conversation archives (read-only)
└── */
    └── *.jsonl             # Past session transcripts
```

### Search Pipeline

1. Query hits both **vector search** (cosine similarity via sqlite-vec) and **keyword search** (BM25 via FTS5)
2. Results are merged using reciprocal rank fusion (70% semantic, 30% keyword)
3. Post-filters apply: date range, project, source type
4. Session deduplication caps results at 2 per conversation file
5. Snippets are truncated at sentence boundaries

### Indexing

- Curated memory files are chunked by markdown headings
- Conversation archives are parsed into exchange-level chunks (user/assistant pairs)
- Embeddings are generated locally using `all-MiniLM-L6-v2` (384-dim)
- Index staleness is checked via file modification times — reindexing only runs when files change

## Tools Reference

### memory_search

Search memories using hybrid semantic + keyword search.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | (required) | Search query text |
| `maxResults` | number | 10 | Maximum results to return |
| `minScore` | number | 0 | Minimum relevance score (0-1) |
| `after` | string | "" | Only results after this date (YYYY-MM-DD) |
| `before` | string | "" | Only results before this date (YYYY-MM-DD) |
| `project` | string | "" | Filter by project directory name |
| `source` | string | "" | "curated", "conversations", or "" for both |

**Search tips:**
- Start broad with 2-3 key terms, not full sentences
- Conversation results typically score 0.02-0.05; curated memory scores higher
- Use `source: "curated"` to search only your notes, `source: "conversations"` for session history

### memory_read

Read a specific memory file or retrieve a past conversation.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `path` | string | (required) | Relative path within `~/.claude-memory/`, or a session UUID |
| `from` | number | 1 | Starting line number (1-based) |
| `lines` | number | 0 | Number of lines to return (0 = all) |

### memory_write

Write to memory files with automatic re-indexing.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `content` | string | (required) | Content to write |
| `file` | string | "memory/YYYY-MM-DD.md" | Target file (MEMORY.md or memory/*.md) |
| `append` | boolean | true | Append to file or overwrite |

## Development

```bash
npm install          # Install dependencies
npm run build        # Build with esbuild
npm run typecheck    # Type checking
npm test             # Run integration tests
```

## Tech Stack

- **Runtime:** Node.js (ES modules)
- **Language:** TypeScript
- **Database:** SQLite via [better-sqlite3](https://github.com/JoshuaWise/better-sqlite3) (WAL mode)
- **Vector search:** [sqlite-vec](https://github.com/asg017/sqlite-vec) (cosine similarity)
- **Keyword search:** SQLite FTS5 (BM25 ranking)
- **Embeddings:** [Xenova/transformers.js](https://github.com/xenova/transformers.js) (all-MiniLM-L6-v2, 384-dim)
- **Schema validation:** [Zod](https://github.com/colinhacks/zod)
- **MCP SDK:** [@modelcontextprotocol/sdk](https://github.com/modelcontextprotocol/typescript-sdk)
- **Build:** esbuild (single-file bundle)

## License

MIT
