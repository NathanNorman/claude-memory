## ADDED Requirements

### Requirement: Matryoshka model switch
The system SHALL switch the codebase embedding model from CodeRankEmbed (768d, no MRL) to nomic-embed-text-v1.5 (768d, MRL-capable). Embeddings SHALL be stored at a configurable truncated dimension (default 256d).

#### Scenario: Model migration detection
- **WHEN** `codebase-index.py` runs and detects the stored model name differs from the configured model
- **THEN** all codebase chunks are purged and a full re-index is triggered automatically

#### Scenario: Dimension truncation
- **WHEN** embeddings are generated with nomic-embed-text-v1.5
- **THEN** the full 768d vector is computed, truncated to the configured dimension (256d), L2-normalized, and stored

### Requirement: Configurable embedding dimensions
The system SHALL support a `--dims` flag on `codebase-index.py` to control the stored embedding dimension. Valid values: 64, 128, 256, 384, 512, 768 (must be a Matryoshka checkpoint).

#### Scenario: Custom dimension via CLI
- **WHEN** `codebase-index.py` is run with `--dims 128`
- **THEN** embeddings are truncated to 128d before storage, and the dimension is recorded in the meta table

#### Scenario: Python server dimension detection
- **WHEN** the MCP server starts and reads embedding BLOBs
- **THEN** the server auto-detects the embedding dimension from BLOB size and adjusts its query embedding truncation to match

### Requirement: Query embedding dimension matching
The Python MCP server SHALL truncate query embeddings to match the stored dimension before computing cosine similarity.

#### Scenario: 256d stored embeddings
- **WHEN** a `memory_search` query is executed with `source=codebase`
- **THEN** the query embedding is computed at full dimension, truncated to 256d, L2-normalized, and compared against stored 256d vectors

### Requirement: Storage reduction verification
After migration, the system SHALL report embedding storage reduction in the indexing output.

#### Scenario: Migration complete
- **WHEN** a full re-index completes with 256d embeddings (previously 768d)
- **THEN** the output reports the storage reduction (e.g., "Embedding storage: 3.0x reduction (768d → 256d)")
