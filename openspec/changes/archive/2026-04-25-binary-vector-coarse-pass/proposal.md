## Why

Vector search at scale (2.8M vectors at 768d) is bottlenecked by the TurboQuant 4-bit coarse pass, which must unpack and compute dot products across all vectors. Binary quantization (1-bit per dimension) enables Hamming distance via XOR + popcount, which is 15-45x faster than packed dot products and reduces the candidate set before TurboQuant reranking even begins.

## What Changes

- Add `quantize_binary()` and `hamming_distance()` functions to `src/quantize.py` for 1-bit vector quantization and batch Hamming distance computation
- Add `embedding_binary BLOB` nullable column to the `chunks` table for storing packed binary vectors (768d -> 96 bytes)
- Convert `VectorSearchBackend` from two-stage to three-stage search pipeline:
  1. Binary Hamming distance over all vectors -> top 1000 candidates
  2. TurboQuant 4-bit dot products on 1000 candidates -> top 50
  3. Float32 exact cosine similarity on 50 candidates -> top k
- Load binary vectors into a contiguous numpy array at startup (~268MB for 2.8M vectors)
- Generate binary embeddings alongside existing embeddings during indexing in `codebase-index.py` and `embed_written_chunks()`
- Graceful fallback: if binary vectors are unavailable, use existing two-stage pipeline

## Capabilities

### New Capabilities
- `binary-vector-search`: Binary (1-bit) quantization layer providing a fast Hamming-distance coarse pass before TurboQuant reranking. Covers quantization functions, the `embedding_binary` column, three-stage search pipeline, and binary vector generation during indexing.

### Modified Capabilities

## Impact

- **`src/quantize.py`**: New `quantize_binary()` and `hamming_distance()` functions
- **`src/unified_memory_server.py`**: `VectorSearchBackend` gains `_binary_matrix` field, three-stage search in `search()`, binary blob generation in `embed_written_chunks()`, schema migration for `embedding_binary` column, updated `_ensure_index()` to load binary vectors, updated `_invalidate_index()` and `get_stats()`
- **`scripts/codebase-index.py`**: `embed_and_store_batch()` generates and stores binary embedding alongside existing embedding
- **Database**: New nullable `embedding_binary BLOB` column on `chunks` table (backward compatible, no migration needed for reads)
- **Memory**: Additional ~268MB for binary matrix at 2.8M vectors (trivial alongside existing TurboQuant and float32 matrices)
