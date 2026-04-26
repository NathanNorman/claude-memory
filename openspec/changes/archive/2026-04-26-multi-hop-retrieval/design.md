## Context

`memory_search` runs 4 retrieval signals (keyword, vector, temporal, entity) in a single pass and merges via N-way RRF (`merge_rrf_multi`). This works well for direct lookups but fails on queries that require chaining -- e.g., "How did search evolve from keyword to hybrid" needs the keyword-era doc AND the hybrid-era doc, which share no terms. Pass 1 might find the hybrid doc; Pass 2 can extract entities (e.g., "FTS5", "sqlite-vec") and retrieve the keyword-era doc that mentions them.

All search primitives already exist in `unified_memory_server.py`: `FlatSearchBackend.search_keyword()`, `VectorSearchBackend.search()`, `TemporalRetrieval.search()`, `EntityRetrieval.search()`, `extract_entities()`, and `merge_rrf_multi()`.

## Goals / Non-Goals

**Goals:**
- Add `memory_deep_search` MCP tool that performs 2-pass retrieval
- Reuse 100% of existing search primitives -- no new backends
- Return results with `hop` metadata so callers know provenance
- Stay under 2s total latency for typical queries

**Non-Goals:**
- Arbitrary N-hop chains (maxHops > 1 deferred)
- Changes to existing `memory_search` tool behavior
- New database tables or schema migrations
- Query planning or LLM-in-the-loop reformulation

## Decisions

**1. Extract entities from top-5 Pass 1 results (not all results)**

Top-5 balances coverage vs. noise. Extracting from all results would pull in low-relevance entities that dilute Pass 2. The existing `extract_entities()` function handles tool/project/person extraction.

Alternative: top-3 (too narrow for diverse queries) or top-10 (diminishing returns, slower).

**2. Pass 2 uses EntityRetrieval + keyword search only (not vector)**

Entity expansion is the core value of multi-hop -- vector similarity would largely return the same results as Pass 1. Keyword search with extracted entity terms catches documents that share terminology but not embedding proximity.

Alternative: full 4-signal Pass 2 (redundant with Pass 1, doubles latency for marginal gain).

**3. Merge Pass 1 + Pass 2 via RRF with deduplication**

Reuse `merge_rrf_multi()` with Pass 1 full results and Pass 2 results as two ranked lists. Deduplicate by chunk ID. Tag each result with `hop: 0` (Pass 1) or `hop: 1` (Pass 2 only).

**4. Implement as internal helper, not separate class**

A `_deep_search()` async function that calls `memory_search` internals directly. No new class hierarchy -- keeps the change minimal and reviewable.

## Risks / Trade-offs

- **Latency doubling** -- Pass 2 adds ~200-500ms. Mitigated by limiting Pass 2 to entity + keyword (skip vector/temporal) and capping entity extraction to top-5.
- **Entity noise** -- Extracted entities from Pass 1 may be irrelevant to the original query. Mitigated by RRF naturally down-ranking results that only appear in Pass 2 with low overlap.
- **No benefit for simple queries** -- Single-concept queries won't gain from multi-hop. This is acceptable; callers choose `memory_deep_search` when they need it.
