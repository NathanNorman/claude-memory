## Why

The existing `dependency_search` tool only supports single-hop queries -- direct importers or imports of a given file. Real-world questions like "what is the blast radius of changing this file?", "trace this endpoint back to the database layer", and "find all transitive implementors" require multi-hop traversal over the edges table. Without this, users must manually chain multiple `dependency_search` calls and stitch results together.

## What Changes

- Add a new MCP tool `graph_traverse` that performs multi-hop graph queries over the `edges` table using SQLite recursive CTEs
- Support both reachability queries (fast, returns nodes + depth) and path-tracking queries (returns full traversal paths)
- Accept a symbol name or file path as the start node, with automatic symbol-to-file resolution
- Add composite indexes on the `edges` table to make recursive traversal performant
- Hard safety caps (max_depth=10, max_results=500) to prevent runaway queries

## Capabilities

### New Capabilities
- `graph-traverse`: Multi-hop graph traversal MCP tool with bidirectional traversal, edge-type filtering, configurable depth, and optional path tracking over the existing edges table

### Modified Capabilities

## Impact

- **Code**: `src/unified_memory_server.py` -- new `graph_traverse` tool function added alongside existing `dependency_search` and `symbol_search`
- **Database**: Two new indexes on the `edges` table (`idx_edges_target_type`, `idx_edges_source_type`), created at startup with `CREATE INDEX IF NOT EXISTS`
- **Dependencies**: None -- pure SQLite recursive CTEs, no new packages
- **APIs**: New MCP tool exposed to Claude Code sessions; no breaking changes to existing tools
