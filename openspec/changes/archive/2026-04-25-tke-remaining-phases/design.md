## Context

claude-memory's TKE code intelligence system currently has: tree-sitter call graph extraction (Java/Kotlin/Python/TS), type hierarchy, CodeRankEmbed embeddings (768d), binary vector coarse pass, multi-hop graph traversal via recursive CTEs, cross-repo dependency resolution via build file parsing, and a webhook auto-indexing pipeline.

The system handles ~15K edges well but the research synthesis identified scaling limits at >1M edges (recursive CTEs degrade to ~80ms at depth 5), fixed-dimension embeddings (768d = 3KB/vector), and tree-sitter's ~80% call graph accuracy ceiling.

## Goals / Non-Goals

**Goals:**
- Sub-millisecond graph traversal at 1M+ edges via igraph in-process sidecar
- Automatic architectural module discovery via community detection
- Optional compiler-grade accuracy via SCIP indexers
- 3x embedding storage reduction via Matryoshka dimension truncation (768d → 256d)
- Semantic enrichment of high-value nodes via cached LLM labels
- <1s webhook-to-searchable latency

**Non-Goals:**
- Replacing SQLite with a graph database (Neo4j, KuzuDB)
- Supporting languages beyond Java/Kotlin/Python/TypeScript
- Real-time streaming index updates (batch/webhook is sufficient)
- Building a UI or visualization layer

## Decisions

### 1. igraph over networkx for in-process graph queries

**Decision**: Use igraph (C core, Python bindings) as the graph query layer.

**Why**: 10M edges = ~320MB in igraph vs ~5GB in networkx. igraph BFS/DFS is sub-millisecond even at 5M edges. The C core means no GIL bottleneck during graph operations.

**Alternative considered**: Keep recursive CTEs only. Rejected because CTEs hit ~200ms at 5M edges depth 5, which is too slow for interactive MCP queries.

**Approach**: Load edges from SQLite into igraph on server startup. Rebuild on SIGHUP or after reindex. Fall back to CTEs if igraph load fails.

### 2. Louvain for community detection (not Leiden)

**Decision**: Use igraph's built-in Louvain implementation.

**Why**: Louvain is deterministic, well-understood, and igraph bundles it — no extra dependency. Leiden is theoretically better (no disconnected communities) but requires the `leidenalg` package and the practical difference on code graphs is minimal.

**Approach**: Run Louvain on the call+import graph, store community IDs in a `communities` table, expose via `community_search` MCP tool.

### 3. SCIP as optional Tier 2 (not replacing tree-sitter)

**Decision**: SCIP indexers run as an optional second pass after tree-sitter extraction.

**Why**: SCIP requires full build (Gradle/npm/pip). Many repos can't build locally. Tree-sitter's ~80% accuracy is good enough for most queries. SCIP fills in the remaining 20% when available.

**Alternative considered**: SCIP-only indexing. Rejected because it requires build infrastructure that most developers don't have configured for every repo.

**Approach**: New `--scip` flag on codebase-index.py. Runs scip-java/scip-typescript/scip-python, parses SCIP protobuf output, merges edges with higher confidence scores.

### 4. nomic-embed-text-v1.5 for Matryoshka embeddings

**Decision**: Switch code embeddings from CodeRankEmbed (768d, no MRL) to nomic-embed-text-v1.5 (768d, MRL to 64d).

**Why**: MRL allows truncating to 256d with <1% quality loss = 3x storage reduction. nomic-embed-text-v1.5 is MIT licensed, CPU-capable, and scores competitively on code retrieval after fine-tuning exposure.

**Alternative considered**: Voyage Code 3 (92.3% accuracy) — rejected because it's API-only, adding latency and cost. CodeSage Large V2 — rejected because 1.3B params requires GPU.

**Approach**: Model swap in codebase-index.py, store at 256d, update Python server to match. Requires full re-index (automated via existing model migration detection).

### 5. GPT-4o-mini for semantic labeling (not local LLM)

**Decision**: Use GPT-4o-mini via API for node labeling.

**Why**: ~$0.01/1K tokens, fast enough for batch labeling. Local LLMs (Llama 3.1, Phi-3) require GPU or are too slow on CPU for batch use. Labels are cached permanently so API cost is one-time per node.

**Approach**: Identify high-value nodes (symbols with many incoming edges, public API annotations). Send function signature + docstring to GPT-4o-mini for a 1-sentence label. Store in symbols.metadata JSON.

### 6. Pipeline instrumentation for latency optimization

**Decision**: Add timing instrumentation to the webhook → index pipeline rather than redesigning it.

**Why**: The pipeline architecture (webhook → SQLite queue → worker → reindex) is sound. The bottleneck is likely embedding generation (batch size, model load time). Measure first, then optimize specific stages.

**Approach**: Add `started_at`/`completed_at` to job queue, log per-stage timing, identify and optimize the slowest stage. Target: <1s for small diffs (<10 files).

## Risks / Trade-offs

- **[igraph memory at scale]** 10M edges = ~320MB. If monitoring >50 large repos, memory could reach 1-2GB. **Mitigation**: Load only the requested codebase's subgraph, not all edges. Add `--max-edges` cap.

- **[SCIP build requirement]** SCIP indexers require a working build. Many Toast repos have complex Gradle configs. **Mitigation**: SCIP is strictly optional. Fall back to tree-sitter gracefully. Document which repos have verified SCIP support.

- **[Model migration re-index time]** Switching to nomic-embed-text-v1.5 requires full re-index of all codebases. At ~100 files/min on CPU, large repos take 30+ minutes. **Mitigation**: Existing model migration detection auto-triggers re-index. Run overnight via cron.

- **[LLM labeling API dependency]** GPT-4o-mini requires internet access and API key. **Mitigation**: Labeling is purely additive — system works fine without labels. Cache aggressively so API is only called once per node.

- **[Community detection staleness]** Communities are computed at index time and may drift as code evolves. **Mitigation**: Recompute on every reindex run. Communities table has `updated_at` for staleness checking.
