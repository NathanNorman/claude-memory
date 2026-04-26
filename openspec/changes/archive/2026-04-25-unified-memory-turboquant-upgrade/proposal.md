## Why

The unified-memory vector backend stores 2,115 chunks as float32 embeddings in 384 dimensions using all-MiniLM-L6-v2, a small model with limited retrieval quality. The current scale (3.2MB, brute-force cosine similarity) works but constrains what's practical: fine-grained indexing, codebase embedding, and higher-quality embedding models all multiply vector count or dimensionality to the point where uncompressed float32 storage becomes wasteful. TurboQuant-style vector quantization (random rotation + scalar codebook, zero overhead) reduces storage 4-6x per vector, making it practical to store 100K+ vectors at higher dimensions on the same memory budget — unlocking better retrieval quality and new data sources without infrastructure changes.

## What Changes

- Add a vector quantization layer to `VectorSearchBackend` that compresses embeddings from float32 to 3-4 bit quantized representations using TurboQuant's random rotation + Lloyd-Max codebook approach
- Add a background bulk indexer script that can embed and index large corpora (codebases, fine-grained conversation splits) as a one-time batch job
- Add a codebase embedding source that indexes functions/classes from configured git repos
- Support configurable embedding models (swap from all-MiniLM-L6-v2 to higher-quality models like bge-small-en-v1.5 or bge-base-en-v1.5)
- Implement finer-grained chunking for conversation archives (paragraph/exchange level instead of multi-exchange blocks)
- **BREAKING**: Quantized embeddings require a one-time full reindex to convert existing float32 BLOBs to quantized format

## Capabilities

### New Capabilities
- `vector-quantization`: TurboQuant-style compression of embedding vectors — random rotation matrix, precomputed Lloyd-Max codebook, quantized storage in SQLite, approximate cosine similarity search with quality guarantees
- `bulk-indexer`: Background batch indexing script for large corpora — runs overnight, supports progress tracking, incremental updates via git hooks
- `codebase-embedding`: Index functions/classes from configured git repos as searchable vectors — AST-aware chunking for Python, file-level for others

### Modified Capabilities
_(no existing specs to modify)_

## Impact

- **Code**: `unified_memory_server.py` (VectorSearchBackend class — storage format, search, index loading), Node.js indexer (`embeddings.ts` — embedding generation, `db.ts` — schema changes for quantized storage)
- **Database**: `chunks.embedding` column format changes from raw float32 BLOB to quantized BLOB + metadata; new `quantization_meta` table for rotation matrix and codebook storage; schema migration required
- **Dependencies**: numpy (already present), no new Python deps for basic quantization; optional model upgrade adds larger sentence-transformers model to the venv
- **Compatibility**: Both Python MCP server and Node.js indexer must agree on quantization format — the dual-embedding-path architecture (Xenova for Node.js, sentence-transformers for Python) must produce compatible quantized representations
