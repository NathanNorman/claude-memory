## 1. Binary Quantization Functions

- [ ] 1.1 Add `quantize_binary(vectors)` to `src/quantize.py` -- takes float32 array of shape `(N, d)` or `(d,)`, returns packed uint8 array via `np.packbits(vectors > 0, axis=1)`
- [ ] 1.2 Add `hamming_distance(binary_query, binary_matrix)` to `src/quantize.py` -- XOR + unpackbits + sum for batch Hamming distance computation
- [ ] 1.3 Add unit tests for `quantize_binary` (single vector, batch, round-trip sign preservation) and `hamming_distance` (identical vectors = 0, known distance values)

## 2. Schema Migration

- [ ] 2.1 Add `embedding_binary BLOB` nullable column migration in `VectorSearchBackend._ensure_conn()` -- use `ALTER TABLE chunks ADD COLUMN embedding_binary BLOB` with try/except for idempotency (column may already exist)

## 3. Binary Vector Generation During Indexing

- [ ] 3.1 Update `VectorSearchBackend.embed_written_chunks()` to compute and store binary embedding from normalized float32 vector before rotation -- `quantize_binary(emb_arr.reshape(1, -1))` -> store as BLOB in `embedding_binary` column
- [ ] 3.2 Update `embed_and_store_batch()` in `scripts/codebase-index.py` to compute and store binary embedding alongside existing embedding -- add `embedding_binary` to the INSERT statement
- [ ] 3.3 Verify both paths write the correct 96-byte (for 768d) or 48-byte (for 384d) binary BLOB

## 4. Binary Matrix Loading

- [ ] 4.1 Add `_binary_matrix` and `_binary_available` fields to `VectorSearchBackend.__init__()`
- [ ] 4.2 Update `_ensure_index()` to load `embedding_binary` BLOBs into a contiguous numpy uint8 array -- only set `_binary_available = True` if every vector with `embedding` also has `embedding_binary`
- [ ] 4.3 Update `_invalidate_index()` to clear `_binary_matrix` and `_binary_available`

## 5. Three-Stage Search Pipeline

- [ ] 5.1 Update `VectorSearchBackend.search()` -- when `_binary_available` is True, add Stage 1: compute binary query via `quantize_binary()`, run `hamming_distance()` against `_binary_matrix`, select top 1000 (or fewer if index is smaller)
- [ ] 5.2 Pass Stage 1 candidate indices to Stage 2 (TurboQuant dot products on filtered subset only instead of all vectors)
- [ ] 5.3 Stage 2 selects top 50 from the 1000 candidates, passes to Stage 3 (existing exact reranking via dequantize)
- [ ] 5.4 When `_binary_available` is False, fall back to existing two-stage pipeline unchanged

## 6. Stats and Observability

- [ ] 6.1 Update `get_stats()` to include `binary_available` (bool) and `binary_vectors` (count of rows with non-null `embedding_binary`)
- [ ] 6.2 Add log messages in `_ensure_index()` reporting binary matrix loading status (loaded N binary vectors / skipped -- incomplete coverage)

## 7. Verification

- [ ] 7.1 Syntax-check the Python server: `python3 -c "import py_compile; py_compile.compile('src/unified_memory_server.py', doraise=True)"`
- [ ] 7.2 Syntax-check quantize.py: `python3 -c "import py_compile; py_compile.compile('src/quantize.py', doraise=True)"`
- [ ] 7.3 Run existing integration tests: `npm test`
- [ ] 7.4 Manual smoke test: write a memory via MCP, verify `embedding_binary` is populated in the DB, run a search query and confirm three-stage path is logged
