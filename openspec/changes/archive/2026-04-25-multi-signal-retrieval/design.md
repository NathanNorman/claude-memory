## Context

claude-memory's retrieval pipeline currently uses 2-channel hybrid search (FTS5 keyword + vector cosine similarity) merged via Reciprocal Rank Fusion (k=60). This scores 87.3% J-score on LoCoMo but temporal questions score 68.8% and multi-hop trails leaders by 5-10pp.

Research into top-performing systems (Hindsight 89.6%, EverMemOS 93.1%, ByteRover 92.2%) reveals they all use 4+ retrieval channels. Our cross-encoder reranking experiment (April 12) showed reranking *hurts* with only 2 channels (-3.6pp R@10) but the "Drowning in Documents" paper confirms it helps with diverse candidate pools. The path forward is more retrieval signals, not better post-processing.

The architecture is dual-runtime: Python MCP server (production) + Node.js indexer (batch). All new extraction will be Python-only. SQLite is the shared storage layer.

Baseline retrieval scores (April 12, 10 conversations, 1,536 questions):
- R@5: 82.9%, R@10: 91.6%
- Temporal R@10: 71.7%, Temporal-inference R@10: 71.7%
- Open-domain R@10: 93.9%

## Goals / Non-Goals

**Goals:**
- Improve LoCoMo temporal retrieval from R@10 71.7% to 80%+
- Improve multi-hop retrieval by 5-10pp
- Maintain zero LLM calls for retrieval (keep the $0 search story)
- Experiment-driven: measure every variant before committing to production
- Backward compatible: existing search works unchanged when new signals have no data

**Non-Goals:**
- LLM-based entity extraction (Graphiti burned $10.86/day — too expensive)
- Cross-encoder reranking (proven negative on our 2-channel pipeline)
- Full knowledge graph with relationship edges (requires LLM for relation extraction)
- Coreference resolution (v2 — skip for now, explicit entity mentions capture ~80% of value)
- Changes to the Node.js indexer (all new extraction in Python only)

## Decisions

### D1: Experiment in benchmark first, port to production later

All new retrieval signals are implemented in `locomo_retrieval_bench.py` first. Each variant is a 10-minute run with zero API cost. Only the winning config gets ported to `unified_memory_server.py`.

**Why:** E2E benchmarks take 2-4 hours per run. Retrieval-only benchmarks take 10 minutes. This gives us 10x more iterations per day. We tried to design the right approach upfront for cross-encoder reranking and got it wrong. Measure, don't guess.

### D2: N-way RRF merge (not weighted scoring or boosting)

Extend the existing `merge_rrf` to accept N ranked lists. Each new signal (temporal, entity) produces a ranked list that enters RRF equally. RRF is parameterless beyond k — no weights to tune.

**Alternatives considered:**
- *Boost multipliers* (multiply base RRF score by temporal/entity factor): Requires weight tuning. More knobs = more ways to overfit.
- *Pre-filtering* (only search chunks matching temporal/entity criteria): Too aggressive — misses chunks where the signal is absent but the content is relevant.
- *Weighted RRF* (give temporal 2x weight on temporal queries): Requires query classification. Complexity for uncertain benefit.

**Why RRF:** It's what we already use, what Hindsight uses, what EverMemOS uses. It handles heterogeneous signals naturally — a sparse entity signal with 20 results merges cleanly with a dense vector signal with 200 results. If experiments show one variant works better, we can pivot, but RRF is the right default.

### D3: spaCy `en_core_web_sm` for NER (not GLiNER, not Flair)

12MB model, 10,000 words/sec on CPU, 18 OntoNotes entity types. Focus on PERSON, DATE, ORG, LOC.

**Alternatives considered:**
- *GLiNER* (zero-shot NER): 20-50x slower, underperforms on informal/conversational text per their own paper.
- *Flair* (89.7% F1): 1.5GB model, 30x slower than spaCy. Marginal accuracy gain doesn't justify indexing cost.
- *LLM-based extraction*: $10.86/day with Graphiti. Non-starter.

### D4: `dateparser` for temporal expression resolution

Pure Python, handles "yesterday", "last week", "3 months ago" with a reference timestamp anchor. No JVM (unlike SUTime), no external service (unlike Duckling).

**Why not regex-only:** Regex catches "May 8, 2023" but not "yesterday" or "a few weeks ago". These relative expressions are exactly what LoCoMo temporal questions test.

### D5: Nearest-centroid clustering for scenes (not DBSCAN, not Louvain)

O(k) per new chunk, naturally incremental (works for both batch and real-time writes), no LLM calls. Threshold τ starts at 0.70, tuned via benchmark experiments.

**Alternatives considered:**
- *DBSCAN/HDBSCAN*: Batch-only, requires re-running on full corpus for each new chunk. Fine for reindex but not for `memory_write`.
- *Louvain/VLouvain*: Strong on batch (GraphRAG-V uses it), but not incremental. Could use as a periodic refinement pass alongside online nearest-centroid.
- *Agglomerative*: O(n²), too expensive for large chunk counts.

### D6: Scene expansion post-RRF (not scene-level retrieval)

After RRF merge, look up scene IDs for top results, pull in cluster neighbors not already in results. This preserves the existing retrieval ranking while adding thematic context.

**Alternative:** Score scenes by max-member-score and return all chunks from top scenes (EverMemOS approach). Risk: large scenes dominate results. Start with expansion, consider scene-level scoring in v2.

## Risks / Trade-offs

- **Entity overlap is sparse** — many chunks won't mention any named entities. Entity signal will be absent for ~30-40% of queries. → RRF handles this naturally; absent signals contribute nothing rather than penalizing.
- **dateparser may misparse** — "May" (the month) vs "May" (a person's name). → spaCy NER runs first; if "May" is tagged as PERSON, don't parse it as a date.
- **Clustering threshold τ sensitivity** — too low creates one mega-scene, too high creates all singletons. → Benchmark sweep: 0.60, 0.65, 0.70, 0.75, 0.80.
- **Scene expansion may retrieve irrelevant neighbors** — cluster quality determines expansion quality. → Limit expansion to top-5 neighbors by similarity to query.
- **Dual-runtime complexity** — Node.js indexer won't populate new tables. → Python backfill script runs after reindex. New tables are additive; missing data means the signal returns empty, not an error.
- **LoCoMo benchmark has 6.4% answer key errors** (Penfield audit). Improvements may not show in J-score if we're "correcting" gold answers. → Track R@5/R@10 (retrieval-only, not judge-dependent) as primary metric.

## Open Questions

- **Entity normalization strategy:** Lowercase exact match vs fuzzy/substring matching? Start simple, evolve based on error analysis.
- **Temporal query detection:** Should we classify queries as temporal/non-temporal before applying temporal signals? Or always apply and let RRF handle it? Benchmark both.
- **Batch re-clustering frequency:** Run Louvain/agglomerative refinement on every reindex, or only when chunk count grows by >20%? Start with every reindex.
