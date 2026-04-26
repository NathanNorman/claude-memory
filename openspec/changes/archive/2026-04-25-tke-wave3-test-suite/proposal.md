## Why

TKE Wave 3 added 6 major capabilities (igraph sidecar, community detection, SCIP indexer, Matryoshka embeddings, LLM semantic labeling, webhook latency optimization) across 4 Python files with ~1,500 lines of new code. The existing test suite (40 tests) only covers build parsing and cross-repo resolution — none of the new features have dedicated tests. Without comprehensive test coverage, regressions from future changes will go undetected.

## What Changes

- Add integration tests for GraphSidecar (igraph BFS, staleness detection, SIGHUP rebuild, memory cap)
- Add integration tests for community detection (Louvain clustering, community_search MCP tool modes, staleness)
- Add unit tests for SCIP parser (language detection, JSON parsing, edge merging)
- Add integration tests for Matryoshka embeddings (truncation, L2 renorm, dimension auto-detection, query matching)
- Add integration tests for LLM labeling (high-value node identification, label caching, label surfacing in search)
- Add integration tests for webhook latency (PipelineTimer, timing storage, pipeline health metrics, warm model)
- Add a test fixture that creates a populated in-memory SQLite DB with edges, symbols, chunks, and communities

## Capabilities

### New Capabilities
- `igraph-sidecar-tests`: Tests for GraphSidecar class — load, traverse, rebuild, staleness, memory cap
- `community-detection-tests`: Tests for Louvain clustering and community_search tool modes
- `scip-parser-tests`: Tests for SCIP language detection, protobuf parsing, edge merging
- `matryoshka-embedding-tests`: Tests for dimension truncation, L2 renorm, auto-detect, query matching
- `llm-labeling-tests`: Tests for high-value node ID, label caching, label in search results
- `webhook-latency-tests`: Tests for PipelineTimer, timing storage, pipeline health, warm model

### Modified Capabilities

## Impact

- New test files in `scripts/` (one per capability)
- No production code changes
- Test DB fixture shared across test files
- Tests run with: `python3 scripts/test_*.py`
