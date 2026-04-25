## Why

claude-memory's hybrid search (FTS5 keyword + vector cosine + RRF) scores 87.3% on LoCoMo but temporal questions score only 68.8% and multi-hop lags behind leaders. Top-performing systems (Hindsight 89.6%, EverMemOS 93.1%) use 4-way retrieval: semantic + keyword + entity/graph + temporal. We have 2 of the 4 channels. Adding entity overlap and temporal proximity as additional RRF signals — plus scene clustering for context expansion — should close the gap to 91-93% without any LLM calls at retrieval time.

The approach is experiment-driven: modify the retrieval-only benchmark first (~10 min/run, zero API cost), measure R@5/R@10 per category for each variant, keep what improves scores, discard what doesn't. Only port winning config to production after validation.

## What Changes

- Add **temporal date extraction** at index time: resolve relative dates ("yesterday", "last week") against session timestamps using `dateparser`, store as `event_date` column on chunks
- Add **temporal proximity scoring** as a new retrieval signal merged via RRF
- Add **NER-based entity extraction** at index time using spaCy `en_core_web_sm`, store entity→chunk mappings in `chunk_entities` table
- Add **entity overlap scoring** as a new retrieval signal merged via RRF
- Add **semantic scene clustering** via nearest-centroid assignment on embeddings, store in `scenes` + `chunk_scenes` tables
- Add **scene expansion** post-RRF: expand retrieved chunks to include cluster neighbors
- Add **score-gated query expansion**: if top RRF score falls below threshold, retry with keyword variants
- Extend `merge_rrf` to accept N ranked lists (currently 2)
- New Python dependencies: `spacy`, `en_core_web_sm`, `dateparser`
- Backfill migration script for existing indexed chunks

## Capabilities

### New Capabilities
- `temporal-retrieval`: Extract and index temporal metadata from chunks; score temporal proximity at search time
- `entity-retrieval`: Extract named entities from chunks via NER; score entity overlap at search time
- `scene-clustering`: Group related chunks into semantic scenes via embedding clustering; expand search results with scene neighbors
- `retrieval-experiment-harness`: Extend the retrieval-only benchmark to support A/B comparison of retrieval configurations

### Modified Capabilities
- `index-file`: Add entity extraction, date extraction, and scene assignment to the indexing pipeline

## Impact

- **Python MCP server** (`src/unified_memory_server.py`): New retrieval signals in `memory_search`, N-way RRF merge, scene expansion post-processing
- **SQLite schema** (`src/db.ts` + Python server): 3 new tables (`chunk_entities`, `scenes`, `chunk_scenes`), 1 new column (`chunks.event_date`)
- **Dependencies**: `spacy` (~12MB model), `dateparser` (pure Python) added to graphiti-venv
- **Benchmark scripts** (`benchmarks/locomo_retrieval_bench.py`): Extended with temporal/entity/scene retrieval modes for experimentation
- **Node.js indexer**: Not modified — all new extraction happens in Python. New tables are readable by both runtimes.
- **Backward compatible**: Existing search behavior unchanged when new signals return no results. New tables are additive.
