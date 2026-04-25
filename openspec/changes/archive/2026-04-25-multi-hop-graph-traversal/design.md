## Context

The claude-memory MCP server (`src/unified_memory_server.py`) already has a `dependency_search` tool that queries the `edges` table for single-hop relationships (direct imports/importers). The `edges` table stores `(source_file, target_file, edge_type, codebase, metadata)` tuples populated by `scripts/codebase-index.py`. The `symbols` table maps symbol names to file paths. Both tables are in the same SQLite database (`~/.claude-memory/index/memory.db`).

The server uses `FlatSearchBackend._ensure_conn()` to get a SQLite connection with WAL mode and `busy_timeout=5000`. All existing tools follow the same pattern: validate inputs, get connection, execute query, format results as a dict.

## Goals / Non-Goals

**Goals:**
- Enable multi-hop traversal queries over the edges table (blast radius, transitive dependencies, trace paths)
- Two query modes: fast reachability (nodes + depth) and path tracking (full traversal paths)
- Symbol-name start nodes resolved automatically via the symbols table
- Performant on large graphs via composite indexes and depth/result caps

**Non-Goals:**
- Cross-codebase traversal (each query scoped to a single codebase)
- Weighted/ranked traversal (all edges treated equally within a type filter)
- Real-time graph updates (indexes built at codebase-index time, not on the fly)
- Replacing `dependency_search` (it remains for simple single-hop lookups)

## Decisions

### 1. SQLite recursive CTEs for traversal

**Choice:** Use `WITH RECURSIVE` CTEs rather than application-level BFS.

**Rationale:** SQLite's CTE engine handles cycle detection (via `UNION` dedup) and depth limiting natively. Benchmarks from the ctxgraph project show ~0.3ms for depth-5 BFS on 196K edges. Keeps all logic in a single query -- no round trips between Python and SQLite.

**Alternative considered:** Application-level BFS with repeated single-hop queries. Rejected: O(depth) round trips, harder cycle detection, more code.

### 2. Two query modes: reachability vs path tracking

**Choice:** Default to reachability mode (just nodes + depth). Path tracking opt-in via `include_paths=true`.

**Rationale:** Reachability uses `UNION` which automatically deduplicates and prevents cycles. Path tracking requires `UNION ALL` with explicit cycle detection via `INSTR(path, node)`, which is more expensive. Most queries just need "what files are affected" not "show me the exact call chain".

**Alternative considered:** Always track paths. Rejected: unnecessary cost for the common case, and path explosion on dense graphs.

### 3. Direction swap via column selection

**Choice:** "upstream" traversal uses `target_file` as the base-case match and walks `source_file`; "downstream" does the reverse.

**Rationale:** In the edges table, `source_file` imports/calls `target_file`. So "what depends on X" (upstream) means finding rows where X is the target. This matches the existing `dependency_search` convention (`imported_by` = upstream, `imports` = downstream).

### 4. Symbol-to-file resolution as a pre-step

**Choice:** If `start_node` does not contain `/` or `.`, treat it as a symbol name and resolve to file(s) via the symbols table before traversal.

**Rationale:** Users often think in terms of class/function names, not file paths. Resolution is a single indexed lookup. If multiple files match, traverse from all of them (union of results).

### 5. Composite indexes on edges table

**Choice:** Add `idx_edges_target_type(target_file, edge_type, codebase)` and `idx_edges_source_type(source_file, edge_type, codebase)` at server startup via `CREATE INDEX IF NOT EXISTS`.

**Rationale:** The recursive CTE's base case and recursive step both filter on (file + codebase + optional edge_type). Without indexes, each recursion level requires a full table scan. With them, each step is an index seek. Running `ANALYZE` after creation ensures the query planner uses them.

### 6. Safety caps

**Choice:** Hard caps at `max_depth=10` and `max_results=500`, enforced with `min()` in Python before query execution.

**Rationale:** Unbounded recursive CTEs on dense graphs can produce millions of rows. The caps are generous enough for real queries (most blast-radius analysis is useful to depth 3-5) while preventing accidental resource exhaustion.

## Risks / Trade-offs

- **[Dense graph explosion]** A file at the center of a large codebase could transitively reach thousands of files even at depth 5. Mitigation: `max_results` cap truncates output; users can reduce depth or add edge_type filters.
- **[Path tracking memory]** Path strings grow linearly with depth. At depth 10 with long file paths, individual path strings could reach ~2KB. Mitigation: max_results cap limits total rows; this is well within SQLite's capabilities.
- **[Index creation on first startup]** `CREATE INDEX IF NOT EXISTS` + `ANALYZE` on a large edges table could take a few seconds on first run. Mitigation: happens once, idempotent, and runs at server startup (not during queries).
- **[Symbol ambiguity]** A symbol name might match multiple files across different classes. Mitigation: traverse from all matching files; codebase filter narrows scope.
