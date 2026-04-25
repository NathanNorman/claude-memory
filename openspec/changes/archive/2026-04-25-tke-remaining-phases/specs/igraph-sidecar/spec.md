## ADDED Requirements

### Requirement: igraph graph loading from SQLite
The system SHALL load edges from the SQLite `edges` table into an igraph directed graph on MCP server startup. The graph SHALL be scoped to a single codebase when specified, or all codebases when unspecified. Node IDs SHALL be file paths (strings). Edge attributes SHALL include `edge_type` and `metadata`.

#### Scenario: Server startup with edges
- **WHEN** the MCP server starts and the edges table contains >0 rows
- **THEN** an igraph directed graph is built from all edges with non-NULL target_file, and a log message reports the node and edge counts

#### Scenario: Server startup with empty edges table
- **WHEN** the MCP server starts and the edges table is empty
- **THEN** igraph loading is skipped and graph_traverse falls back to recursive CTEs

### Requirement: igraph-backed graph_traverse
The system SHALL use igraph for `graph_traverse` queries when the igraph graph is loaded and the requested codebase is present in the graph. The system SHALL fall back to recursive CTEs when igraph is unavailable or the codebase is not loaded.

#### Scenario: Downstream traversal via igraph
- **WHEN** `graph_traverse` is called with `direction=downstream` and igraph is loaded for the target codebase
- **THEN** the system uses igraph BFS from the start node, following outgoing edges, and returns reachable nodes with depth

#### Scenario: Edge type filtering via igraph
- **WHEN** `graph_traverse` is called with `edge_types=calls` and igraph is loaded
- **THEN** only edges with `edge_type=calls` are traversed

#### Scenario: Fallback to CTE when igraph unavailable
- **WHEN** `graph_traverse` is called and igraph failed to load
- **THEN** the system uses the existing recursive CTE implementation transparently

### Requirement: igraph graph refresh
The system SHALL rebuild the igraph graph when SIGHUP is received or after a reindex completes. The rebuild SHALL be atomic — the old graph remains queryable until the new one is ready.

#### Scenario: SIGHUP triggers rebuild
- **WHEN** the MCP server process receives SIGHUP
- **THEN** the igraph graph is rebuilt from the current edges table without interrupting in-flight queries

#### Scenario: Automatic rebuild after reindex
- **WHEN** the indexer completes a reindex and writes new edges
- **THEN** the next `graph_traverse` call detects staleness and triggers a background rebuild

### Requirement: Memory-bounded graph loading
The system SHALL enforce a configurable maximum edge count (default 5M) when loading the igraph graph. Edges exceeding the limit SHALL be skipped with a warning.

#### Scenario: Edge count exceeds limit
- **WHEN** the edges table contains 6M edges and the limit is 5M
- **THEN** only 5M edges are loaded (ordered by updated_at DESC) and a warning is logged
