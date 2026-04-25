## MODIFIED Requirements

### Requirement: Index file pipeline
The indexing pipeline SHALL, in addition to existing chunking, embedding, FTS5 indexing, and hash-based staleness detection, also perform: (1) named entity extraction via spaCy and storage in `chunk_entities`, (2) temporal date extraction via dateparser and storage in `chunks.event_date`, and (3) scene assignment via nearest-centroid clustering and storage in `chunk_scenes`.

#### Scenario: Full indexing pipeline
- **WHEN** a new markdown file is indexed
- **THEN** the system SHALL chunk the content, generate embeddings, populate FTS5, extract entities, resolve temporal expressions, and assign chunks to scenes

#### Scenario: Incremental indexing preserves new metadata
- **WHEN** a file is re-indexed due to content change (hash mismatch)
- **THEN** old entity mappings, event dates, and scene assignments for that file's chunks SHALL be deleted and regenerated

#### Scenario: Missing extraction does not block indexing
- **WHEN** spaCy or dateparser fails on a specific chunk
- **THEN** that chunk SHALL still be indexed with embeddings and FTS5; entity/temporal/scene fields SHALL be left empty rather than failing the entire file

## ADDED Requirements

### Requirement: Backfill migration script
A one-time migration script SHALL process all existing indexed chunks to populate `chunk_entities`, `event_date`, and `chunk_scenes` tables. The script SHALL be idempotent and resumable.

#### Scenario: Backfill existing chunks
- **WHEN** running the backfill script against a database with 15,000 existing chunks
- **THEN** all chunks SHALL have entity mappings, event dates (where extractable), and scene assignments populated

#### Scenario: Idempotent re-run
- **WHEN** running the backfill script a second time
- **THEN** already-populated chunks SHALL be skipped (no duplicate entities or scene assignments)

### Requirement: New SQLite tables
The database schema SHALL include:
- `chunk_entities` table: (chunk_rowid, entity_text, entity_label, entity_text_original) with indexes on entity_text and entity_label
- `scenes` table: (id, centroid BLOB, member_count, created_at, updated_at)
- `chunk_scenes` table: (chunk_id, scene_id, similarity) with index on scene_id
- `chunks.event_date` column: TEXT (ISO 8601 YYYY-MM-DD), indexed

#### Scenario: Schema migration
- **WHEN** the system starts with an existing database lacking the new tables
- **THEN** the new tables and column SHALL be created automatically without data loss
