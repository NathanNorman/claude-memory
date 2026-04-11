# claude-memory

A persistent memory system for Claude Code, implemented as an MCP server. Gives Claude long-term recall across sessions by indexing curated notes, thousands of past conversation archives, and external codebases with hybrid keyword + vector search.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MCP](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io/)

## The Problem

Claude Code sessions are stateless. Every new conversation starts from scratch — no memory of past debugging sessions, architecture decisions, or learned preferences. You end up re-explaining context that Claude already helped you figure out last week.

## How It Works

claude-memory runs as an MCP server that Claude Code connects to automatically. It provides five tools:

- **`memory_search`** — Hybrid search combining FTS5 keyword matching and vector similarity (cosine) across curated notes, conversation archives, and indexed codebases, merged via Reciprocal Rank Fusion
- **`codebase_search`** — Semantic search over indexed source code repositories, surfacing existing implementations before you write duplicates
- **`memory_read`** — Read specific memory files or retrieve full past conversations by session UUID
- **`memory_write`** — Append to daily logs or long-term memory files, with immediate FTS5 indexing and vector embedding generation
- **`get_status`** — Health check for both search backends with chunk/vector counts

All data stays local in `~/.claude-memory/`. No external API calls for search. Embeddings are generated locally using `bge-base-en-v1.5` (768-dim) via [sentence-transformers](https://www.sbert.net/), with optional TurboQuant 4-bit quantization for 8x storage compression.

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

Claude Code will automatically have access to all five tools. No additional configuration needed.

## Architecture

The system has two components: a **Node.js indexer** that builds and maintains the search index, and a **Python MCP server** that handles search queries and writes.

```
~/claude-memory/                    # This repo (source code)
├── src/
│   ├── unified_memory_server.py    # Python MCP server (runtime)
│   ├── code_chunker.py             # Code-aware chunking (Python/Java/Kotlin/Shell)
│   ├── indexer.ts                  # Node.js batch indexer
│   ├── semantic-markdown-chunker.ts # 3-stage semantic markdown chunking
│   ├── llm-boundary-scorer.ts      # LLM-based conversation boundary scoring
│   ├── llm-client.ts               # OpenAI-compatible LLM client
│   ├── embeddings.ts               # Embedding generation (ONNX)
│   ├── chunker.ts                  # Exchange-aware conversation chunking
│   ├── search.ts                   # Search implementation
│   ├── doctor-cli.ts               # Database diagnostics
│   └── prompts/                    # LLM scoring prompts
│       ├── boundary-score-system.txt
│       └── boundary-score-user.txt
├── scripts/
│   ├── codebase-index.py           # External codebase indexer
│   ├── index_session.py            # Real-time session indexer (SessionEnd hook)
│   ├── conversation_parser.py      # JSONL conversation parser
│   ├── summary_refinement.py       # LLM judge-refine summary loop
│   ├── summary_prompts.py          # Summary/judge/refiner prompts
│   └── summary_llm.py             # LLM client for summaries (claude CLI)
└── unified-mcp-launcher.sh         # MCP server launcher

~/.claude-memory/                   # Runtime data directory
├── MEMORY.md                       # Long-term curated knowledge
├── memory/
│   └── YYYY-MM-DD.md               # Daily structured logs
├── index/
│   ├── memory.db                   # SQLite search index (FTS5 + embeddings)
│   └── reindex.lock                # File lock for serialized writes
├── conversation-archive/           # JSONL backups (rsync'd every 30min)
├── backups/                        # Daily DB backups
└── graphiti-venv/                  # Python virtualenv

~/.claude/projects/                 # Conversation archives (read-only)
└── */
    └── *.jsonl                     # Past session transcripts
```

### Search Pipeline

1. **FTS5 keyword search** — Fast exact matching via SQLite FTS5 (BM25 ranking)
2. **Vector similarity search** — Two-stage quantized search: approximate shortlist via 4-bit packed dot products, then exact reranking of top-30 candidates from dequantized vectors
3. **Reciprocal Rank Fusion** — Results from both backends merged with RRF (k=60)
4. **Post-filtering** — Date range, project, source type filters applied
5. **Deduplication** — Session results capped at 2 per conversation file
6. **Truncation** — Snippets cut at sentence boundaries

### Chunking Strategies

**Curated memory files** use a 3-stage semantic markdown chunking pipeline:
1. **Parse** — Split markdown into atomic units (headings, paragraphs, code blocks, lists)
2. **Score boundaries** — Heuristic scoring based on heading level changes, topic transitions, code/prose boundaries
3. **Segment** — Dynamic programming (variance-minimizing DP) to find optimal chunk boundaries within token budget

**Conversation archives** use exchange-aware chunking:
- JSONL files are parsed into user/assistant exchange pairs
- Boundary scoring uses heuristics (or optionally LLM-based scoring via `--llm-scoring`)
- Same variance-minimizing DP segments exchanges into coherent topic-based chunks

**Source code** (via codebase indexer) uses language-aware chunking:
- Python: AST-based (functions, classes)
- Java/Kotlin: Regex-based (class/interface/method declarations)
- Shell: Function declaration splitting
- Other files: Size-based splitting at blank-line boundaries

### Embedding on Write

When `memory_write` is called, the server:
1. Writes content to the target markdown file
2. Chunks and indexes via FTS5 (immediate keyword search coverage)
3. Generates embeddings via `bge-base-en-v1.5` (768-dim), quantizes to 4-bit, and writes to the `chunks` table (immediate vector search coverage)

No waiting for the Node.js reindexer — written memories are searchable via both backends immediately.

### Iterative Summary Refinement

Conversation sessions can be automatically summarized using an LLM judge-refine loop:
1. **Summarize** — Generate initial summary from conversation transcript
2. **Judge** — Score summary on 6 dimensions (accuracy, completeness, conciseness, structure, actionability, context) on a 0-10 scale
3. **Refine** — If score < threshold (default 8.0), refine with judge feedback and re-score
4. **Store** — Final summary saved to `files.summary` column for search result enrichment

Controlled via `MEMORY_SUMMARY_ENABLED=1` and `MEMORY_SUMMARY_MODEL` env vars.

### Codebase Indexing

External repositories can be indexed for semantic search:

```bash
# Full index
python3 scripts/codebase-index.py --path ~/my-repo --name my-repo

# Incremental update (only changed files)
python3 scripts/codebase-index.py --path ~/my-repo --name my-repo --update

# List indexed codebases
python3 scripts/codebase-index.py --list

# Remove
python3 scripts/codebase-index.py --remove --name my-repo
```

Codebase chunks are stored in the main `chunks` table with `file_path` prefixed by `codebase:<name>/`. A `PreToolUse:Write` hook surfaces similar existing code when creating new source files, preventing duplicate implementations.

### Concurrent Access

Multiple Claude Code sessions each spawn their own MCP server process, all sharing the same SQLite database:

- **Write serialization** — File lock (`reindex.lock`) ensures only one process reindexes at a time
- **Graceful search degradation** — Vector and keyword search are wrapped independently; if one fails, the other still returns results
- **Busy timeout** — `busy_timeout = 5000` gives concurrent readers/writers 5 seconds to acquire locks
- **Graceful shutdown** — SIGTERM/SIGINT handlers checkpoint the WAL and close cleanly

### Indexing

- Curated memory files are chunked using the semantic markdown chunker (parse → score → DP segmentation)
- Conversation archives are parsed into exchange-aware chunks with boundary scoring
- Only main session files (`<uuid>.jsonl`) are indexed; agent subagent files are skipped
- Conversation chunks are **never pruned** — even after Claude Code deletes the original JSONL, the indexed content survives
- Embeddings are generated locally using `bge-base-en-v1.5` (768-dim, ONNX runtime for Node.js, sentence-transformers for Python)
- TurboQuant 4-bit quantization compresses embeddings 8x with ≥0.998 recall@10
- Index staleness is checked via file modification times — reindexing only processes changed files
- Embedding cache table avoids re-embedding unchanged content on reindex

**Automatic indexing** is handled three ways:
1. A **SessionEnd hook** (`index_session` MCP tool) indexes each session immediately with embeddings
2. A **cron job** (`memory-reindex`) runs every 30 minutes as a catch-all for missed sessions
3. A **conversation backup** cron (`conversation-backup`) rsyncs raw JSONL files every 30 minutes to `~/.claude-memory/conversation-archive/` before Claude Code can prune them

Manual reindex: `npx tsc && node dist/reindex-cli.js`

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
| `source` | string | "" | "curated", "conversations", "codebase", or "" for all |

### codebase_search

Search indexed codebases for existing implementations.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | (required) | Search query (e.g., "manifest discovery") |
| `codebase` | string | "" | Filter to a specific codebase name, or "" for all |
| `maxResults` | number | 10 | Maximum results to return |

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

### index_session

Index a conversation session JSONL file (called by SessionEnd hook).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `session_path` | string | (required) | Absolute path to the session JSONL file |

### get_status

Health check for both backends. Returns chunk counts, vector counts, model info, quantization status.

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
npm test             # Run integration tests (28 tests)
```

## Tech Stack

**MCP Server (Python):**
- [FastMCP](https://github.com/modelcontextprotocol/python-sdk) — MCP server framework
- [sentence-transformers](https://www.sbert.net/) — Local embedding generation (bge-base-en-v1.5, 768-dim)
- SQLite (stdlib) — FTS5 keyword search + embedding BLOB storage
- TurboQuant — 4-bit vector quantization with rotation + k-means codebook

**Indexer (Node.js):**
- [better-sqlite3](https://github.com/JoshuaWise/better-sqlite3) — SQLite with WAL mode
- [sqlite-vec](https://github.com/asg017/sqlite-vec) — ANN vector index (vec0)
- [Xenova/transformers.js](https://github.com/xenova/transformers.js) — ONNX embedding generation
- [esbuild](https://esbuild.github.io/) — Single-file bundle

**Chunking & Scoring:**
- Semantic markdown chunker — Parse → boundary score → variance-minimizing DP segmentation
- Exchange-aware conversation chunker — Preserve user/assistant pairs across chunk boundaries
- LLM boundary scorer — Two-pass coprime windows (16, 11) with per-pair caching
- Code chunker — AST (Python), regex (Java/Kotlin/Shell), size-based (other)

## License

MIT
