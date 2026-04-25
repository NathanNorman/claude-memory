## ADDED Requirements

### Requirement: Binary quantization functions
The system SHALL provide `quantize_binary(vectors)` in `src/quantize.py` that converts float32 vectors to packed binary representation (1 bit per dimension) using sign-based thresholding at zero. The system SHALL provide `hamming_distance(binary_query, binary_matrix)` that computes Hamming distances between a single packed binary query and a matrix of packed binary vectors using bitwise XOR.

#### Scenario: Single vector binary quantization
- **WHEN** `quantize_binary()` is called with a float32 array of shape `(1, 768)`
- **THEN** it returns a uint8 array of shape `(1, 96)` where each bit represents the sign of the corresponding dimension (1 if positive, 0 if non-positive)

#### Scenario: Batch vector binary quantization
- **WHEN** `quantize_binary()` is called with a float32 array of shape `(N, 768)`
- **THEN** it returns a uint8 array of shape `(N, 96)` with one packed binary row per input vector

#### Scenario: Hamming distance computation
- **WHEN** `hamming_distance()` is called with a binary query of shape `(1, 96)` and a binary matrix of shape `(N, 96)`
- **THEN** it returns an integer array of shape `(N,)` containing the Hamming distance (number of differing bits) between the query and each row

#### Scenario: Identical vectors have zero Hamming distance
- **WHEN** `hamming_distance()` is called with a query and matrix where the query matches row `i` exactly
- **THEN** the distance at index `i` is 0

### Requirement: Binary embedding storage column
The `chunks` table SHALL have an `embedding_binary BLOB` nullable column for storing packed binary vectors. The column SHALL be added via `ALTER TABLE ADD COLUMN` during connection initialization, which is safe and backward-compatible in SQLite.

#### Scenario: Column migration on existing database
- **WHEN** the server connects to a database that lacks the `embedding_binary` column
- **THEN** the column is added automatically without disrupting existing data

#### Scenario: Column already exists
- **WHEN** the server connects to a database that already has the `embedding_binary` column
- **THEN** no error occurs and the column is left as-is

### Requirement: Three-stage search pipeline
When binary vectors are available for all indexed vectors, `VectorSearchBackend.search()` SHALL use a three-stage pipeline: (1) Hamming distance over all binary vectors selecting top 1000 candidates, (2) TurboQuant 4-bit dot products on those 1000 selecting top 50, (3) exact float32 cosine similarity on those 50 selecting top k results.

#### Scenario: Full three-stage search
- **WHEN** a search query is executed and `_binary_matrix` is fully populated
- **THEN** Stage 1 computes Hamming distances on all N binary vectors and selects the 1000 closest, Stage 2 computes TurboQuant dot products on those 1000 and selects the top 50, and Stage 3 computes exact cosine similarity on those 50 to return the final top-k results

#### Scenario: Stage 1 candidate count with small index
- **WHEN** the total number of indexed vectors is less than 1000
- **THEN** Stage 1 passes all vectors through to Stage 2 (no filtering)

#### Scenario: Stage 2 candidate count with fewer than 50 from Stage 1
- **WHEN** Stage 1 produces fewer than 50 candidates
- **THEN** Stage 2 passes all candidates through to Stage 3

### Requirement: Fallback to two-stage pipeline
When binary vectors are not available (missing column, partially populated, or legacy database), the system SHALL fall back to the existing two-stage pipeline (TurboQuant over all vectors, then float32 reranking).

#### Scenario: No binary vectors in database
- **WHEN** a search is executed on a database where no rows have `embedding_binary` populated
- **THEN** the system uses the existing two-stage pipeline without error

#### Scenario: Partially populated binary vectors
- **WHEN** some but not all rows have `embedding_binary` populated
- **THEN** the system falls back to the two-stage pipeline (binary matrix requires full coverage to be valid)

### Requirement: Binary matrix loading at startup
`_ensure_index()` SHALL load all `embedding_binary` BLOBs into a contiguous numpy uint8 array (`_binary_matrix`) of shape `(N, packed_dims)`. The binary matrix SHALL only be used if every indexed vector has a corresponding binary embedding.

#### Scenario: All vectors have binary embeddings
- **WHEN** `_ensure_index()` loads embeddings and every row with a non-null `embedding` also has a non-null `embedding_binary`
- **THEN** `_binary_matrix` is populated and `_binary_available` is set to True

#### Scenario: Some vectors missing binary embeddings
- **WHEN** `_ensure_index()` finds rows where `embedding` is non-null but `embedding_binary` is null
- **THEN** `_binary_matrix` is not populated and `_binary_available` is set to False

### Requirement: Binary vector generation during indexing
Both `embed_written_chunks()` (runtime MCP writes) and `codebase-index.py` (batch indexing) SHALL generate and store binary embeddings alongside existing embeddings. Binary vectors SHALL be computed from the original (unrotated) normalized float32 embedding.

#### Scenario: Runtime memory_write generates binary embedding
- **WHEN** `embed_written_chunks()` generates an embedding for a new chunk
- **THEN** it also computes and stores the binary embedding in the `embedding_binary` column

#### Scenario: Batch codebase indexing generates binary embedding
- **WHEN** `embed_and_store_batch()` in `codebase-index.py` processes a chunk
- **THEN** it computes and stores the binary embedding in the `embedding_binary` column alongside the quantized (or float32) embedding

#### Scenario: Binary computed from unrotated embedding
- **WHEN** a binary embedding is generated
- **THEN** it is computed from the normalized float32 vector before any WHT rotation is applied

### Requirement: Index invalidation includes binary matrix
`_invalidate_index()` SHALL clear `_binary_matrix` and `_binary_available` alongside the existing fields, so that the next search reloads all data from the database.

#### Scenario: Index invalidation after write
- **WHEN** `_invalidate_index()` is called (e.g., after `embed_written_chunks`)
- **THEN** `_binary_matrix` is set to None and `_binary_available` is set to False

### Requirement: Stats include binary vector information
`get_stats()` SHALL report whether binary vectors are available and the count of binary-populated rows.

#### Scenario: Stats with binary vectors
- **WHEN** `get_stats()` is called and binary vectors are loaded
- **THEN** the result includes `binary_available: true` and `binary_vectors: <count>`

#### Scenario: Stats without binary vectors
- **WHEN** `get_stats()` is called and binary vectors are not available
- **THEN** the result includes `binary_available: false`
