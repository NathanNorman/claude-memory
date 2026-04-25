## 1. Database Indexes

- [ ] 1.1 Add composite index creation (`idx_edges_target_type`, `idx_edges_source_type`) to server startup in `unified_memory_server.py`, using `CREATE INDEX IF NOT EXISTS` followed by `ANALYZE`

## 2. Symbol Resolution Helper

- [ ] 2.1 Add a helper function `_resolve_start_node(conn, start_node, codebase)` that checks if start_node contains `/` or `.` -- if not, queries the `symbols` table to resolve to file path(s); returns list of file paths or raises an error if no symbol found

## 3. Core graph_traverse Tool

- [ ] 3.1 Add the `graph_traverse` MCP tool function with parameters: `start_node`, `codebase`, `direction`, `edge_types`, `max_depth`, `max_results`, `include_paths`
- [ ] 3.2 Implement parameter validation and safety clamping (max_depth capped at 10, max_results capped at 500, defaults of 5 and 100)
- [ ] 3.3 Implement edge_types parsing (comma-separated string to list, build `IN (...)` clause or omit if empty)
- [ ] 3.4 Implement Mode A: reachability query using `UNION`-based recursive CTE with direction-aware column selection, depth limiting, and `GROUP BY file ORDER BY MIN(depth)`
- [ ] 3.5 Implement Mode B: path-tracking query using `UNION ALL`-based recursive CTE with `INSTR`-based cycle detection and path string accumulation
- [ ] 3.6 Implement response formatting: build result dict with `start`, `direction`, `edge_types`, `nodes_found`, `results` array, and optional `paths` array
- [ ] 3.7 Wire in symbol resolution from task 2.1 -- call `_resolve_start_node` before building the CTE query, handle multi-file start nodes

## 4. Validation

- [ ] 4.1 Syntax-check the modified `unified_memory_server.py` with `py_compile`
- [ ] 4.2 Manually test with the existing SQLite database to verify the tool registers and executes without error
