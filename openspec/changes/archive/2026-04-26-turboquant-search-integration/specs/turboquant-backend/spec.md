## ADDED Requirements

### Requirement: TurboQuantBackend class
The system SHALL provide a `TurboQuantBackend` class in `unified_memory_server.py` that implements `search(query: str, limit: int) -> list[dict]` with the same return format as `VectorSearchBackend`.

#### Scenario: Search returns same schema as float32
- **WHEN** `TurboQuantBackend.search("python decorator", 10)` is called
- **THEN** each result dict contains keys: id, file_path, chunk_index, start_line, end_line, title, content, score

#### Scenario: Three-stage pipeline execution
- **WHEN** binary index and packed vectors are available
- **THEN** search executes binary Hamming (all N -> 1000) then TurboQuant 4-bit (1000 -> 50) then float32 rerank (50 -> top k)

#### Scenario: Graceful fallback when sidecar missing
- **WHEN** sidecar files do not exist and `VECTOR_BACKEND=turboquant`
- **THEN** the server SHALL log a warning and fall back to `VectorSearchBackend` float32 path

### Requirement: Backend selection via env var
The system SHALL select the vector search backend based on `VECTOR_BACKEND` environment variable.

#### Scenario: Default backend
- **WHEN** `VECTOR_BACKEND` is unset
- **THEN** `VectorSearchBackend` (float32 brute-force) is used

#### Scenario: TurboQuant backend
- **WHEN** `VECTOR_BACKEND=turboquant`
- **THEN** `TurboQuantBackend` is used if sidecar files exist

### Requirement: Sidecar file loading
The `TurboQuantBackend` SHALL load pre-computed sidecar files from `~/.claude-memory/index/` at startup.

#### Scenario: Startup with valid sidecar
- **WHEN** `packed_vectors.bin`, `rerank_matrix.f32`, and `quantization.json` exist
- **THEN** packed vectors are loaded into memory and rerank matrix is memory-mapped

#### Scenario: Memory budget at scale
- **WHEN** 2.8M chunks with 384-dim 4-bit vectors are loaded
- **THEN** packed vector memory SHALL be <= 600MB and rerank matrix SHALL be memory-mapped (not resident)

### Requirement: Recall guarantee
The `TurboQuantBackend` SHALL achieve recall@10 >= 0.99 compared to float32 ground truth.

#### Scenario: Recall benchmark on 92K chunks
- **WHEN** benchmark is run on the current 92K-chunk index
- **THEN** recall@10 >= 0.99 across 100 random queries
