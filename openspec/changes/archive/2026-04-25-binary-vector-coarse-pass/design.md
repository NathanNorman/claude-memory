## Context

The unified-memory vector search currently uses a two-stage pipeline: TurboQuant 4-bit approximate dot products over all vectors, then float32 exact reranking of top-30 candidates. At 2.8M vectors (768d), the TurboQuant coarse pass must unpack 4-bit indices and compute centroid lookups for every vector on every query. Binary quantization (1-bit per dimension) enables a much cheaper first pass using Hamming distance (XOR + popcount), which operates on raw bytes without any unpacking or lookup tables.

Current data flow:
1. `_ensure_index()` loads all embedding BLOBs into `_packed_list` (4-bit) and optionally `_matrix` (float32)
2. `search()` runs TurboQuant dot products on all N vectors, reranks top-30 with float32
3. `embed_written_chunks()` and `codebase-index.py` generate and store embeddings during indexing

The binary layer slots in before TurboQuant, reducing the candidate set from N to 1000 before any centroid lookups happen.

## Goals / Non-Goals

**Goals:**
- Add binary (1-bit) quantization as a coarse filter before TurboQuant reranking
- Reduce query latency by narrowing the TurboQuant pass from all-N to ~1000 candidates
- Maintain backward compatibility -- databases without binary vectors continue to work
- Generate binary vectors during both batch indexing and runtime `memory_write`

**Non-Goals:**
- Replacing TurboQuant -- binary quantization has lower recall than 4-bit, so it serves as a filter, not a replacement
- Optimizing the binary pass with SIMD/C extensions -- numpy's vectorized XOR+popcount is sufficient for this scale
- Adding binary support to the Node.js indexer -- only the Python paths (MCP server + codebase-index.py) are in scope
- Supporting non-sign-based binary quantization (e.g., learned hash functions)

## Decisions

### 1. Sign-based binary quantization (threshold at zero)

Binary vectors are computed as `np.packbits(vector > 0, axis=1)`. This is the simplest and most common approach -- each dimension becomes 1 if positive, 0 if negative.

**Why not learned hashing (LSH, ITQ)?** Sign-based quantization achieves 93-96% recall retention (per MixedBread benchmarks) and is deterministic, parameter-free, and essentially zero-cost to compute. Learned methods add training complexity for marginal gains at this recall target (we rerank anyway).

**Why not threshold at the mean?** For normalized embeddings, the mean per dimension is approximately zero, so `> 0` and `> mean` are nearly equivalent.

### 2. Three-stage pipeline with fixed candidate counts

- Stage 1 (binary): all N vectors -> top 1000
- Stage 2 (TurboQuant 4-bit): 1000 -> top 50
- Stage 3 (float32 dequantize): 50 -> top k

**Why 1000 for Stage 1?** Binary quantization retains ~95% recall, but individual ranking quality is noisy. 1000 candidates gives ample headroom for TurboQuant to recover any misranked results. At 1000 vectors, the TurboQuant batch dot product takes ~1-3ms -- negligible.

**Why 50 for Stage 2 (not 30)?** The existing two-stage pipeline uses rerank_k=30 when TurboQuant sees all vectors. When TurboQuant only sees 1000 binary-filtered candidates, slightly widening to 50 compensates for any recall loss from the binary filter.

**Why not adaptive counts?** Fixed counts are simpler to reason about, benchmark, and debug. The performance difference between adaptive and fixed is negligible at these scales.

### 3. Nullable `embedding_binary` column (not a separate table)

Binary vectors are stored in the existing `chunks` table as a nullable BLOB column. This mirrors how `embedding` (float32/TurboQuant) is already stored.

**Why not a separate table?** A join on every read adds complexity. The binary BLOB is only 96 bytes per row -- trivial storage overhead. Nullable column means old rows without binary data still work.

**Why not pack binary into the existing embedding column?** The embedding column already has dual-purpose logic (float32 vs TurboQuant detected by BLOB size). Adding a third format would make the size-detection heuristic fragile. A separate column is explicit.

### 4. Contiguous numpy array for binary matrix

All binary vectors are loaded into a single `np.ndarray` of shape `(N, packed_dims)` at startup, stored as `self._binary_matrix`. Hamming distance is computed as `np.bitwise_xor(query, matrix)` followed by `np.unpackbits(...).sum(axis=1)`.

**Why not memory-mapped?** At 2.8M x 96 bytes = ~268MB, the binary matrix fits comfortably in RAM. Memory mapping adds complexity (file management, page faults) for no benefit at this size.

### 5. Binary vectors generated from pre-rotation float32 embeddings

Binary quantization uses the original (unrotated) embedding, not the WHT-rotated version. The rotation is designed to spread information evenly for scalar quantization -- it doesn't improve sign-based binary quantization, which only needs the sign of each dimension.

## Risks / Trade-offs

**[Recall degradation from binary filter]** Binary quantization discards magnitude information, keeping only sign. At 768 dimensions, this retains ~95% recall for top-1000 retrieval.
-> Mitigation: TurboQuant Stage 2 on 1000 candidates recovers most lost recall. The 50->k exact reranking catches the rest. Net recall impact is negligible for top-10.

**[Memory increase]** Binary matrix adds ~268MB at 2.8M vectors.
-> Mitigation: This is modest compared to the existing TurboQuant matrix (~1.4GB) and float32 matrix (~8.6GB at 768d). Total memory budget is still reasonable for a local MCP server.

**[Schema migration on existing databases]** Adding `embedding_binary` column requires an ALTER TABLE.
-> Mitigation: `ALTER TABLE ADD COLUMN` with a nullable column is safe in SQLite, even on large tables -- it's a metadata-only operation with no data rewrite. Run in `_ensure_conn()` alongside existing migration logic.

**[Incremental population]** Existing vectors won't have binary embeddings until re-indexed.
-> Mitigation: The three-stage pipeline falls back to two-stage when `_binary_matrix` is not fully populated. Binary vectors can be backfilled from existing float32 embeddings without re-running the embedding model.
