# Tasks

## Phase 1: Vector Quantization Core

- [x] **T1: Implement TurboQuant quantization module** — Spec: vector-quantization R1, R2 — File: `~/claude-memory/src/quantize.py` (new). Create a standalone Python module with: `generate_rotation(dims, seed)` (Walsh-Hadamard transform with random sign flips, O(d log d) without materializing full matrix), `compute_codebook(dims, bit_width)` (Lloyd-Max for Beta((d-1)/2, (d-1)/2), precompute for b=1,2,3,4; Panter-Dite for b>4), `quantize(vector, rotation_fn, codebook)` (rotate, nearest centroid per coordinate, packed uint8 array), `dequantize(packed, rotation_fn, codebook)` (unpack, lookup, inverse-rotate), `quantized_dot_product(query_rotated, packed, codebook)` (dot product in rotated space without full dequantization). Unit tests: verify round-trip distortion ≤ 0.009 at b=4, verify quantized dot product matches exact within tolerance.

- [x] **T2: Add `quantization_meta` table and migration** — Spec: vector-quantization R4 — File: `~/claude-memory/src/unified_memory_server.py` (modify DB init). Add `quantization_meta` table creation to `_ensure_conn()` or a migration function. Schema: id, model_name, dims, bit_width, rotation_seed, codebook (BLOB), created_at. On first quantized write, generate seed + codebook and insert row. Migration: detect if table exists, create if not (backward compatible).

- [x] **T3: Modify VectorSearchBackend to support quantized embeddings** — Spec: vector-quantization R3, R5, R6 — File: `~/claude-memory/src/unified_memory_server.py` (VectorSearchBackend class). `_ensure_index()`: detect BLOB size to distinguish float32 vs quantized; load accordingly. For quantized: load packed arrays + codebook, pre-rotate codebook for fast search. `search()`: rotate query, use `quantized_dot_product` against all stored vectors. Mixed-mode: convert any remaining float32 BLOBs to quantized on-the-fly during index load. `embed_written_chunks()`: after generating float32 embedding, quantize and store packed format.

- [x] **T4: Make embedding model configurable** — Spec: vector-quantization R7 — File: `~/claude-memory/src/unified_memory_server.py`. Read `MEMORY_EMBEDDING_MODEL` env var (default: `all-MiniLM-L6-v2`). Read dims from model config (not hardcoded `EMBEDDING_DIMS = 384`). On model change: `meta` table version mismatch triggers re-embed warning in logs. Update `_ensure_model()` to use the configured model name.

- [x] **T5: Migration script — convert float32 embeddings to quantized** — Spec: vector-quantization R6 — File: `~/claude-memory/scripts/migrate_to_quantized.py` (new). Read all float32 BLOBs from `chunks.embedding`. Generate rotation seed + codebook, write to `quantization_meta`. Quantize all vectors in batches of 1000. Write packed arrays back to `chunks.embedding`. Verify: compare top-10 search results before/after for a set of test queries. Auto-backup DB before migration (copy to `~/.claude-memory/backups/`).

## Phase 2: Bulk Indexer

- [x] **T6: Create bulk indexer script** — Spec: bulk-indexer R1, R2, R3, R4 — File: `~/claude-memory/scripts/bulk_index.py` (new). CLI with argparse: `--source`, `--repo`, `--model`, `--progress`, `--background`. Load embedding model once, embed in batches of 32. After embedding each batch, quantize and write to DB. Use `embedding_cache` content hash for deduplication. Progress logging to stderr; tqdm if `--progress` flag. SIGINT handler: commit current batch before exit.

- [x] **T7: Background execution and lock coordination** — Spec: bulk-indexer R5, R6 — File: `~/claude-memory/scripts/bulk_index.py` (extend). `--background`: daemonize via double-fork, write PID file, redirect output to log. Acquire/release `reindex.lock` per-batch (not for entire run). Stale lock detection: reclaim locks older than 5 minutes (match Node.js indexer behavior).

## Phase 3: Codebase Embedding

- [x] **T8: Codebase source configuration** — Spec: codebase-embedding R1 — File: `~/claude-memory/scripts/bulk_index.py` (extend `--source codebase`). Read `~/.claude-memory/codebase-sources.json`. Validate paths exist, expand `~`. If config missing, skip codebase indexing with info log.

- [x] **T9: Python AST chunker** — Spec: codebase-embedding R2 — File: `~/claude-memory/src/code_chunker.py` (new). `chunk_python_file(path)` returns list of chunks (title, content, start_line, end_line). Use `ast.parse()` to extract top-level `FunctionDef`, `AsyncFunctionDef`, `ClassDef`. Include decorators and docstrings in chunk content. Skip functions shorter than 3 lines. Nested functions included in parent, not separate.

- [x] **T10: File-level chunker for other languages** — Spec: codebase-embedding R3 — File: `~/claude-memory/src/code_chunker.py` (extend). `chunk_file(path)` returns list of chunks. If file ≤ 200 lines: one chunk (title=filename, content=full file). If file > 200 lines: split at blank-line boundaries into ~100-150 line chunks. Dispatch: `.py` → AST chunker, everything else → file-level chunker.

- [x] **T11: Git integration for incremental codebase indexing** — Spec: codebase-embedding R4, R5 — File: `~/claude-memory/scripts/bulk_index.py` (extend). Run `git ls-files` in each configured repo to get file list. Store indexed commit SHA per repo in `files` table. On re-run: `git diff --name-only <stored-sha>..HEAD` to find changed files. Delete chunks for removed files, re-embed changed files. Prefix file_path with `codebase/<repo-name>/` for namespace isolation.

- [x] **T12: Integration test — end-to-end quantized search** — File: `~/claude-memory/scripts/test_quantized_search.py` (new). Generate 1000 random 384-dim vectors, quantize, store in test DB. Run 100 queries, compare top-10 results against exact brute-force. Assert recall@10 ≥ 95%. Test mixed-mode (some float32, some quantized) returns correct results. Test codebase chunk ingestion → search → result includes code snippets.
