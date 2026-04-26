## ADDED Requirements

### Requirement: Matryoshka truncation math tests
The test suite SHALL verify dimension truncation and L2 renormalization produce correct vectors.

#### Scenario: 768d → 256d truncation
- **GIVEN** a 768-dim normalized vector
- **WHEN** truncated to 256d and L2-renormalized
- **THEN** the result has 256 dimensions and L2 norm ≈ 1.0

#### Scenario: Truncation preserves leading dimensions
- **GIVEN** a vector [0.1, 0.2, 0.3, ..., 0.768]
- **WHEN** truncated to 3 dimensions
- **THEN** result is L2-normalized version of [0.1, 0.2, 0.3]

#### Scenario: No truncation when dims match
- **GIVEN** a 256-dim vector and truncate_dims=256
- **WHEN** truncation is applied
- **THEN** vector is unchanged

### Requirement: embed_and_store_batch truncation integration tests
The test suite SHALL verify embed_and_store_batch stores correctly sized BLOBs.

#### Scenario: Stored BLOB size matches truncated dims
- **GIVEN** a mock model that returns 768-dim vectors, truncate_dims=256
- **WHEN** embed_and_store_batch() is called
- **THEN** stored embedding BLOBs are 256*4=1024 bytes (float32)

#### Scenario: Document prefix prepended
- **GIVEN** doc_prefix="search_document: " and chunk content "hello"
- **WHEN** embed_and_store_batch() is called
- **THEN** the model receives "search_document: <structural_prefix>hello"

### Requirement: Python server dimension auto-detection tests
The test suite SHALL verify the MCP server correctly detects stored embedding dimensions.

#### Scenario: Detect dims from meta table
- **GIVEN** meta table with codebase_embedding_dims=256
- **WHEN** _check_model_version() runs
- **THEN** _codebase_stored_dims is set to 256

#### Scenario: Detect dims from BLOB size fallback
- **GIVEN** no meta row but a codebase chunk with 1024-byte embedding BLOB
- **WHEN** _check_model_version() runs
- **THEN** _codebase_stored_dims is set to 256 (1024/4)

### Requirement: Query embedding dimension matching tests
The test suite SHALL verify query embeddings are truncated to match stored dimension.

#### Scenario: Query truncated from 768d to 256d
- **GIVEN** stored_dims=256 and a 768-dim query vector
- **WHEN** search_codebase() processes the query
- **THEN** cosine similarity is computed in 256-dim space
