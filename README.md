# claude-memory

A persistent memory system for Claude Code, implemented as an MCP server. Gives Claude long-term recall across sessions by indexing curated notes and thousands of past conversation archives with hybrid keyword + vector search.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MCP](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io/)

## The Problem

Claude Code sessions are stateless. Every new conversation starts from scratch — no memory of past debugging sessions, architecture decisions, or learned preferences. You end up re-explaining context that Claude already helped you figure out last week.

## How It Works

claude-memory runs as an MCP server that Claude Code connects to automatically. It provides four tools:

- **`memory_search`** — Hybrid search combining FTS5 keyword matching and vector similarity (cosine) across curated notes and conversation archives, merged via Reciprocal Rank Fusion
- **`memory_read`** — Read specific memory files or retrieve full past conversations by session UUID
- **`memory_write`** — Append to daily logs or long-term memory files, with immediate FTS5 indexing and vector embedding generation
- **`get_status`** — Health check for both search backends with chunk/vector counts

All data stays local in `~/.claude-memory/`. No external API calls for search. Embeddings are generated locally using `all-MiniLM-L6-v2` (384-dim) via [sentence-transformers](https://www.sbert.net/).

## Quick Start

### 1. Clone and build

```bash
git clone https://github.com/NathanNorman/claude-memory.git
cd claude-memory
npm install
npm run build
```

### 2. Set up the Python environment

The MCP server runs in Python. Set up a venv with the required packages:

```bash
python3 -m venv ~/.claude-memory/graphiti-venv
~/.claude-memory/graphiti-venv/bin/pip install mcp sentence-transformers torch numpy
```

### 3. Add to Claude Code

Add to your MCP settings (e.g., `~/.claude.json`):

```json
{
  "mcpServers": {
    "unified-memory": {
      "type": "stdio",
      "command": "/bin/bash",
      "args": ["/path/to/claude-memory/unified-mcp-launcher.sh"]
    }
  }
}
```

### 4. Initialize the index

```bash
# Build the search index from conversation archives
node dist/reindex-cli.js
```

### 5. Start using it

Claude Code will automatically have access to `memory_search`, `memory_read`, `memory_write`, and `get_status` tools. No additional configuration needed.

## Architecture

The system has two components: a **Node.js indexer** that builds and maintains the search index, and a **Python MCP server** that handles search queries and writes.

```
~/claude-memory/                    # This repo (source code)
├── src/
│   ├── unified_memory_server.py    # Python MCP server (runtime)
│   ├── indexer.ts                  # Node.js batch indexer
│   ├── embeddings.ts               # Embedding generation (ONNX)
│   ├── chunker.ts                  # Document chunking
│   ├── search.ts                   # Search implementation
│   └── doctor-cli.ts               # Database diagnostics
├── scripts/
│   ├── conversation_parser.py      # JSONL conversation parser
│   └── shared.py                   # Shared utilities
└── unified-mcp-launcher.sh         # MCP server launcher

~/.claude-memory/                   # Runtime data directory
├── MEMORY.md                       # Long-term curated knowledge
├── memory/
│   └── YYYY-MM-DD.md               # Daily structured logs
├── index/
│   ├── memory.db                   # SQLite search index (FTS5 + embeddings)
│   └── reindex.lock                # File lock for serialized writes
└── graphiti-venv/                  # Python virtualenv

~/.claude/projects/                 # Conversation archives (read-only)
└── */
    └── *.jsonl                     # Past session transcripts
```

### Search Pipeline

1. **FTS5 keyword search** — Fast exact matching via SQLite FTS5 (BM25 ranking)
2. **Vector similarity search** — Cosine similarity over 384-dim embeddings (brute-force, loaded into numpy)
3. **Reciprocal Rank Fusion** — Results from both backends merged with RRF (k=60)
4. **Post-filtering** — Date range, project, source type filters applied
5. **Deduplication** — Session results capped at 2 per conversation file
6. **Truncation** — Snippets cut at sentence boundaries

### Embedding on Write

When `memory_write` is called, the server:
1. Writes content to the target markdown file
2. Chunks and indexes via FTS5 (immediate keyword search coverage)
3. Generates embeddings via `all-MiniLM-L6-v2` and writes BLOBs to the `chunks` table (immediate vector search coverage)

No waiting for the Node.js reindexer — written memories are searchable via both backends immediately.

### Concurrent Access

Multiple Claude Code sessions each spawn their own MCP server process, all sharing the same SQLite database:

- **Write serialization** — File lock (`reindex.lock`) ensures only one process reindexes at a time
- **Graceful search degradation** — Vector and keyword search are wrapped independently; if one fails, the other still returns results
- **Busy timeout** — `busy_timeout = 5000` gives concurrent readers/writers 5 seconds to acquire locks
- **Graceful shutdown** — SIGTERM/SIGINT handlers checkpoint the WAL and close cleanly

### Indexing

- Curated memory files are chunked by markdown headings
- Conversation archives are parsed into exchange-level chunks (user/assistant pairs)
- Only main session files (`<uuid>.jsonl`) are indexed; agent subagent files are skipped
- Embeddings are generated locally using `all-MiniLM-L6-v2` (384-dim, ONNX runtime)
- Index staleness is checked via file modification times — reindexing only processes changed files
- Embedding cache table avoids re-embedding unchanged content on reindex

**Automatic indexing** is handled two ways:
1. A **SessionEnd hook** (`memory-reindex.py`) fires asynchronously after each Claude Code session
2. A **cron job** (`memory-reindex`) runs every 30 minutes as a catch-all

Manual reindex: `node dist/reindex-cli.js`

## Tools Reference

### memory_search

Search memories using hybrid keyword + vector search.

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
| `from_line` | number | 1 | Starting line number (1-based) |
| `lines` | number | 0 | Number of lines to return (0 = all) |

### memory_write

Write to memory files with immediate indexing and embedding.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `content` | string | (required) | Content to write |
| `file` | string | "memory/YYYY-MM-DD.md" | Target file (MEMORY.md or memory/*.md) |
| `append` | boolean | true | Append to file or overwrite |

### get_status

Health check for both backends. Returns chunk counts, vector counts, model info.

## Database Doctor

A built-in diagnostic and repair tool for the search index.

```bash
# Diagnose (read-only)
node dist/doctor-cli.js

# Diagnose and repair
node dist/doctor-cli.js --fix
```

**Checks:** chunk/file/vector row counts, FTS5 integrity, cross-table consistency, WAL size, stale processes, stale locks.

**Repairs (with `--fix`):** Rebuilds FTS5 and vec0 tables from source data, checkpoints WAL, removes stale locks.

## Development

```bash
npm install          # Install Node.js dependencies
npm run build        # Build indexer + doctor CLI
npm run typecheck    # Type checking
npm test             # Run integration tests
```

## Tech Stack

**MCP Server (Python):**
- [FastMCP](https://github.com/modelcontextprotocol/python-sdk) — MCP server framework
- [sentence-transformers](https://www.sbert.net/) — Local embedding generation (all-MiniLM-L6-v2)
- SQLite (stdlib) — FTS5 keyword search + embedding BLOB storage

**Indexer (Node.js):**
- [better-sqlite3](https://github.com/JoshuaWise/better-sqlite3) — SQLite with WAL mode
- [sqlite-vec](https://github.com/asg017/sqlite-vec) — ANN vector index (vec0)
- [Xenova/transformers.js](https://github.com/xenova/transformers.js) — ONNX embedding generation
- [esbuild](https://esbuild.github.io/) — Single-file bundle

## License

MIT
