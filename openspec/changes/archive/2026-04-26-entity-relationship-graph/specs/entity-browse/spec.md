## ADDED Requirements

### Requirement: Entity browse MCP tool

The system SHALL expose an `entity_browse` MCP tool that lists entities from the `chunk_entities` table with occurrence counts.

Parameters:
- `type` (optional string): filter by entity_type ("tool", "project", "person"). If omitted, return all types.
- `query` (optional string): substring match against entity_value (case-insensitive).
- `limit` (optional int, default 50): max results returned.

The tool SHALL return entities sorted by occurrence count descending. Each result SHALL include `entity_value`, `entity_type`, and `count`.

#### Scenario: Browse all entities
- **WHEN** `entity_browse()` is called with no parameters
- **THEN** the system returns up to 50 entities sorted by occurrence count descending, each with entity_value, entity_type, and count

#### Scenario: Filter by type
- **WHEN** `entity_browse(type="tool")` is called
- **THEN** only entities with entity_type="tool" are returned

#### Scenario: Search by query substring
- **WHEN** `entity_browse(query="toast")` is called
- **THEN** only entities whose entity_value contains "toast" (case-insensitive) are returned

#### Scenario: Empty results
- **WHEN** `entity_browse(type="person", query="zzzznonexistent")` is called
- **THEN** the system returns an empty list with a message indicating no matches

### Requirement: Entity relationships table

The system SHALL create an `entity_relationships` table with columns: `entity_a TEXT`, `relation_type TEXT`, `entity_b TEXT`, `chunk_id TEXT`, `confidence REAL DEFAULT 1.0`. Entities SHALL be stored in canonical order (`entity_a < entity_b` lexicographically).

#### Scenario: Table creation
- **WHEN** the MCP server starts
- **THEN** `entity_relationships` table exists with appropriate indexes on entity_a and entity_b

### Requirement: Write-time co-occurrence extraction

The system SHALL extract co-occurrence relationships during `index_written_file`. For each chunk, all pairs of distinct entities SHALL produce a relationship row with `relation_type='co-occurrence'`.

#### Scenario: Two entities in same chunk
- **WHEN** a chunk contains entities "toast-analytics" (project) and "sarah chen" (person)
- **THEN** an entity_relationships row is created with entity_a="sarah chen", entity_b="toast-analytics", relation_type="co-occurrence", chunk_id set to the chunk's id

#### Scenario: Chunk re-index cleans old relationships
- **WHEN** a file is re-indexed via `index_written_file`
- **THEN** old entity_relationships rows for that file's chunks are deleted before new ones are inserted
