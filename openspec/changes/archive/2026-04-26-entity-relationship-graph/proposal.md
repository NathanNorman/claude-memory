## Why

The `chunk_entities` table has 562K extracted entities (tools, projects, persons) but no way to browse them or discover relationships. Users cannot answer "who works on toast-analytics?" or "what tools does Sarah use?" without manual search. Co-occurrence data (entities appearing in the same chunk) encodes implicit relationships that are currently invisible.

## What Changes

- New `entity_relationships` table storing co-occurrence edges between entities (entity_a, relation_type, entity_b, chunk_id, confidence)
- Co-occurrence extraction during `index_written_file` -- when multiple entities appear in the same chunk, relationship rows are created
- New `entity_browse` MCP tool to list and search entities by type with occurrence counts
- New `entity_graph` MCP tool to find related entities via co-occurrence traversal
- Backfill script to populate relationships from existing 562K chunk_entities rows

## Capabilities

### New Capabilities
- `entity-browse`: MCP tool to list, filter, and search entities by type with occurrence counts
- `entity-graph`: MCP tool to traverse entity co-occurrence relationships and find related entities

### Modified Capabilities

(none)

## Impact

- `src/unified_memory_server.py` -- new table in `_ensure_dep_tables()`, new co-occurrence logic in `index_written_file`, two new MCP tool handlers
- `~/.claude-memory/index/memory.db` -- new `entity_relationships` table, new indexes
- New backfill script in `scripts/`
