## Context

claude-memory extracts entities (tool, project, person) into `chunk_entities` during indexing. The `EntityRetrieval` scorer uses these for search-time entity-overlap boosting. However, there is no way to browse entities directly or discover relationships between them. The 562K existing entity rows encode implicit co-occurrence relationships (entities in the same chunk are related) that are untapped.

## Goals / Non-Goals

**Goals:**
- Enable browsing and searching the entity catalog with occurrence counts
- Surface entity relationships derived from chunk co-occurrence
- Integrate relationship extraction into the existing write-time indexing path
- Backfill relationships from existing chunk_entities data

**Non-Goals:**
- NLP-based relationship type inference (e.g., "works on", "uses") -- all relationships are co-occurrence for now
- Entity deduplication or normalization (e.g., merging "sarah chen" and "Sarah Chen")
- Graph visualization UI

## Decisions

### 1. Co-occurrence as relationship type

All relationships are `co-occurrence` -- two entities appearing in the same chunk. This avoids NLP complexity while still capturing meaningful signal. The `relation_type` column allows future expansion to typed relationships.

**Alternative considered:** LLM-based relationship extraction. Rejected because it would add latency to write path and cost per chunk.

### 2. Relationship table with chunk_id foreign key

Store `(entity_a, relation_type, entity_b, chunk_id, confidence)` rather than aggregated counts. Keeping chunk_id allows tracing back to source material and computing freshness-weighted scores later.

**Alternative considered:** Aggregated edge table `(entity_a, entity_b, count)`. Rejected because it loses provenance and temporal signal.

### 3. Symmetric storage with canonical ordering

Store each relationship once with `entity_a < entity_b` (lexicographic). Query both directions by checking either column. This halves storage vs. storing both directions.

### 4. Write-time extraction in index_written_file

After inserting chunk_entities, compute pairwise co-occurrences for that chunk and insert into entity_relationships. For a chunk with N entities, this creates N*(N-1)/2 rows. Typical chunks have 2-5 entities so this is manageable.

### 5. Backfill as standalone Python script

`scripts/backfill-entity-relationships.py` iterates chunk_entities grouped by chunk_id, generates co-occurrence pairs, and batch-inserts. Runs once, idempotent.

## Risks / Trade-offs

- **Quadratic growth per chunk**: A chunk with 20 entities creates 190 relationship rows. Mitigated by the reality that most chunks have <5 entities.
- **No deduplication**: "sarah chen" and "Sarah Chen" are already normalized to lowercase in `extract_entities`, so this is handled at extraction time.
- **Stale relationships**: If chunk_entities are deleted during re-index, orphaned relationships remain. Mitigated by deleting relationships by chunk_id alongside chunk_entities deletion in `index_written_file`.
