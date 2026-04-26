## Why

TKE Waves 1-2 delivered call graphs, type hierarchy, CodeRankEmbed, binary vectors, webhook pipeline, multi-hop graph traversal, and cross-repo dependency resolution. The system works at current scale (~15K edges) but has known scaling limits (brute-force graph queries degrade past 1M edges, embeddings are fixed-dimension 768d, no compiler-grade accuracy option). Completing the remaining capabilities closes the gap between "works for us" and "production-grade code intelligence system."

## What Changes

- Add igraph as an in-process graph query layer for sub-millisecond traversal at >1M edges
- Add Louvain community detection for automatic architectural clustering of codebases
- Integrate SCIP indexers (Java/Kotlin, TypeScript, Python) for compiler-grade call graph accuracy
- Switch to Matryoshka-capable embedding model for dimension reduction (768d → 256d) with <1% quality loss
- Add selective LLM labeling on high-value nodes (public APIs, module entry points)
- Optimize webhook-to-search latency to <1s end-to-end

## Capabilities

### New Capabilities
- `igraph-sidecar`: In-process igraph graph query layer loaded from SQLite edges at startup, with fallback to recursive CTEs for small graphs
- `community-detection`: Louvain clustering over the edges graph to identify architectural modules, exposed as MCP tool
- `scip-indexer-integration`: Optional SCIP-based indexing for compiler-grade call graph and type resolution accuracy
- `matryoshka-embeddings`: Switch to MRL-capable model with configurable dimension truncation for storage/speed tradeoff
- `llm-semantic-labeling`: Batch LLM annotation of high-value nodes (API endpoints, entry points) cached in symbols metadata
- `webhook-latency-optimization`: Pipeline tuning to achieve <1s from git push to searchable index update

### Modified Capabilities
- None (all new capabilities layer on top of existing infrastructure)

## Impact

- **Dependencies**: igraph (pip), SCIP indexers (npm/pip CLI tools), new embedding model download (~500MB)
- **Database**: New `communities` table, `labels` column on symbols, embedding dimension change requires full re-index
- **MCP tools**: New `community_search` tool, enhanced `graph_traverse` with igraph backend
- **Indexing pipeline**: SCIP as optional second pass after tree-sitter, latency monitoring instrumentation
