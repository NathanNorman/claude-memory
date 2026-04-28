# claude-memory

A persistent memory system for Claude Code, implemented as an MCP server. Gives Claude long-term recall across sessions by indexing curated notes, thousands of past conversation archives, and external codebases with hybrid keyword + vector search.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MCP](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io/)

## The Problem

Claude Code sessions are stateless. Every new conversation starts from scratch. You end up re-explaining context that Claude already helped you figure out last week.

## How It Works

claude-memory runs as an MCP server that Claude Code connects to automatically. It provides 13 tools across four categories:

**Search & retrieval:**
- **`memory_search`** -- Hybrid FTS5 keyword + vector cosine similarity, merged via Reciprocal Rank Fusion (k=60)
- **`memory_deep_search`** -- 2-pass multi-hop retrieval: standard search, then entity extraction from top results seeds an expanded search
- **`codebase_search`** -- Semantic search over indexed source code repositories

**Code intelligence:**
- **`symbol_search`** -- Find class/function/method definitions across indexed codebases (SQL LIKE patterns)
- **`graph_traverse`** -- Walk upstream (callers) or downstream (callees) through the call graph
- **`community_search`** -- Identify tightly-coupled file clusters via Louvain community detection
- **`dependency_search`** -- Query cross-repo dependency edges (repo_depends_on / repo_depended_on_by)
- **`entity_browse`** -- List extracted entities (tools, projects, people) with occurrence counts
- **`entity_graph`** -- Explore entity co-occurrence neighborhoods at depth 1-2

**Read & write:**
- **`memory_read`** -- Read specific memory files or retrieve full past conversations by session UUID
- **`memory_write`** -- Append to daily logs or long-term memory files, with immediate FTS5 indexing and vector embedding
- **`index_session`** -- Index a conversation session JSONL file (called by SessionEnd hook)

**Health:**
- **`get_status`** -- Health check for both search backends with chunk/vector counts and model info

All data stays local in `~/.claude-memory/`. No external API calls for search. Embeddings are generated locally using three models: `bge-base-en-v1.5` (768-dim) for memory search, `nomic-embed-text-v1.5` (768-dim) for codebase indexing, and `all-MiniLM-L6-v2` (384-dim) for the Node.js batch indexer. Optional TurboQuant 4-bit quantization provides 8x storage compression with >=0.998 recall@10.

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

Claude Code will automatically have access to all 13 tools. No additional configuration needed.

## Architecture

The system has three subsystems: a **Python MCP server** (runtime), a **Node.js indexer** (batch), and a **webhook pipeline** (real-time remote indexing). All three share a single SQLite database in WAL mode.

```
~/claude-memory/                        # This repo (source code)
├── src/
│   ├── unified_memory_server.py        # Python MCP server (runtime, 13 tools)
│   ├── server.ts                       # Node.js MCP server entry point
│   ├── tools.ts                        # MCP tool handlers + Zod schemas
│   ├── types.ts                        # Shared TypeScript types
│   │
│   │  # Search
│   ├── search.ts                       # Search orchestration (keyword + vector)
│   ├── hybrid.ts                       # FTS5 query building, BM25 scoring, RRF merge
│   ├── db.ts                           # SQLite operations, migrations
│   ├── embeddings.ts                   # Embedding generation (ONNX, transformers.js)
│   ├── quantize.py                     # TurboQuant 4-bit quantization (WHT + Lloyd-Max)
│   │
│   │  # Chunking
│   ├── chunker.ts                      # Exchange-aware conversation chunking
│   ├── semantic-chunker.ts             # Boundary scoring + variance-minimizing DP
│   ├── semantic-markdown-chunker.ts    # 3-stage markdown chunking pipeline
│   ├── llm-boundary-scorer.ts          # LLM-based scoring (coprime windows 16, 11)
│   ├── llm-client.ts                   # OpenAI-compatible LLM client
│   ├── code_chunker.py                 # Code-aware chunking (AST/regex/size-based)
│   ├── conversation-parser.ts          # JSONL -> structured exchange pairs
│   │
│   │  # Code intelligence
│   ├── ast_parser.py                   # tree-sitter (Java/Kotlin/TS) + ast (Python)
│   ├── import_resolver.py              # Import string -> file path resolution
│   ├── call_resolver.py                # 6-strategy call resolution cascade
│   ├── scip_parser.py                  # Optional SCIP indexer integration (Tier 2)
│   ├── build_parser.py                 # Gradle/Maven/pip/npm dependency extraction
│   │
│   │  # Webhook pipeline
│   ├── webhook_server.py               # FastAPI webhook receiver (HMAC-SHA256)
│   ├── job_queue.py                    # SQLite-backed job queue with deduplication
│   ├── index_worker.py                 # Background worker (bare mirror indexing)
│   ├── mirror_manager.py               # Bare git clone/fetch management
│   ├── poll_repos.py                   # Polling fallback (git ls-remote cron)
│   │
│   │  # Tools
│   ├── doctor-cli.ts                   # Database diagnostics and repair
│   ├── reindex-cli.ts                  # Batch reindexing CLI
│   ├── indexer.ts                      # File scanning, staleness detection
│   ├── integration.test.ts             # Integration tests
│   └── prompts/                        # LLM scoring prompts
│       ├── boundary-score-system.txt
│       └── boundary-score-user.txt
│
├── scripts/
│   ├── codebase-index.py               # External codebase indexer
│   ├── index_session.py                # Real-time session indexer (SessionEnd hook)
│   ├── conversation_parser.py          # JSONL conversation parser (Python)
│   ├── cross_repo_deps.py              # Cross-repo dependency graph builder
│   ├── build-reference-db.py           # Addon reference database builder
│   ├── migrate_to_quantized.py         # TurboQuant sidecar file generation
│   ├── backfill_entity_relationships.py # Entity graph backfill
│   ├── backfill_signals.py             # Signal backfill utility
│   ├── bulk_index.py                   # Bulk indexing utility
│   ├── ingest_archive.py               # Archive ingestion
│   ├── summary_refinement.py           # LLM judge-refine summary loop
│   ├── summary_prompts.py              # Summary/judge/refiner prompts
│   ├── summary_llm.py                  # LLM client for summaries (claude CLI)
│   ├── start-webhook-server.sh         # Webhook server launcher
│   ├── index_missing_sessions.sh       # Catch-up indexing for missed sessions
│   ├── restore_pre_turboquant.sh       # Rollback script for quantization
│   └── test_*.py                       # Test files (14 test modules)
│
├── benchmarks/
│   ├── retrieval_bench.py              # Recall@5/10 benchmark harness
│   ├── corpus.json                     # 50-document synthetic corpus
│   ├── baseline.json                   # 2-signal baseline (R@5=0.680)
│   └── baseline-4signal.json           # 4-signal baseline (R@5=0.777)
│
└── unified-mcp-launcher.sh             # MCP server launcher

~/.claude-memory/                       # Runtime data directory
├── MEMORY.md                           # Long-term curated knowledge
├── memory/
│   └── YYYY-MM-DD.md                   # Daily structured logs
├── index/
│   ├── memory.db                       # SQLite search index (FTS5 + embeddings)
│   ├── reindex.lock                    # File lock for serialized writes
│   ├── packed_vectors.bin              # TurboQuant 4-bit sidecar (optional)
│   ├── rerank_matrix.f32              # Float32 rerank sidecar (optional)
│   └── quantization.json              # Quantization metadata (optional)
├── mirrors/                            # Bare git clones (webhook pipeline)
├── conversation-archive/               # JSONL backups (rsync'd every 30min)
├── backups/                            # Daily DB backups
└── graphiti-venv/                      # Python virtualenv
```

### Search Pipeline

1. **FTS5 keyword search** -- Fast exact matching via SQLite FTS5 (BM25 ranking)
2. **Vector similarity search** -- Three-stage quantized search: binary Hamming coarse pass (top 1,000), 4-bit TurboQuant dot products (top 50), float32 mmap exact rerank (top k)
3. **Reciprocal Rank Fusion** -- Results from both backends merged with RRF (k=60)
4. **Post-filtering** -- Date range, project, source type filters applied
5. **Deduplication** -- Session results capped at 2 per conversation file
6. **Truncation** -- Snippets cut at sentence boundaries

### Chunking Strategies

**Curated memory files** use a 3-stage semantic markdown chunking pipeline:
1. **Parse** -- Split markdown into 7 atomic unit types (headings, paragraphs, code blocks, lists, tables, thematic breaks, frontmatter)
2. **Score boundaries** -- Heuristic scoring based on heading level changes, topic transitions, content type shifts, blank lines
3. **Segment** -- Variance-minimizing dynamic programming to find optimal chunk boundaries (minChunkTokens=100, maxChunkTokens=2000, varianceWeight=0.3)

**Conversation archives** use exchange-aware chunking:
- JSONL files are parsed into user/assistant exchange pairs
- Boundary scoring uses 7 signals: topic shift phrases (+1.5), file path shifts (+1.0), time gaps (+0.5/+1.0), tool type shifts (+0.5), read-write transitions (+0.5), user questions (+0.25)
- Optional LLM-based scoring via coprime windows (sizes 16 and 11, gcd=1) with per-pair caching
- Same variance-minimizing DP segments exchanges into coherent topic-based chunks

**Source code** (via codebase indexer) uses language-aware chunking:
- Python: AST-based (functions, classes via stdlib `ast`)
- TypeScript/JavaScript: tree-sitter (class, function, interface, enum, arrow function declarations)
- Java/Kotlin: Regex-based (class/interface/method declarations)
- Shell: Function declaration splitting
- Other files: Size-based splitting at blank-line boundaries

### Embedding on Write

When `memory_write` is called, the server:
1. Writes content to the target markdown file
2. Chunks and indexes via FTS5 (immediate keyword search coverage)
3. Generates embeddings via `bge-base-en-v1.5` (768-dim), quantizes to 4-bit, and writes to the `chunks` table (immediate vector search coverage)

No waiting for the Node.js reindexer -- written memories are searchable via both backends immediately.

### Code Intelligence

The code intelligence subsystem builds a call graph and type hierarchy from indexed codebases:

**AST extraction** (`ast_parser.py`): tree-sitter for Java, Kotlin, and TypeScript; stdlib `ast` for Python. Extracts imports (with type classification), symbol declarations (classes, interfaces, functions, methods with line numbers), call sites, and type hierarchy (extends, implements, delegation).

**Call resolution** (`call_resolver.py`): A 6-strategy cascade resolves each extracted call site to a target symbol, short-circuiting on first match:

| Priority | Strategy | Confidence |
|----------|----------|------------|
| 1 | Import-map exact match | 0.95 |
| 2 | Import-map suffix fallback | 0.85 |
| 3 | Same-module prefix match | 0.90 |
| 4 | Unique name project-wide | 0.75 |
| 5 | Suffix + directory distance | 0.55 |
| 6 | Fuzzy string similarity | 0.30-0.40 |

**SCIP integration** (`scip_parser.py`): Optional Tier 2 indexing via scip-java, scip-typescript, or scip-python. SCIP edges (0.95 confidence) replace tree-sitter edges for the same source/target file pair.

**Cross-repo dependencies** (`cross_repo_deps.py` + `build_parser.py`): Parses Gradle KTS/Groovy (including version catalog TOML), Maven (with property interpolation), pyproject.toml, requirements.txt, and package.json into `repo_dependency` edges.

### Webhook Pipeline

For repositories on GitHub rather than the local machine, the webhook pipeline provides push-triggered incremental indexing:

1. GitHub push fires a webhook to `webhook_server.py` (FastAPI, HMAC-SHA256 verified)
2. Job enqueued to a SQLite-backed queue with deduplication (rapid pushes to the same repo coalesce into one job)
3. Background worker claims job, fetches bare git mirror, computes diff
4. Only changed files are re-chunked and re-embedded
5. Performance target: under 1 second per job

Polling fallback via `poll_repos.py` checks tracked repos via `git ls-remote` and enqueues jobs when remote HEAD changes.

### Iterative Summary Refinement

Conversation sessions can be automatically summarized using an LLM judge-refine loop:
1. **Summarize** -- Generate initial summary from conversation transcript
2. **Judge** -- Score summary on 6 dimensions (decisions/rationale, identifiers/configs, approaches tried, file references, correctness, structure) on a 0-10 scale
3. **Refine** -- If score < threshold (default 8.0), refine with judge feedback and re-score
4. **Store** -- Final summary saved to `files.summary` column for search result enrichment

Controlled via `MEMORY_SUMMARY_ENABLED=1` and `MEMORY_SUMMARY_MODEL` env vars.

### Codebase Indexing

External repositories can be indexed for semantic search:

```bash
# Full index
python3 scripts/codebase-index.py --path ~/my-repo --name my-repo

# Incremental update (only changed files)
python3 scripts/codebase-index.py --path ~/my-repo --name my-repo --update

# Low-impact mode (throttled, nice'd)
python3 scripts/codebase-index.py --path ~/my-repo --name my-repo --throttle

# List indexed codebases
python3 scripts/codebase-index.py --list

# Remove
python3 scripts/codebase-index.py --remove --name my-repo
```

Codebase chunks are stored in the main `chunks` table with `file_path` prefixed by `codebase:<name>/`. A `PreToolUse:Write` hook surfaces similar existing code when creating new source files, preventing duplicate implementations.

### Addon Reference Databases

Skills and plugins can ship pre-built `.db` files containing searchable reference material. The server discovers these at startup and makes them searchable via `memory_search(source="<name>")`.

```bash
# Build from a directory of markdown/text files
python3 scripts/build-reference-db.py ./my-docs/ -o my-skill.db
```

### Concurrent Access

Multiple Claude Code sessions each spawn their own MCP server process, all sharing the same SQLite database:

- **Write serialization** -- File lock (`reindex.lock`) ensures only one process reindexes at a time
- **Graceful search degradation** -- Vector and keyword search are wrapped independently; if one fails, the other still returns results
- **Busy timeout** -- `busy_timeout = 5000` gives concurrent readers/writers 5 seconds to acquire locks
- **Graceful shutdown** -- SIGTERM/SIGINT handlers checkpoint the WAL and close cleanly

### Indexing

- Curated memory files are chunked using the semantic markdown chunker (parse -> score -> DP segmentation)
- Conversation archives are parsed into exchange-aware chunks with boundary scoring
- Only main session files (`<uuid>.jsonl`) are indexed; agent subagent files are skipped
- Conversation chunks are **never pruned** -- even after Claude Code deletes the original JSONL, the indexed content survives
- Embeddings are generated locally (ONNX runtime for Node.js, sentence-transformers for Python)
- TurboQuant 4-bit quantization compresses embeddings 8x with >=0.998 recall@10
- Index staleness is checked via file modification times -- reindexing only processes changed files
- Embedding cache table avoids re-embedding unchanged content on reindex

**Automatic indexing** is handled three ways:
1. A **SessionEnd hook** (`index_session` MCP tool) indexes each session immediately with FTS5; embeddings are filled lazily on next server warmup
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

### memory_deep_search

2-pass multi-hop search with entity expansion. Same parameters as `memory_search`. Pass 1 runs standard hybrid search. Pass 2 extracts entities (tools, projects, people) from top results and searches for those entities via keyword + entity overlap (skips vector + temporal to save ~500ms).

### codebase_search

Search indexed codebases for existing implementations.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | (required) | Search query (e.g., "manifest discovery") |
| `codebase` | string | "" | Filter to a specific codebase name, or "" for all |
| `maxResults` | number | 10 | Maximum results to return |

### symbol_search

Find symbol definitions (classes, functions, methods) across indexed codebases.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern` | string | (required) | SQL LIKE pattern (e.g., "%PaymentService%") |
| `codebase` | string | "" | Filter to a specific codebase |
| `kind` | string | "" | Filter by symbol kind: "class", "function", "method", etc. |

### graph_traverse

Walk the call graph upstream (callers) or downstream (callees) from a file.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file_path` | string | (required) | File path within the codebase |
| `direction` | string | "downstream" | "upstream" (callers) or "downstream" (callees) |
| `depth` | number | 1 | Traversal depth (1-3) |

### community_search

Find the cluster of tightly-coupled files around a given file using Louvain community detection.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file_path` | string | (required) | File path to find the community for |

### dependency_search

Query cross-repo build dependency edges.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `codebase` | string | (required) | Codebase name |
| `direction` | string | "imports" | "imports" (what this repo depends on) or "imported_by" (what depends on this repo) |

### entity_browse

List entities extracted from indexed content, ranked by occurrence count.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `entity_type` | string | "" | Filter by type: "tool", "project", "person", or "" for all |
| `limit` | number | 50 | Maximum entities to return |

### entity_graph

Explore entity co-occurrence neighborhoods.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `entity` | string | (required) | Entity value to explore |
| `depth` | number | 1 | Neighborhood depth (1-2) |

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

## Retrieval Benchmarks

A synthetic corpus of 50 documents and 50 queries across four categories measures recall:

| Configuration | R@5 | R@10 |
|---------------|-----|------|
| 2-signal (keyword + vector) | 0.680 | 0.786 |
| 4-signal (+ temporal + entity) | **0.777** | **0.858** |

| Category | 2-signal R@5 | 4-signal R@5 | Delta |
|----------|--------------|--------------|-------|
| entity | 0.896 | 1.000 | +10.4pp |
| general | 0.833 | 0.833 | +0.0pp |
| multi-hop | 0.463 | 0.642 | +17.9pp |
| temporal | 0.563 | 0.655 | +9.2pp |

Run benchmarks: `python3 benchmarks/retrieval_bench.py`

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
npm run build        # Build indexer + doctor CLI (esbuild bundles)
npm run typecheck    # TypeScript type checking
npm test             # tsc compile + integration tests (node --test)

# Python tests
python3 -m pytest scripts/test_*.py -v
```

## Tech Stack

**MCP Server (Python):**
- [FastMCP](https://github.com/modelcontextprotocol/python-sdk) -- MCP server framework
- [sentence-transformers](https://www.sbert.net/) -- Local embedding generation (bge-base-en-v1.5 768-dim, nomic-embed-text-v1.5 768-dim)
- SQLite (stdlib) -- FTS5 keyword search + embedding BLOB storage
- TurboQuant -- 4-bit vector quantization with Walsh-Hadamard rotation + Lloyd-Max codebook

**Indexer (Node.js):**
- [better-sqlite3](https://github.com/JoshuaWise/better-sqlite3) -- SQLite with WAL mode
- [sqlite-vec](https://github.com/asg017/sqlite-vec) -- ANN vector index (vec0)
- [Xenova/transformers.js](https://github.com/xenova/transformers.js) -- ONNX embedding generation (all-MiniLM-L6-v2 384-dim)
- [esbuild](https://esbuild.github.io/) -- Single-file bundle

**Webhook Pipeline (Python):**
- [FastAPI](https://fastapi.tiangolo.com/) -- Webhook receiver with HMAC-SHA256 verification
- Bare git mirrors -- No working copies, reads via `git show`
- SQLite job queue -- Deduplication, atomic claims via `BEGIN IMMEDIATE`

**Code Intelligence (Python):**
- [tree-sitter](https://tree-sitter.github.io/) -- AST extraction for Java, Kotlin, TypeScript
- [SCIP](https://sourcegraph.com/docs/code-intelligence/scip) -- Optional compiler-grade indexing (Tier 2)
- 6-strategy call resolution cascade (0.95 to 0.30 confidence)

**Chunking & Scoring:**
- Semantic markdown chunker -- Parse -> boundary score -> variance-minimizing DP segmentation
- Exchange-aware conversation chunker -- 7 boundary signals, coprime LLM scoring windows
- Code chunker -- AST (Python), tree-sitter (TS/JS), regex (Java/Kotlin/Shell), size-based (other)

## License

MIT
