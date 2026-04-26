## ADDED Requirements

### Requirement: Entity graph MCP tool

The system SHALL expose an `entity_graph` MCP tool that finds entities related to a given entity via co-occurrence relationships.

Parameters:
- `entity` (required string): the entity_value to start from (case-insensitive lookup).
- `depth` (optional int, default 1, max 2): how many hops to traverse. Depth 1 returns direct co-occurrences. Depth 2 includes co-occurrences of co-occurrences.
- `limit` (optional int, default 20): max related entities returned.

The tool SHALL return related entities sorted by co-occurrence count descending. Each result SHALL include `entity_value`, `entity_type`, `co_occurrence_count`, and `depth` (1 or 2).

#### Scenario: Direct relationships (depth 1)
- **WHEN** `entity_graph(entity="toast-analytics")` is called
- **THEN** the system returns entities that co-occur with "toast-analytics" in at least one chunk, sorted by co-occurrence count descending

#### Scenario: Two-hop traversal (depth 2)
- **WHEN** `entity_graph(entity="toast-analytics", depth=2)` is called
- **THEN** the system returns both direct co-occurrences (depth=1) and entities that co-occur with those direct neighbors (depth=2), with depth=1 results ranked higher

#### Scenario: Entity not found
- **WHEN** `entity_graph(entity="nonexistent-thing")` is called
- **THEN** the system returns an empty list with a message indicating the entity was not found

#### Scenario: Case-insensitive lookup
- **WHEN** `entity_graph(entity="Toast-Analytics")` is called
- **THEN** the system matches against "toast-analytics" (all entities are stored lowercase)

### Requirement: Backfill script

The system SHALL include a `scripts/backfill-entity-relationships.py` script that populates `entity_relationships` from existing `chunk_entities` data. The script SHALL be idempotent (safe to run multiple times) and SHALL report progress.

#### Scenario: Initial backfill
- **WHEN** the backfill script runs on a database with 562K chunk_entities and no entity_relationships
- **THEN** co-occurrence pairs are generated for all chunks and inserted, with a progress report showing chunks processed and relationships created

#### Scenario: Re-run is idempotent
- **WHEN** the backfill script runs again after a successful run
- **THEN** existing relationships are not duplicated (script clears and rebuilds, or uses INSERT OR IGNORE)
