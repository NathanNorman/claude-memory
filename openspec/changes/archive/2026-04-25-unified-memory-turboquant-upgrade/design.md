## Context

unified-memory is a personal MCP server combining SQLite FTS5 keyword search with brute-force vector cosine similarity. It indexes ~285 files into ~2,115 chunks with 384-dim float32 embeddings (all-MiniLM-L6-v2). Storage is 3.2MB. The system has two embedding paths: Node.js indexer (Xenova/transformers.js, ONNX) for batch indexing and Python server (sentence-transformers, PyTorch) for query-time embedding. Both produce compatible 384-dim vectors.

The system works well at current scale but is constrained by float32 storage costs if we want to scale to 100K+ vectors (fine-grained chunking, codebase indexing) or upgrade to higher-dimensional embedding models.

TurboQuant (ICLR 2026) provides a proven, simple quantization scheme: random orthogonal rotation → precomputed Lloyd-Max scalar codebook → 3-4 bit storage per dimension, with zero overhead (no per-block zero points or scales). At 4-bit quantization, storage drops 8x per vector vs float32.

## Goals / Non-Goals

**Goals:**
- Compress stored embeddings 4-8x via TurboQuant-style quantization without meaningful retrieval quality loss
- Support 100K+ vectors at up to 1024 dimensions within current memory budget (~50MB)
- Enable swapping to higher-quality embedding models (configurable, not hardcoded)
- Add background bulk indexer for one-time large corpus indexing (codebases, fine-grained re-chunking)
- Maintain backward compatibility during migration (read old float32, write new quantized)

**Non-Goals:**
- GPU acceleration for embedding or search (CPU-only is fine at this scale)
- ColBERT-style multi-vector retrieval (future work, not this change)
- Serving as a general-purpose vector database (this is a personal memory system)
- Real-time streaming indexing (batch + incremental is sufficient)
- QJL residual correction for unbiased inner products (overkill for retrieval — MSE-optimal quantization is sufficient since we're doing top-k recall, not attention score estimation)

## Decisions

### D1: TurboQuant MSE-only, no QJL residual stage

**Choice:** Implement only TurboQuant_mse (random rotation + Lloyd-Max scalar quantization). Skip the QJL 1-bit residual correction (TurboQuant_prod).

**Why:** QJL's unbiased inner product estimation matters for attention score computation in LLMs where softmax amplifies errors. For top-k retrieval (find the 10 most similar vectors), MSE-optimal quantization is sufficient — we need correct ranking, not exact scores. The distortion bound D_mse ≤ 2.7/4^b at b=4 gives <0.01 MSE per coordinate, which preserves ranking reliably.

**Alternative:** Full TurboQuant_prod with QJL. Rejected because it doubles the bit budget (b-1 bits for MSE + 1 bit for QJL) and adds a random projection matrix S, for marginal benefit in a retrieval-only use case.

### D2: 4-bit quantization (16 centroids per coordinate)

**Choice:** Default to 4-bit (b=4) quantization. At b=4, Lloyd-Max distortion is 0.009 per coordinate — essentially lossless for ranking.

**Why:** 4-bit gives 8x compression over float32 (4 bits vs 32 bits per dimension). At 1024 dims, 100K vectors: uncompressed = 400MB, 4-bit = 50MB. Fits comfortably in memory. Going lower (3-bit, 2-bit) saves more but the quality tradeoff isn't worth it for a personal tool.

**Alternative:** 3-bit (≈10.7x compression, 0.03 distortion) or 2-bit (≈16x, 0.117 distortion). Available as config option but not the default.

### D3: Quantization metadata in a separate table, not inline

**Choice:** Store the rotation matrix and codebook centroids in a `quantization_meta` table, not embedded in each row.

**Why:** The rotation matrix Π (d×d orthogonal) and codebook (2^b centroids) are shared across ALL vectors — they're properties of the quantization scheme, not per-vector data. Storing them once saves space and simplifies updates. The `chunks.embedding` column stores only the quantized index array (b bits × d dimensions packed into bytes).

### D4: Python-only quantization, Node.js writes float32 to embedding_cache

**Choice:** The Node.js indexer continues to produce float32 embeddings (Xenova). Quantization happens in the Python server when loading the index or when called by a post-index script.

**Why:** Implementing TurboQuant in both JS and Python creates a compatibility risk (rotation matrix and codebook must be identical). Keeping quantization in Python (numpy) is simpler and numpy already handles the linear algebra. The Node.js indexer writes float32 to `embedding_cache`; a Python post-processing step quantizes and writes to `chunks.embedding`.

**Alternative:** Implement quantization in both languages. Rejected — dual implementation of numerical code invites subtle divergences.

### D5: Configurable embedding model via environment variable

**Choice:** `MEMORY_EMBEDDING_MODEL` env var, defaulting to `all-MiniLM-L6-v2`. Changing the model triggers a full re-embed on next reindex (detected via `meta` table model version).

**Why:** Model upgrades shouldn't require code changes. The `meta` table already tracks embedding model version for invalidation. When model changes, all embeddings are regenerated and re-quantized.

### D6: AST-aware chunking for Python codebases, file-level for others

**Choice:** Use Python's `ast` module to extract function/class definitions for `.py` files. For other languages, chunk at the file level with a size cap.

**Why:** Python is the primary language in the repos being indexed. AST parsing gives clean function-level chunks with docstrings. Adding full multi-language AST parsing (tree-sitter for JS/Java/etc.) is out of scope — file-level chunking with heading detection is good enough for non-Python files.

## Risks / Trade-offs

**[Quantization quality at high query-document angle]** Cosine similarity between near-orthogonal vectors has higher distortion after quantization (π/2 factor from QJL analysis). → Mitigation: At b=4, absolute distortion is 0.009 — negligible even in worst case. And near-orthogonal pairs are low-similarity results that wouldn't be in top-k anyway.

**[Dual embedding path divergence]** Node.js (Xenova/ONNX) and Python (sentence-transformers/PyTorch) produce slightly different float32 values for the same input. After quantization, small float differences could map to different centroids. → Mitigation: Normalize before rotation. The rotation + quantization is applied after embedding, so both paths feed into the same quantization pipeline (Python-only per D4). The float32 → quantized mapping is deterministic given the same input floats within centroid bucket boundaries.

**[Rotation matrix size]** A d×d orthogonal rotation matrix at d=1024 is 1024×1024×4 bytes = 4MB. → Mitigation: Use a structured random rotation (Walsh-Hadamard + random sign flips) instead of a full dense matrix. This uses O(d) storage and O(d log d) compute instead of O(d²). TurboQuant paper notes this is standard practice.

**[Full reindex required on model change]** Changing the embedding model invalidates all existing embeddings. At 100K+ vectors, re-embedding takes ~17 minutes on CPU. → Mitigation: Run as overnight background job. The system gracefully degrades during reindex (FTS5 keyword search still works, vector search returns stale results until cache refresh).

**[One-time migration]** Existing float32 embeddings must be converted to quantized format. → Mitigation: Migration script reads all float32 BLOBs, applies rotation + quantization, writes back. Runs once. Keep a backup of the DB before migration (the indexer already does this).

## Migration Plan

1. **Phase 1 — Quantization layer (no breaking changes):** Add quantization code to Python server. Store quantized embeddings alongside float32. Search uses float32 if available, quantized as fallback. This validates the quantization quality without risk.

2. **Phase 2 — Cutover:** Migration script converts all float32 embeddings to quantized. Remove float32 storage path. Update Node.js indexer to skip embedding storage (Python post-processing handles it). Full reindex.

3. **Phase 3 — New capabilities:** Add bulk indexer script, codebase embedding source, model configurability. These build on the quantized storage layer.

**Rollback:** Restore SQLite DB from `~/.claude-memory/backups/` (auto-created before each reindex). Re-run `node dist/reindex-cli.js` to regenerate float32 embeddings.

## Open Questions

- Should the Walsh-Hadamard rotation be deterministic (seeded) or random? Deterministic is reproducible but may interact poorly with specific embedding model geometry. Random is theoretically optimal but requires storing the seed.
- What's the right chunking granularity for codebases? Function-level seems right for Python but may be too fine for configs/READMEs and too coarse for large functions. Need to experiment.
- Should we support multiple embedding models simultaneously (e.g., keep MiniLM for fast queries, use BGE for high-quality batch indexing)? This complicates the rotation matrix management but could be valuable.
