# Vector Search Upgrade: Replacing the Dead Graph

**Date:** 2026-03-03
**Status:** Implemented (2026-03-03)
**Author:** Claude (with Nathan)

## Problem

The unified-memory system was designed with three search backends:

1. **FTS5 keyword search** — works, fast, always available
2. **Numpy brute-force vector search** — works, but has gaps
3. **Graphiti knowledge graph** — dead. OpenAI API costs for LLM entity extraction made it impractical

Without the graph, the system loses:
- Semantic query expansion (graph entities enriched keyword searches)
- Multi-hop reasoning across sessions
- Entity/relationship connections ("what decision affected X?")

The graph was supposed to be the "smart" layer. FTS5 is the "fast" layer. Right now we only have fast.

## Current State of Vector Search

Here's the surprise: **vector search already exists and mostly works**. But it's duct-taped together across two languages.

### What's in place

| Component | What it does | Status |
|-----------|-------------|--------|
| Node.js indexer (`~/claude-memory/src/`) | Embeds chunks with `all-MiniLM-L6-v2` (384-dim), writes to `chunks.embedding` BLOB + `chunks_vec` (vec0) | Works, runs on reindex |
| `embedding_cache` table | 28,592 cached embeddings, avoids re-embedding on reindex | Works |
| `chunks_vec` (vec0 ANN index) | Populated by Node.js indexer | Exists but **unused** — Python can't load it |
| Python `VectorSearchBackend` | Loads ALL embeddings into numpy matrix, brute-force cosine sim | Works at current scale (3.6K chunks) |
| RRF merge (`merge_rrf`, k=60) | Combines FTS5 + vector results | Works |
| `memory_write` embedding | Skipped — written chunks get FTS5 only, no vector until reindex | **Gap** |

### Coverage

- 3,571 of 3,609 chunks (99%) have embeddings
- 38 chunks missing embeddings (written via `memory_write`, never reindexed)
- Model: `Xenova/all-MiniLM-L6-v2` (ONNX, runs locally, no API cost)

### The split-brain problem

The Node.js indexer writes vec0 entries, but the Python MCP server can't query vec0 (`sqlite-vec` Python package fails to load the extension on this platform). So the Python server works around it by loading all 3,600 raw BLOB embeddings into a numpy array and doing dot products. This works fine at current scale but:

- Wastes ~5MB of memory per server instance
- Cold start loads all BLOBs on first vector query (~200ms)
- Won't scale past ~100K chunks
- The vec0 table is maintained for nothing

## Proposed Changes

Three changes, in priority order. Each is independently useful.

### 1. Fix `memory_write` embedding gap (small, high value)

**Problem:** When you write a memory via `memory_write`, it gets FTS5 indexed immediately but no embedding is generated. That chunk is invisible to vector search until the Node.js indexer runs (manually, or next session).

**Fix:** In `unified_memory_server.py`, after writing chunks to FTS5, also:
1. Embed the new chunk(s) using the already-loaded `SentenceTransformer` model
2. Write the embedding BLOB to `chunks.embedding`
3. (Skip `chunks_vec` — we can't write to it from Python anyway)

The numpy matrix gets rebuilt on next search call (it's lazy-loaded and could be invalidated).

**Scope:** ~30 lines in `index_written_file()` in `unified_memory_server.py`.

### 2. Fix vec0 loading from Python (medium, enables future scale)

**Problem:** The Python graphiti-venv has `sqlite-vec 0.1.6` installed but `sqlite_vec.load(conn)` fails. The Node.js indexer uses `sqlite-vec 0.1.7-alpha.2`. The native extensions are incompatible.

**Options (pick one):**

**(A) Use the `.dylib` directly.** The Node.js `sqlite-vec` package bundles a `vec0.dylib`. Find it in `node_modules/sqlite-vec/` and load it via `conn.load_extension('/path/to/vec0')` from Python. No pip install needed.

```python
# Instead of: import sqlite_vec; sqlite_vec.load(conn)
# Do: conn.load_extension('/Users/nathan.norman/claude-memory/node_modules/sqlite-vec/vec0')
```

**(B) Build sqlite-vec from source for Python 3.12.** The pip package may have a newer release that works. Check `pip install sqlite-vec --upgrade` or build from the GitHub repo.

**(C) Keep numpy brute-force.** At 3.6K chunks it's fast enough. Revisit when chunk count hits 50K+.

**Recommendation:** Try (A) first — it's a one-line change. If the dylib ABI matches Python's SQLite, it just works. If not, (C) is fine for now.

### 3. Remove Graphiti dependency, simplify startup (medium, reduces complexity)

**Problem:** The launcher script tries to start FalkorDB, sets OPENAI_API_KEY, and the server initializes a `GraphSearchBackend` that either fails silently or costs money. The graph ingestion on SessionEnd also runs and may silently fail.

**Changes:**
- Remove `falkordb-ensure` from `unified-mcp-launcher.sh`
- Remove `GraphSearchBackend` class and all graph-related code from `unified_memory_server.py`
- Remove `ingest_session.py` Graphiti ingestion from SessionEnd hook (or make it a no-op)
- Remove `graphiti-core`, `falkordb` from venv dependencies
- Keep `sentence-transformers` and `torch` (needed for vector search)
- Docker compose file and FalkorDB data can be archived

**Why:** Dead code that adds startup time, confusing error messages, and an OpenAI API key requirement for a feature that's off. Cleaning it out makes the system easier to understand and debug.

## What This Gets You

After these three changes:

| Before | After |
|--------|-------|
| FTS5 keyword search + broken graph + partial vector | FTS5 keyword search + full vector search |
| `memory_write` chunks invisible to vector search | Immediate vector coverage on write |
| Graph backend that silently fails | No graph — clean, honest two-backend system |
| FalkorDB Docker container needed at startup | No Docker dependency |
| OpenAI API key required (for graph) | No external API keys needed |
| ~200ms cold start for numpy matrix | Same (or better with vec0 fix) |

The hybrid FTS5 + vector search with RRF merge is actually a strong retrieval system. It gives you:
- **Keyword precision** (FTS5) — exact matches, code snippets, error messages
- **Semantic recall** (vector) — conceptually related content even without keyword overlap
- **Rank fusion** — results that score well on both methods surface highest

This is essentially what the graph was supposed to add (semantic connections) but without the per-query LLM cost.

## Non-Goals

- **No new embedding model.** `all-MiniLM-L6-v2` is fine. It's local, fast, 384-dim, and all existing embeddings use it. Changing models means re-embedding everything.
- **No re-architecture of the indexer.** The Node.js indexer works. It runs on reindex, maintains the cache, handles conversations. Leave it alone.
- **No chunking changes.** 400-token sliding window with exchange-aware conversation chunking is reasonable.
- **No query-time LLM calls.** The whole point is avoiding per-query API costs. Search stays pure retrieval.

## Implementation Order

1. **Fix `memory_write` embeddings** — highest value, smallest change, no dependencies
2. **Remove Graphiti** — cleanup, reduces moving parts, makes debugging easier
3. **Fix vec0 from Python** — nice-to-have, only matters at scale

## Files to Modify

| File | Change |
|------|--------|
| `~/.claude-memory/unified_memory_server.py` | Add embedding generation in `index_written_file()`, remove GraphSearchBackend |
| `~/.claude-memory/unified-mcp-launcher.sh` | Remove `falkordb-ensure`, remove `OPENAI_API_KEY` export |
| `~/.claude-memory/scripts/ingest_session.py` | Remove or no-op the Graphiti ingestion |
| `~/.claude/settings.json` | Update SessionEnd hook if it references ingest_session.py |
| `~/.claude-memory/graphiti-venv/` | Remove `graphiti-core`, `falkordb` packages (keep `sentence-transformers`) |

## Open Questions

1. **Should we keep the graph data?** FalkorDB has extracted entities from months of sessions. Could be useful for a future cheaper graph approach (e.g., local LLM extraction). Recommend: archive `falkordb/data/` but don't delete.

2. **Should the Node.js indexer still write to `chunks_vec`?** If Python can't read it, it's wasted I/O. But if we fix vec0 loading (change 2), we'll want it. Recommend: keep it, it's not hurting anything.

3. **Embedding model upgrade path?** If we ever want better embeddings (e.g., `nomic-embed-text-v1.5`, 768-dim), the `meta.embedding_model` key in the DB triggers full reindex. The migration path exists. Not needed now.
