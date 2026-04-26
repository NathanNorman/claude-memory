## ADDED Requirements

### Requirement: Multi-hop graph traversal tool
The system SHALL expose a `graph_traverse` MCP tool that performs multi-hop traversal over the `edges` table using SQLite recursive CTEs. The tool SHALL accept `start_node`, `codebase`, `direction`, `edge_types`, `max_depth`, `max_results`, and `include_paths` parameters.

#### Scenario: Basic upstream reachability query
- **WHEN** `graph_traverse` is called with `start_node="src/payments/processor.kt"`, `codebase="my-app"`, `direction="upstream"`, `max_depth=3`
- **THEN** the tool returns all files that transitively depend on `processor.kt` up to 3 hops, each with its minimum depth and the edge type that connected it

#### Scenario: Basic downstream reachability query
- **WHEN** `graph_traverse` is called with `start_node="src/api/handler.kt"`, `codebase="my-app"`, `direction="downstream"`, `max_depth=5`
- **THEN** the tool returns all files that `handler.kt` transitively depends on up to 5 hops

### Requirement: Direction controls traversal orientation
The system SHALL support two directions: `upstream` (find files that depend on the start node, walking source_file from target_file matches) and `downstream` (find files the start node depends on, walking target_file from source_file matches). The default direction SHALL be `downstream`.

#### Scenario: Upstream walks reverse dependency edges
- **WHEN** `graph_traverse` is called with `direction="upstream"` and `start_node="src/db/schema.kt"`
- **THEN** the recursive CTE base case matches rows where `target_file = start_node` and returns `source_file` values, then recurses by matching `target_file = previous_result`

#### Scenario: Downstream walks forward dependency edges
- **WHEN** `graph_traverse` is called with `direction="downstream"` and `start_node="src/api/router.kt"`
- **THEN** the recursive CTE base case matches rows where `source_file = start_node` and returns `target_file` values, then recurses by matching `source_file = previous_result`

### Requirement: Edge type filtering
The system SHALL accept an `edge_types` parameter as a comma-separated string (e.g., `"calls,imports"`) to filter traversal to only those edge types. When empty, all edge types SHALL be included.

#### Scenario: Filter to only call edges
- **WHEN** `graph_traverse` is called with `edge_types="calls"`
- **THEN** only edges with `edge_type = 'calls'` are traversed; import edges are ignored

#### Scenario: Multiple edge type filters
- **WHEN** `graph_traverse` is called with `edge_types="calls,imports"`
- **THEN** edges with `edge_type IN ('calls', 'imports')` are traversed

#### Scenario: No edge type filter
- **WHEN** `graph_traverse` is called with `edge_types=""`
- **THEN** all edge types are traversed

### Requirement: Reachability mode returns nodes with depth
When `include_paths` is false (the default), the system SHALL use a `UNION`-based recursive CTE that returns each reachable file with its minimum depth. Results SHALL be ordered by depth ascending, then limited to `max_results`.

#### Scenario: Reachability returns deduplicated nodes
- **WHEN** `graph_traverse` is called with `include_paths=false` and a file is reachable via multiple paths at different depths
- **THEN** the file appears once in results with the minimum depth at which it was reached

#### Scenario: Results ordered by depth
- **WHEN** `graph_traverse` returns results
- **THEN** results are sorted by depth ascending (closest files first)

### Requirement: Path tracking mode returns full traversal paths
When `include_paths` is true, the system SHALL use a `UNION ALL`-based recursive CTE with `INSTR`-based cycle detection that tracks the full path from start node to each reached node. The response SHALL include a `paths` array with each path as an ordered list of file names.

#### Scenario: Path tracking returns traversal chains
- **WHEN** `graph_traverse` is called with `include_paths=true`
- **THEN** the response includes a `paths` array where each entry is a list of files from the traversed node back to the start node

#### Scenario: Cycle detection prevents infinite recursion
- **WHEN** a graph contains a cycle (A -> B -> C -> A)
- **THEN** the path-tracking CTE detects the cycle via `INSTR(path, node)` and stops recursing along that path

### Requirement: Symbol-to-file resolution
When `start_node` does not contain `/` or `.`, the system SHALL treat it as a symbol name and resolve it to file path(s) via the `symbols` table. If multiple files contain the symbol, the system SHALL traverse from all matching files. If the codebase parameter is provided, symbol resolution SHALL be scoped to that codebase.

#### Scenario: Symbol name resolves to file
- **WHEN** `graph_traverse` is called with `start_node="PaymentProcessor"` and `codebase="my-app"`
- **THEN** the system queries `symbols` for `name = 'PaymentProcessor'` in codebase `my-app`, gets the file path(s), and uses those as traversal start points

#### Scenario: Symbol not found returns error
- **WHEN** `graph_traverse` is called with `start_node="NonExistentClass"`
- **THEN** the system returns an error indicating no matching symbol was found

#### Scenario: File path used directly
- **WHEN** `graph_traverse` is called with `start_node="src/main/Foo.kt"`
- **THEN** the system uses the path directly without symbol resolution (because it contains `/` or `.`)

### Requirement: Safety caps on depth and results
The system SHALL enforce a hard maximum depth of 10 and a hard maximum results of 500, regardless of what the caller requests. Values exceeding these caps SHALL be silently clamped.

#### Scenario: Depth exceeds cap
- **WHEN** `graph_traverse` is called with `max_depth=20`
- **THEN** the system clamps `max_depth` to 10

#### Scenario: Results exceeds cap
- **WHEN** `graph_traverse` is called with `max_results=1000`
- **THEN** the system clamps `max_results` to 500

#### Scenario: Default values
- **WHEN** `graph_traverse` is called without specifying `max_depth` or `max_results`
- **THEN** `max_depth` defaults to 5 and `max_results` defaults to 100

### Requirement: Composite indexes on edges table
The system SHALL create two composite indexes at startup: `idx_edges_target_type(target_file, edge_type, codebase)` and `idx_edges_source_type(source_file, edge_type, codebase)`. Index creation SHALL use `CREATE INDEX IF NOT EXISTS` and be followed by `ANALYZE`.

#### Scenario: Indexes created on startup
- **WHEN** the MCP server starts and connects to the database
- **THEN** both indexes exist on the `edges` table and `ANALYZE` has been run

#### Scenario: Indexes are idempotent
- **WHEN** the server restarts and the indexes already exist
- **THEN** `CREATE INDEX IF NOT EXISTS` succeeds without error or duplicate indexes

### Requirement: Response format
The system SHALL return a JSON object with: `start` (the resolved start node(s)), `direction`, `edge_types` (list), `nodes_found` (count), `results` (array of `{file, depth, via_edge}`), and optionally `paths` (array of file-path lists when `include_paths=true`).

#### Scenario: Reachability response format
- **WHEN** `graph_traverse` completes with `include_paths=false`
- **THEN** the response contains `start`, `direction`, `edge_types`, `nodes_found`, and `results` (no `paths` key)

#### Scenario: Path tracking response format
- **WHEN** `graph_traverse` completes with `include_paths=true`
- **THEN** the response contains all reachability fields plus a `paths` array

### Requirement: Backend availability check
The system SHALL return an error dict if the flat search backend is unavailable, consistent with existing tools like `dependency_search`.

#### Scenario: Backend unavailable
- **WHEN** `graph_traverse` is called but `flat_backend` is None
- **THEN** the system returns `{'error': 'Flat search backend not available', 'results': []}`
