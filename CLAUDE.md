# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

claude-memory is a persistent memory system for Claude Code, implemented as an MCP server. It has three main subsystems:

1. **Python MCP server** (`src/unified_memory_server.py`) — The runtime that Claude Code connects to. Handles search, read, write, and status tools via stdio. This is what runs in production.
2. **Node.js indexer** (`src/indexer.ts` and friends) — Batch indexer that scans conversation archives, chunks content, generates embeddings, and maintains the SQLite search index. Runs on-demand via reindex.
3. **Webhook pipeline** (`src/webhook_server.py`, `src/index_worker.py`, `src/mirror_manager.py`) — FastAPI server that receives GitHub push webhooks and incrementally re-indexes changed files via bare git mirrors.

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

# Python tests (pytest-style, run individually)
~/.claude-memory/graphiti-venv/bin/python3 -m pytest scripts/test_conversation_parser.py -v
~/.claude-memory/graphiti-venv/bin/python3 -m pytest scripts/test_code_chunker.py -v
~/.claude-memory/graphiti-venv/bin/python3 -m pytest scripts/test_build_parser.py -v
~/.claude-memory/graphiti-venv/bin/python3 -m pytest scripts/test_cross_repo_deps.py -v
~/.claude-memory/graphiti-venv/bin/python3 -m pytest scripts/test_turboquant_backend.py -v
# Or run all Python tests at once:
~/.claude-memory/graphiti-venv/bin/python3 -m pytest scripts/test_*.py -v

# Database diagnostics
node dist/doctor-cli.js          # Diagnose (read-only)
node dist/doctor-cli.js --fix    # Diagnose and repair

# Retrieval benchmarks
~/.claude-memory/graphiti-venv/bin/python3 benchmarks/retrieval_bench.py
```

**Dual build system**: `npm run build` uses esbuild to create single-file bundles (`dist/server.js`, `dist/doctor-cli.js`). `tsc` (via `npm test` or `npx tsc`) compiles all src/ files individually to `dist/` — this is how `reindex-cli.js` and `integration.test.js` get built. The esbuild bundles are for MCP/CLI distribution; tsc output is for tests and one-off scripts.

Node.js tests use the native test runner (`node:test`). Single test file: `src/integration.test.ts` → `dist/integration.test.js`. No way to run individual Node.js tests. There's also `test/integration.mjs` for additional integration testing.

## Architecture

### Runtime flow (Python MCP server)

```
unified-mcp-launcher.sh
  → unified_memory_server.py (FastMCP, stdio transport)
    ├── FlatSearchBackend    — FTS5 keyword search (SQLite)
    ├── VectorSearchBackend  — Brute-force cosine similarity (numpy over embedding BLOBs)
    ├── TurboQuantBackend    — Optional 3-stage quantized search (binary → 4-bit → float32)
    └── RRF merge            — Reciprocal Rank Fusion (k=60) combines both result sets
```

MCP tools: `memory_search`, `memory_deep_search`, `memory_read`, `memory_write`, `get_status`, `codebase_search`, `dependency_search`, `symbol_search`, `graph_traverse`, `community_search`, `entity_browse`, `entity_graph`, `index_session`.

On `memory_write`: content is written to disk, chunked and indexed into FTS5, then embedded via `sentence-transformers` and stored as BLOBs — all synchronously. Written memories are searchable via both backends immediately.

**Embedding models**: The Python MCP server docstring references `all-MiniLM-L6-v2` (384-dim). The README references `bge-base-en-v1.5` (768-dim). Check `unified_memory_server.py` model loading to confirm which is active — the README may reflect a planned or completed migration.

### Indexing flow (Node.js)

```
reindex-cli.ts (CLI entry point)
  → indexer.ts (file scanning, staleness detection)
    → chunker.ts (markdown heading-aware + conversation exchange-aware)
    → semantic-markdown-chunker.ts (3-stage: parse → boundary score → DP segmentation)
    → semantic-chunker.ts (conversation-specific: heuristic boundary → DP segmentation)
    → llm-boundary-scorer.ts (optional LLM-based scoring via --llm-scoring flag)
    → embeddings.ts (Xenova/transformers.js, ONNX)
    → conversation-parser.ts (JSONL → structured exchanges)
    → db.ts (SQLite writes: chunks, chunks_fts, chunks_vec, embedding_cache)
```

**Indexing is triggered automatically two ways:**
1. **SessionEnd hook** (`scripts/index_session.py`) — indexes the session immediately with FTS5; embeddings left NULL for lazy fill
2. **claude-cron job** (`memory-reindex`, every 30 min) — catch-all for missed sessions

The indexer is incremental: mtime check first, then content hash. Already-indexed files skip in O(1).

**Note:** Only main session files (`<uuid>.jsonl`) are indexed. Agent subagent files (`agent-*.jsonl` in `<uuid>/subagents/`) are intentionally skipped.

### Webhook pipeline

For remote repository indexing without local clones:

```
GitHub push event → webhook_server.py (FastAPI, HMAC-SHA256 verified)
  → job_queue.py (SQLite-backed queue with deduplication)
  → index_worker.py (background thread, claims jobs)
    → mirror_manager.py (bare git clone/fetch, diff, file read)
    → code_chunker.py + ast_parser.py (chunk and extract symbols)
    → embeddings written to chunks table
```

`poll_repos.py` provides a cron-compatible polling fallback — checks tracked repos via `git ls-remote` and enqueues jobs when remote HEAD changes. Config via `~/.claude-memory/webhook-config.json` or `TRACKED_REPOS` env var.

### Code intelligence subsystem

Compiler-grade code graph for `symbol_search`, `graph_traverse`, and `community_search` MCP tools:

- **`ast_parser.py`** — Tree-sitter (Java/Kotlin/TypeScript) and stdlib `ast` (Python) extraction of imports, symbol declarations, call sites, and type hierarchy (extends/implements)
- **`import_resolver.py`** — Maps import strings to file paths; handles monorepo layouts with multiple source roots
- **`call_resolver.py`** — 6-strategy cascade (import-map exact → suffix → same-module → unique-name → proximity → fuzzy) to resolve call sites to target symbols
- **`scip_parser.py`** — Optional Tier 2: runs SCIP indexers (scip-java, scip-typescript, scip-python) for high-confidence edges that replace tree-sitter results
- **`build_parser.py`** — Extracts dependencies from Gradle (KTS + Groovy), Maven, pip, and npm build files for `cross_repo_deps.py`

### Summary refinement pipeline

LLM judge-refine loop for conversation summaries (`scripts/`):

- **`summary_prompts.py`** — Prompts for summarize, judge (6 dimensions, 0-10 scale), and refine steps
- **`summary_llm.py`** — LLM client using `claude` CLI subprocess
- **`summary_refinement.py`** — Orchestrates: summarize → judge → refine loop until score ≥ threshold (default 8.0)

Controlled via env vars: `MEMORY_SUMMARY_ENABLED=1`, `MEMORY_SUMMARY_MODEL` (default `haiku`), `MEMORY_SUMMARY_THRESHOLD`, `MEMORY_SUMMARY_MAX_ITER`.

### Data directory (`~/.claude-memory/`)

Not in this repo — runtime data only:
- `MEMORY.md` — curated long-term knowledge
- `memory/YYYY-MM-DD.md` — daily structured logs
- `index/memory.db` — SQLite search index (FTS5 + embedding BLOBs, WAL mode)
- `mirrors/` — bare git clones managed by webhook pipeline
- `graphiti-venv/` — Python virtualenv (sentence-transformers, torch, numpy, mcp)

### Database schema (SQLite, WAL mode)

- `chunks` — main content table (id, file_path, chunk_index, start/end_line, title, content, embedding BLOB, hash, updated_at)
- `files` — indexed file tracking (file_path, content_hash, last_indexed, chunk_count, summary). The `summary` column stores episodic-memory summaries for conversations.
- `chunks_fts` — FTS5 virtual table (content, title; content-sync'd to chunks)
- `chunks_vec` — vec0 virtual table (embedding float[384]) — written by Node.js indexer
- `embedding_cache` — keyed by (provider, model, hash) → embedding BLOB
- `meta` — tracks embedding model version for invalidation
- `entity_relationships` — co-occurrence graph (entity_a, relation_type, entity_b, chunk_id, confidence)
- `codebase_meta` — per-file content hashes for incremental codebase updates
- `quantization_meta` — TurboQuant parameters (model_name, dims, bit_width, rotation_seed, codebook)

sqlite-vec requires `BigInt` for rowid values in vec0 operations.

### Node.js source modules (`src/`)

- **`server.ts`** — MCP server entry point (esbuild bundle target)
- **`tools.ts`** — MCP tool handler implementations with Zod input schemas
- **`types.ts`** — Shared TypeScript types (`SearchResult`, `ConversationExchange`, etc.)
- **`hybrid.ts`** — FTS5 query building (`buildFtsQuery`), BM25→0-1 score conversion, RRF merge
- **`search.ts`** — Search implementation orchestrating keyword + vector backends
- **`db.ts`** — SQLite operations (open, insert, delete, migrations)
- **`indexer.ts`** — File scanning, staleness detection, incremental indexing
- **`chunker.ts`** — Markdown heading-aware + conversation exchange-aware chunking
- **`semantic-markdown-chunker.ts`** — 3-stage pipeline: parse → boundary score → variance-minimizing DP
- **`semantic-chunker.ts`** — Conversation-specific: heuristic boundary scoring (tool-type shifts, file-path changes, topic phrases, time gaps) → DP segmentation
- **`llm-boundary-scorer.ts`** — Two-pass coprime windows (16, 11) with per-pair caching for LLM-based boundary scoring
- **`llm-client.ts`** — OpenAI-compatible LLM client for boundary scoring
- **`embeddings.ts`** — Embedding generation via Xenova/transformers.js (ONNX)
- **`conversation-parser.ts`** — JSONL → structured exchange pairs
- **`doctor-cli.ts`** — Database diagnostics and repair CLI
- **`reindex-cli.ts`** — CLI entry point for batch reindexing

### Conversation preservation

Conversation chunks are **never pruned** from the index, even after Claude Code deletes the original `.jsonl` files. The index is the only surviving copy of that knowledge. Only curated memory files (`MEMORY.md`, `memory/*.md`) are pruned when removed from disk. This is enforced in `indexer.ts:indexAll()` — the prune loop skips any path starting with `conversations/`.

### Codebase indexing

Source code from external repos can be indexed for semantic search via `codebase_search` MCP tool and `memory_search` with `source=codebase`.

```bash
# Index a codebase (first time — full index)
~/.claude-memory/graphiti-venv/bin/python3 ~/claude-memory/scripts/codebase-index.py --path ~/toast-analytics --name toast-analytics

# Incremental update (only changed files)
~/.claude-memory/graphiti-venv/bin/python3 ~/claude-memory/scripts/codebase-index.py --path ~/toast-analytics --name toast-analytics --update

# Low-impact mode (throttled, nice'd)
~/.claude-memory/graphiti-venv/bin/python3 ~/claude-memory/scripts/codebase-index.py --path ~/toast-analytics --name toast-analytics --throttle

# List indexed codebases
~/.claude-memory/graphiti-venv/bin/python3 ~/claude-memory/scripts/codebase-index.py --list

# Remove a codebase
~/.claude-memory/graphiti-venv/bin/python3 ~/claude-memory/scripts/codebase-index.py --remove --name toast-analytics
```

Codebase chunks are stored in the existing `chunks` table with `file_path` prefixed by `codebase:<name>/`. The `codebase_meta` table tracks per-file content hashes for incremental updates. A `PreToolUse:Write` hook (`~/.claude/hooks/checks/pre-write-codebase-check.py`) surfaces similar existing code when creating new source files.

### Addon reference databases

Skills and plugins can ship pre-built `.db` files containing searchable reference material (documentation, guides, API references). The server discovers these at startup and makes them searchable via `memory_search(source="<name>")`.

**Discovery (mirrors Claude Code's skill resolution):**
- Plugins: reads `~/.claude/plugins/installed_plugins.json`, globs `**/*.db` under each installPath → source name `plugin-name:stem`
- Local skills: globs `~/.claude/skills/**/*.db` → source name is filename stem
- Local skills shadow plugins on name collision

**Source routing:** `source` parameter on `memory_search` routes to addon DBs exclusively — no cross-contamination with primary `memory.db`. Empty source = primary only.

**Building addon databases:**

```bash
# Build from a directory of markdown/text files
~/.claude-memory/graphiti-venv/bin/python3 ~/claude-memory/scripts/build-reference-db.py ./my-docs/ -o my-skill.db

# Place next to SKILL.md
cp my-skill.db ~/.claude/skills/my-skill/

# Search via MCP
memory_search(query="window functions", source="my-skill")
```

**Model compatibility:** Each addon DB stamps its embedding model in the `meta` table. Mismatched models are skipped at discovery time.

## Key Design Decisions

- **Hybrid search with RRF**: Vector (cosine similarity) and keyword (FTS5 BM25) results merged via Reciprocal Rank Fusion (k=60) rather than weighted-sum scoring. Avoids suppressing keyword-only results below thresholds.
- **Two embedding paths**: Node.js indexer uses Xenova/transformers.js (ONNX). Python server uses sentence-transformers (PyTorch). Same model family, compatible embeddings.
- **Python reads BLOBs, not vec0**: The Python server loads all embedding BLOBs into a numpy matrix for brute-force cosine sim. The `chunks_vec` (vec0) table exists but is only written/queried by the Node.js side. This is a pragmatic workaround — `sqlite-vec` Python bindings don't load on this platform.
- **Exchange-aware chunking**: Conversation archives are chunked at exchange boundaries — user/assistant pairs are never split across chunks.
- **Semantic boundary scoring**: Two chunker variants — `semantic-markdown-chunker.ts` for curated notes (heading-level-aware), `semantic-chunker.ts` for conversations (tool-type shifts, file-path changes, topic phrases, time gaps). Both use variance-minimizing DP for final segmentation.
- **Embedding cache**: Table `embedding_cache` avoids re-embedding unchanged content across reindexes.
- **Mtime-based staleness**: `isIndexStale()` compares file mtimes against DB timestamps — O(file count) not O(DB size).
- **Multi-hop retrieval**: `memory_deep_search` MCP tool runs 2-pass search — Pass 1 uses standard hybrid search, Pass 2 extracts new entities from top results and searches again with entity+keyword only (no vector/temporal — saves ~500ms). Results merged via RRF and deduplicated.
- **Entity relationship graph**: `entity_relationships` table stores co-occurrence pairs (canonical ordering). `entity_browse` lists entities with counts; `entity_graph` explores co-occurrence neighborhoods at depth 1-2.
- **Call resolution cascade**: 6-strategy cascade in `call_resolver.py` (import-map exact → suffix → same-module → unique-name → proximity → fuzzy) with decreasing confidence scores. SCIP indexer results override tree-sitter when available.
- **Cross-repo dependency graph**: `cross_repo_deps.py` uses `build_parser.py` to parse Gradle/Maven/npm/pip build files into `repo_dependency` edges. `dependency_search` supports `repo_depends_on` and `repo_depended_on_by` directions.
- **TurboQuant sidecar backend**: `TurboQuantBackend` loads pre-built sidecar files for 3-stage search (binary Hamming → 4-bit dot products → float32 mmap rerank). Selected via `VECTOR_BACKEND=turboquant` env var or auto-detected from sidecar files.

### TurboQuant sidecar backend

```bash
# Generate sidecar files from existing embeddings (without modifying DB)
source ~/.claude-memory/graphiti-venv/bin/activate
python3 scripts/migrate_to_quantized.py --sidecar-only

# Or generate alongside normal DB migration
python3 scripts/migrate_to_quantized.py --sidecar

# Files written to ~/.claude-memory/index/:
# - packed_vectors.bin: concatenated 4-bit packed vectors
# - rerank_matrix.f32: float32 matrix for exact reranking (mmap'd)
# - quantization.json: metadata (codebook, rotation_seed, dims, rowid_map)
```

**Env vars:**
- `VECTOR_BACKEND`: `float32` (default) or `turboquant`. Auto-detects sidecar files if present.
- Falls back to float32 `VectorSearchBackend` if sidecar files missing or search fails.

### Cross-repo dependency indexing

```bash
# Index repo dependencies
python3 scripts/cross_repo_deps.py --path ~/my-repo --name my-repo

# Incremental update (skip unchanged build files)
python3 scripts/cross_repo_deps.py --path ~/my-repo --name my-repo --update

# List indexed repos
python3 scripts/cross_repo_deps.py --list

# Remove a repo's dependency edges
python3 scripts/cross_repo_deps.py --remove --name my-repo
```

### Entity relationship backfill

```bash
# Backfill entity_relationships from existing chunk_entities
python3 scripts/backfill_entity_relationships.py
```

## Concurrency

Multiple Claude Code sessions share the same SQLite database:

- **Write lock**: `reindex.lock` file (O_CREAT|O_EXCL) serializes reindexing. Stale locks (>5 min) auto-reclaimed.
- **Search degradation**: Vector and keyword search are wrapped independently — if one backend fails, the other still returns results.
- **Safe virtual table writes**: FTS5/vec0 operations in `insertChunk()` and `deleteChunksByFile()` are individually try/caught.
- **busy_timeout**: 5000ms for concurrent readers/writers.
- **Graceful shutdown**: SIGTERM/SIGINT → checkpoint WAL → close DB.

## Logging

- **Python server**: `logging` module to stderr. Log prefix: `[unified-memory]`.
- **Webhook pipeline**: `logging` module to stderr. Log prefix: `[webhook-pipeline]`.
- **Node.js**: `process.stderr.write`. Stdout is reserved for MCP JSON-RPC protocol messages.
