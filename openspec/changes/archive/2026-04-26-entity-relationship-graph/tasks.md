## 1. Schema & Table Setup

- [ ] 1.1 Add `entity_relationships` table creation to `_ensure_dep_tables()` in `unified_memory_server.py` with columns: entity_a TEXT, relation_type TEXT, entity_b TEXT, chunk_id TEXT, confidence REAL DEFAULT 1.0
- [ ] 1.2 Add indexes on entity_relationships: idx_entity_rel_a (entity_a), idx_entity_rel_b (entity_b), idx_entity_rel_chunk (chunk_id)

## 2. Co-occurrence Extraction

- [ ] 2.1 Add `extract_co_occurrences(entities, chunk_id)` helper function that generates canonical pairs (entity_a < entity_b) from a list of entities
- [ ] 2.2 Call co-occurrence extraction after entity insertion in `index_written_file`, inserting rows into entity_relationships
- [ ] 2.3 Add deletion of entity_relationships by chunk_id in the cleanup section of `index_written_file` (alongside chunk_entities deletion)

## 3. entity_browse MCP Tool

- [ ] 3.1 Register `entity_browse` tool with FastMCP (type: optional str, query: optional str, limit: optional int default 50)
- [ ] 3.2 Implement SQL query: SELECT entity_value, entity_type, COUNT(*) as count FROM chunk_entities with optional WHERE filters, GROUP BY, ORDER BY count DESC
- [ ] 3.3 Return formatted results with entity_value, entity_type, count

## 4. entity_graph MCP Tool

- [ ] 4.1 Register `entity_graph` tool with FastMCP (entity: str, depth: optional int default 1, limit: optional int default 20)
- [ ] 4.2 Implement depth-1 query: find entities that share entity_relationships rows with the given entity, aggregated by co-occurrence count
- [ ] 4.3 Implement depth-2 query: union depth-1 results with second-hop neighbors, tagging each result with its depth level
- [ ] 4.4 Resolve entity_type for each result via chunk_entities lookup

## 5. Backfill Script

- [ ] 5.1 Create `scripts/backfill-entity-relationships.py` that reads chunk_entities grouped by chunk_id
- [ ] 5.2 Generate pairwise co-occurrence rows with canonical ordering and batch-insert
- [ ] 5.3 Add progress reporting (chunks processed, relationships created) and idempotency (DELETE all + re-insert, or clear table first)

## 6. Testing

- [ ] 6.1 Add integration test: write a memory file, verify entity_relationships rows are created for co-occurring entities
- [ ] 6.2 Add integration test: entity_browse returns correct counts and respects type/query filters
- [ ] 6.3 Add integration test: entity_graph returns correct depth-1 and depth-2 results
- [ ] 6.4 Run backfill script against live database, verify relationship counts are reasonable
