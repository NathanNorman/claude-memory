## Why

Multi-hop queries like "How did claude-memory search evolve from keyword to hybrid" require connecting multiple documents across time, but `memory_search` does single-pass retrieval. It merges keyword + vector + temporal + entity signals via N-way RRF in one shot, so it returns the best matches for the query but misses related documents that would complete the picture. A second retrieval pass that expands on entities found in Pass 1 results can chain these connections.

## What Changes

- New MCP tool `memory_deep_search(query, maxResults, maxHops=1)` that performs 2-pass retrieval
- Pass 1: runs existing `memory_search` internals (keyword + vector + temporal + entity)
- Pass 2: extracts entities from top-5 Pass 1 results, runs EntityRetrieval with expanded entity set + keyword search with extracted terms
- Merges Pass 1 + Pass 2 via RRF, deduplicates, returns results with `hop` field indicating which pass found them
- New test coverage in `scripts/test_multi_signal.py`
- Benchmark harness integration for evaluation

## Capabilities

### New Capabilities
- `deep-search`: Multi-hop retrieval tool that chains entity extraction from initial results into a second retrieval pass, merging both passes via RRF

### Modified Capabilities

## Impact

- `src/unified_memory_server.py` -- new `memory_deep_search` tool function, new internal `_deep_search_pass2()` helper
- Reuses existing `extract_entities()`, `EntityRetrieval.search()`, `FlatSearchBackend.merge_rrf_multi()`, and `memory_search` internals
- No schema changes -- reads existing `chunks`, `chunk_entities`, `chunks_fts` tables
- No breaking changes to existing `memory_search` tool
