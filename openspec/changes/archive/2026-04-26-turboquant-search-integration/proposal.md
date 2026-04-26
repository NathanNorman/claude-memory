## Why

The Python MCP server does brute-force float32 cosine similarity over all embeddings. At 92K chunks this takes ~50ms, but the vision is indexing all 3,373 Toast repos (~2.8M chunks). Float32 brute-force at that scale would require ~4GB RAM and >1s latency. TurboQuant (WHT rotation + Lloyd-Max 4-bit quantization) already exists in `src/quantize.py` and is partially wired into `VectorSearchBackend`, but the integration is inline, untested at scale, and lacks a production-ready quantization pipeline. This change extracts a clean `TurboQuantBackend`, adds an offline quantization pipeline, and benchmarks for the 2.8M-chunk target.

## What Changes

- Extract `TurboQuantBackend` class from the inline quantization logic in `VectorSearchBackend.search()`, implementing the same `search()` interface
- Add `VECTOR_BACKEND=turboquant` env var toggle (default remains `float32`)
- Create offline quantization script that pre-computes packed 4-bit sidecar files from DB embeddings
- Add memory-mapped float32 reranking matrix (only ~50 rows paged per query)
- Benchmark search latency and recall at 92K and projected 2.8M chunks
- Memory budget: 4-bit at 2.8M chunks = ~512MB RAM for packed vectors

## Capabilities

### New Capabilities
- `turboquant-backend`: TurboQuant vector search backend with three-stage pipeline (binary -> 4-bit -> float32 rerank) and env-var toggle
- `quantize-pipeline`: Offline quantization tooling to pre-compute packed vectors and sidecar files from existing DB embeddings

### Modified Capabilities

## Impact

- `src/unified_memory_server.py` — new `TurboQuantBackend` class, backend selection logic in `UnifiedMemoryServer`
- `src/quantize.py` — no changes (already complete)
- `scripts/migrate_to_quantized.py` — enhanced to produce sidecar files
- New sidecar files in `~/.claude-memory/index/` (packed vectors, mmap rerank matrix)
- Python venv: no new dependencies (numpy, sentence-transformers already present)
