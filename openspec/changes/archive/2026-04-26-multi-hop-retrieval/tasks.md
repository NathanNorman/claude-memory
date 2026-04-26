## 1. Core Implementation

- [ ] 1.1 Add `_extract_pass2_entities()` helper that takes top-5 Pass 1 results, calls `extract_entities()` on each, and returns new entities not present in the original query entities
- [ ] 1.2 Add `_deep_search_pass2()` helper that runs `EntityRetrieval.search()` with expanded entity set and `FlatSearchBackend.search_keyword()` with extracted entity values as query terms
- [ ] 1.3 Add `memory_deep_search(query, maxResults, maxHops)` MCP tool function that orchestrates Pass 1 (reusing `memory_search` internals), Pass 2, RRF merge, deduplication, and `hop` field tagging
- [ ] 1.4 Register `memory_deep_search` with the FastMCP server (add `@mcp.tool()` decorator and docstring)

## 2. Result Formatting

- [ ] 2.1 Add `hop` field to each result dict: `0` for Pass 1 results, `1` for Pass 2-only results
- [ ] 2.2 Add `hops_performed` to the response dict (0 if Pass 2 skipped, 1 otherwise)
- [ ] 2.3 Apply existing post-filters (date range, project, source) to merged results

## 3. Testing

- [ ] 3.1 Add multi-hop retrieval tests to `scripts/test_multi_signal.py`: basic deep search returns results, Pass 2 skipped when no new entities, hop field values are correct
- [ ] 3.2 Add latency assertion: deep search completes under 2s on test index
- [ ] 3.3 Test deduplication: results appearing in both passes have `hop: 0`

## 4. Benchmark Integration

- [ ] 4.1 Add multi-hop query examples to the benchmark harness evaluation set
- [ ] 4.2 Compare `memory_search` vs `memory_deep_search` recall on multi-hop queries
