# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

claude-memory is a persistent memory system for Claude Code, implemented as an MCP server. It has two components:

1. **Python MCP server** (`src/unified_memory_server.py`) — The runtime that Claude Code connects to. Handles search, read, write, and status tools via stdio. This is what runs in production.
2. **Node.js indexer** (`src/indexer.ts` and friends) — Batch indexer that scans conversation archives, chunks content, generates embeddings, and maintains the SQLite search index. Runs on-demand via reindex.

## Build & Development Commands

```bash
# Node.js indexer
npm install          # Install dependencies
npm run build        # Build MCP server + doctor CLI (esbuild, single-file ESM bundles → dist/)
npm run build:doctor # Build doctor CLI only
npm run typecheck    # TypeScript type checking (tsc --noEmit)
npm test             # tsc compile + integration tests (node --test)

# Reindex (rebuild search index from memory files + conversation archives)
npx tsc && node dist/reindex-cli.js   # Backs up DB to ~/.claude-memory/backups/ before reindexing

# Python MCP server (uses venv at ~/.claude-memory/graphiti-venv/)
~/.claude-memory/graphiti-venv/bin/python3 src/unified_memory_server.py  # Run directly
python3 -c "import py_compile; py_compile.compile('src/unified_memory_server.py', doraise=True)"  # Syntax check

# Database diagnostics
node dist/doctor-cli.js          # Diagnose (read-only)
node dist/doctor-cli.js --fix    # Diagnose and repair
```

**Dual build system**: `npm run build` uses esbuild to create single-file bundles (`dist/server.js`, `dist/doctor-cli.js`). `tsc` (via `npm test` or `npx tsc`) compiles all src/ files individually to `dist/` — this is how `reindex-cli.js` and `integration.test.js` get built. The esbuild bundles are for MCP/CLI distribution; tsc output is for tests and one-off scripts.

Tests use Node.js native test runner (`node:test`). Single test file: `src/integration.test.ts` → `dist/integration.test.js`. No way to run individual tests.

## Architecture

### Runtime flow (Python MCP server)

```
unified-mcp-launcher.sh
  → unified_memory_server.py (FastMCP, stdio transport)
    ├── FlatSearchBackend    — FTS5 keyword search (SQLite)
    ├── VectorSearchBackend  — Brute-force cosine similarity (numpy over embedding BLOBs)
    └── RRF merge            — Reciprocal Rank Fusion (k=60) combines both result sets
```

Four MCP tools: `memory_search`, `memory_read`, `memory_write`, `get_status`.

On `memory_write`: content is written to disk, chunked and indexed into FTS5, then embedded via `sentence-transformers` (all-MiniLM-L6-v2) and stored as BLOBs — all synchronously. Written memories are searchable via both backends immediately.

### Indexing flow (Node.js)

```
reindex-cli.ts (CLI entry point)
  → indexer.ts (file scanning, staleness detection)
    → chunker.ts (markdown heading-aware + conversation exchange-aware)
    → embeddings.ts (Xenova/transformers.js, ONNX, all-MiniLM-L6-v2)
    → conversation-parser.ts (JSONL → structured exchanges)
    → db.ts (SQLite writes: chunks, chunks_fts, chunks_vec, embedding_cache)
```

**Indexing is triggered automatically two ways:**
1. **SessionEnd hook** (`~/.claude/hooks/memory-reindex.py`) — fires async (Popen, detached) after each session
2. **claude-cron job** (`memory-reindex`, every 30 min) — catch-all for missed sessions

The indexer is incremental: mtime check first, then content hash. Already-indexed files skip in O(1).

**Note:** Only main session files (`<uuid>.jsonl`) are indexed. Agent subagent files (`agent-*.jsonl` in `<uuid>/subagents/`) are intentionally skipped (line 133 of `indexer.ts`).

### Data directory (`~/.claude-memory/`)

Not in this repo — runtime data only:
- `MEMORY.md` — curated long-term knowledge
- `memory/YYYY-MM-DD.md` — daily structured logs
- `index/memory.db` — SQLite search index (FTS5 + embedding BLOBs, WAL mode)
- `graphiti-venv/` — Python virtualenv (sentence-transformers, torch, numpy, mcp)

### Database schema (SQLite, WAL mode)

- `chunks` — main content table (id, file_path, chunk_index, start/end_line, title, content, embedding BLOB, hash, updated_at)
- `files` — indexed file tracking (file_path, content_hash, last_indexed, chunk_count, summary). The `summary` column was added via migration in `db.ts` and stores episodic-memory summaries for conversations.
- `chunks_fts` — FTS5 virtual table (content, title; content-sync'd to chunks)
- `chunks_vec` — vec0 virtual table (embedding float[384]) — written by Node.js indexer
- `embedding_cache` — keyed by (provider, model, hash) → embedding BLOB
- `meta` — tracks embedding model version for invalidation

sqlite-vec requires `BigInt` for rowid values in vec0 operations.

### Conversation preservation

Conversation chunks are **never pruned** from the index, even after Claude Code deletes the original `.jsonl` files. The index is the only surviving copy of that knowledge. Only curated memory files (`MEMORY.md`, `memory/*.md`) are pruned when removed from disk. This is enforced in `indexer.ts:indexAll()` — the prune loop skips any path starting with `conversations/`.

### Python scripts (`scripts/`)

Standalone Python utilities, not part of the MCP server or Node.js indexer:
- `conversation_parser.py` / `shared.py` — JSONL conversation parsing (Python equivalent of `src/conversation-parser.ts`)
- `ingest_session.py` — One-off session ingestion script
- `test_conversation_parser.py` — Tests for the Python parser

## Key Design Decisions

- **Hybrid search with RRF**: Vector (cosine similarity) and keyword (FTS5 BM25) results merged via Reciprocal Rank Fusion (k=60) rather than weighted-sum scoring. Avoids suppressing keyword-only results below thresholds.
- **Two embedding paths**: Node.js indexer uses Xenova/transformers.js (ONNX). Python server uses sentence-transformers (PyTorch). Same model (`all-MiniLM-L6-v2`), same 384-dim output, compatible embeddings.
- **Python reads BLOBs, not vec0**: The Python server loads all embedding BLOBs into a numpy matrix for brute-force cosine sim. The `chunks_vec` (vec0) table exists but is only written/queried by the Node.js side. This is a pragmatic workaround — `sqlite-vec` Python bindings don't load on this platform.
- **Exchange-aware chunking**: Conversation archives are chunked at exchange boundaries — user/assistant pairs are never split across chunks.
- **Embedding cache**: Table `embedding_cache` avoids re-embedding unchanged content across reindexes.
- **Mtime-based staleness**: `isIndexStale()` compares file mtimes against DB timestamps — O(file count) not O(DB size).

## Concurrency

Multiple Claude Code sessions share the same SQLite database:

- **Write lock**: `reindex.lock` file (O_CREAT|O_EXCL) serializes reindexing. Stale locks (>5 min) auto-reclaimed.
- **Search degradation**: Vector and keyword search are wrapped independently — if one backend fails, the other still returns results.
- **Safe virtual table writes**: FTS5/vec0 operations in `insertChunk()` and `deleteChunksByFile()` are individually try/caught.
- **busy_timeout**: 5000ms for concurrent readers/writers.
- **Graceful shutdown**: SIGTERM/SIGINT → checkpoint WAL → close DB.

## Logging

- **Python server**: `logging` module to stderr. Log prefix: `[unified-memory]`.
- **Node.js**: `process.stderr.write`. Stdout is reserved for MCP JSON-RPC protocol messages.
