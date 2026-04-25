# Vector Quantization

## Purpose

Compress embedding vectors from float32 (32 bits/dim) to 4-bit quantized representation (4 bits/dim) using TurboQuant_mse: random orthogonal rotation followed by Lloyd-Max scalar quantization with a precomputed codebook. Zero overhead — no per-block zero points or scales stored.

## Requirements

### R1: Quantization Pipeline

- Given a float32 embedding vector x of dimension d, produce a quantized representation using:
  1. Multiply by a shared rotation matrix: y = Π · x (where Π is orthogonal, d×d)
  2. For each coordinate y_j, find the nearest centroid in a precomputed codebook of 2^b centroids
  3. Store the centroid index (b bits per coordinate) as a packed byte array
- The rotation matrix Π must be generated via Walsh-Hadamard transform with random sign flips (O(d) storage, O(d log d) compute) — not a dense d×d matrix
- The codebook must be precomputed by solving the 1D Lloyd-Max problem on the Beta((d-1)/2, (d-1)/2) distribution. For b=1,2,3,4, use numerically computed centroids. For b>4, use the Panter-Dite formula
- Default bit-width: b=4 (16 centroids)

### R2: Dequantization Pipeline

- Given quantized indices and the shared rotation matrix + codebook, reconstruct an approximate float32 vector:
  1. Look up centroid values from indices
  2. Multiply by Π^T (inverse rotation = transpose for orthogonal matrix)
- Dequantization is used for search (reconstruct query comparison targets) or for returning approximate embeddings to callers

### R3: Approximate Cosine Similarity Search

- Search must work directly on quantized vectors without full dequantization
- For a float32 query vector q:
  1. Rotate: q' = Π · q
  2. For each stored quantized vector, compute dot product in rotated space: Σ q'_j · c_{idx_j} where c is the codebook
  3. This avoids dequantizing every stored vector — just a lookup + multiply per dimension
- Return top-k results ranked by approximate cosine similarity
- At b=4, recall@10 must be ≥95% compared to exact float32 brute-force search on the same corpus

### R4: Storage Format

- `chunks.embedding` column: packed byte array of quantized indices (⌈b × d / 8⌉ bytes per vector)
- `quantization_meta` table:
  - `id` INTEGER PRIMARY KEY
  - `model_name` TEXT — embedding model these parameters are for
  - `dims` INTEGER — embedding dimensionality
  - `bit_width` INTEGER — quantization bit width (b)
  - `rotation_seed` INTEGER — seed for deterministic Walsh-Hadamard sign generation
  - `codebook` BLOB — float32 array of 2^b centroids
  - `created_at` TEXT
- One row per (model_name, dims, bit_width) combination
- Rotation matrix is regenerated from seed at load time (not stored as a BLOB)

### R5: Index Loading

- On first search query, load all quantized embeddings into a numpy array (uint8 or packed bits)
- Load codebook and rotation seed from `quantization_meta`
- Generate rotation matrix from seed
- Pre-rotate and cache the codebook lookup table for fast dot product computation
- Cache invalidation: when `chunks` table is modified, invalidate the in-memory index (same as current behavior)

### R6: Backward Compatibility

- During migration (Phase 1): if `chunks.embedding` contains a float32 BLOB (len == dims × 4), use it directly with current brute-force search
- If `chunks.embedding` contains a quantized BLOB (len == ⌈b × dims / 8⌉), use quantized search
- Mixed-mode search (some float32, some quantized) must work correctly — convert float32 to quantized on-the-fly during index load

### R7: Configurable Embedding Model

- `MEMORY_EMBEDDING_MODEL` environment variable, default `all-MiniLM-L6-v2`
- Changing the model triggers full re-embed on next reindex (detected via `meta` table model version check)
- The `VectorSearchBackend.MODEL_NAME` and `EMBEDDING_DIMS` must be read from config, not hardcoded
