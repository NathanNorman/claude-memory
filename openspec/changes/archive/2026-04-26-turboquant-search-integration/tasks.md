## 1. Extract TurboQuantBackend class

- [ ] 1.1 Create `TurboQuantBackend` class in `unified_memory_server.py` with `search(query, limit)` interface
- [ ] 1.2 Implement sidecar file loading: `packed_vectors.bin` (numpy fromfile), `rerank_matrix.f32` (mmap), `quantization.json` (metadata)
- [ ] 1.3 Implement three-stage search: binary Hamming -> 4-bit dot products -> float32 mmap rerank
- [ ] 1.4 Remove inline quantization branches from `VectorSearchBackend.search()` (restore to pure float32 brute-force)

## 2. Backend selection and fallback

- [ ] 2.1 Add `VECTOR_BACKEND` env var reading in `UnifiedMemoryServer.__init__()` to select backend
- [ ] 2.2 Implement fallback: if sidecar files missing when turboquant selected, log warning and use float32
- [ ] 2.3 Wire selected backend into the RRF merge path (replace hardcoded `VectorSearchBackend` reference)

## 3. Quantization pipeline

- [ ] 3.1 Add `--sidecar` flag to `scripts/migrate_to_quantized.py` to produce sidecar files
- [ ] 3.2 Implement `packed_vectors.bin` writer: concatenated fixed-size packed vectors with rowid mapping
- [ ] 3.3 Implement `rerank_matrix.f32` writer: float32 matrix written as flat binary for mmap
- [ ] 3.4 Implement `quantization.json` writer: metadata including codebook, rowid map, vector count
- [ ] 3.5 Add `--update` flag for incremental re-quantization (append-only for new chunks)

## 4. Validation and benchmarks

- [ ] 4.1 Add post-quantization validation to the pipeline script (recall@10 on 20 sample queries)
- [ ] 4.2 Write benchmark script: measure search latency at 92K chunks (float32 vs turboquant)
- [ ] 4.3 Run recall benchmark: 100 random queries, compare turboquant top-10 vs float32 top-10
- [ ] 4.4 Document projected memory and latency at 2.8M chunks based on 92K measurements

## 5. Integration and testing

- [ ] 5.1 Add integration test: `TurboQuantBackend` returns results matching float32 for known queries
- [ ] 5.2 Test fallback path: verify float32 is used when sidecar files absent
- [ ] 5.3 Test `memory_write` path: verify newly written chunks are searchable (float32 fallback for unquantized rowids)
- [ ] 5.4 Update CLAUDE.md with TurboQuant backend documentation and env var reference
