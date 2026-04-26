## Context

`VectorSearchBackend` (line 665 of `unified_memory_server.py`) currently has three search paths inline: float32 brute-force, two-stage TurboQuant, and three-stage binary+TurboQuant. The quantization logic is mixed into the single class, making it hard to test, toggle, or benchmark independently. `src/quantize.py` provides all primitives (WHT rotation, Lloyd-Max codebook, 4-bit packing, batch dot products, binary Hamming). `scripts/migrate_to_quantized.py` converts float32 BLOBs in-place but doesn't produce sidecar files for startup-time loading.

Current state at 92K chunks: float32 matrix = ~135MB RAM, search ~50ms. At 2.8M chunks: float32 = ~4GB RAM, search >1s.

## Goals / Non-Goals

**Goals:**
- Extract `TurboQuantBackend` with same `search(query, limit) -> list[dict]` interface as `VectorSearchBackend`
- Toggle via `VECTOR_BACKEND` env var: `float32` (default), `turboquant`
- Sidecar file format: pre-packed 4-bit vectors + mmap-able float32 rerank matrix
- Memory at 2.8M chunks: ~512MB (packed) + mmap rerank (paged on demand)
- Search latency target: <100ms at 2.8M chunks
- Recall@10 >= 0.998 vs float32 ground truth

**Non-Goals:**
- GPU acceleration or FAISS/Annoy integration
- Changing the embedding model or dimensionality
- Modifying the Node.js indexer or FTS5 keyword backend
- Quantization-aware training

## Decisions

**1. Separate class vs refactoring VectorSearchBackend**
Extract `TurboQuantBackend` as a peer class rather than subclass. The float32 path stays untouched in `VectorSearchBackend` (remove the inline quantized branches). Both implement `search(query, limit)`. The `UnifiedMemoryServer` selects based on env var. This keeps the float32 path as a safe fallback.

Alternative: Subclass with override. Rejected because the index loading, memory layout, and search path are fundamentally different.

**2. Sidecar files vs DB-stored packed BLOBs**
Use sidecar `.bin` files in `~/.claude-memory/index/`:
- `packed_vectors.bin` — concatenated 4-bit packed vectors (fixed 192 bytes each for 384-dim)
- `rerank_matrix.f32` — float32 matrix, memory-mapped
- `quantization.json` — metadata (model, dims, bit_width, seed, vector count, codebook)

Alternative: Store packed BLOBs in the chunks table. Rejected because loading 2.8M BLOBs from SQLite at startup is slow; a single mmap is O(1).

**3. Three-stage pipeline**
Binary Hamming (all N -> top 1000) -> TurboQuant 4-bit dot products (1000 -> top 50) -> float32 rerank (50 -> top k). The binary pass is critical at 2.8M scale — Hamming on packed bits is ~10x faster than 4-bit dot products.

**4. Quantization pipeline as enhancement to migrate_to_quantized.py**
Extend the existing script to also produce sidecar files. Add `--sidecar` flag. The script already handles backup, codebook generation, and batch quantization.

## Risks / Trade-offs

- [Sidecar staleness] If chunks are added via `memory_write` after quantization, sidecar falls behind -> Mitigation: `TurboQuantBackend` falls back to float32 for rowids not in sidecar; periodic re-quantization via cron
- [Memory at 2.8M] 512MB for packed + mmap overhead -> Mitigation: mmap rerank matrix is paged by OS; only ~50 float32 rows loaded per query
- [Startup latency] Loading sidecar files at server start -> Mitigation: numpy `fromfile` for packed vectors is fast; mmap for rerank is O(1)
- [Recall degradation at scale] 4-bit may lose precision on tail queries -> Mitigation: rerank_k=50 with float32 exact reranking; benchmarked at 0.998 recall@10

## Open Questions

- Should `memory_write` hot-patch the sidecar (append new packed vectors) or defer to next quantization run?
- Threshold for auto-switching: should the server auto-select turboquant when chunk count exceeds N?
