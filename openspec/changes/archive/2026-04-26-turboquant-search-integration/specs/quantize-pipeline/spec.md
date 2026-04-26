## ADDED Requirements

### Requirement: Sidecar file generation
The quantization pipeline SHALL produce sidecar files from existing DB embeddings via `migrate_to_quantized.py --sidecar`.

#### Scenario: Generate sidecar from 92K chunks
- **WHEN** `python3 scripts/migrate_to_quantized.py --sidecar` is run against a DB with 92K float32 embeddings
- **THEN** three files are created in `~/.claude-memory/index/`: `packed_vectors.bin`, `rerank_matrix.f32`, `quantization.json`

#### Scenario: Sidecar metadata format
- **WHEN** sidecar files are generated
- **THEN** `quantization.json` SHALL contain: model_name, dims, bit_width, rotation_seed, codebook (base64), vector_count, rowid_map (rowid -> offset), created_at

### Requirement: Incremental re-quantization
The pipeline SHALL support incremental updates when new chunks are added.

#### Scenario: Append new vectors
- **WHEN** `--sidecar --update` is run after new chunks are indexed
- **THEN** only new/changed chunks are quantized and appended to sidecar files; existing packed vectors are preserved

### Requirement: Database backup before migration
The pipeline SHALL create a WAL-safe backup before any destructive migration.

#### Scenario: Backup creation
- **WHEN** `migrate_to_quantized.py` is run
- **THEN** a backup is written to `~/.claude-memory/backups/` before any writes

### Requirement: Validation report
The pipeline SHALL output a validation summary after quantization.

#### Scenario: Post-quantization validation
- **WHEN** sidecar generation completes
- **THEN** the script prints: vector count, packed file size, compression ratio, and recall@10 on 20 random sample queries vs float32 ground truth
