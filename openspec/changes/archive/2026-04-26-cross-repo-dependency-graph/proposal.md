## Why

claude-memory has intra-repo dependency edges (imports, calls, extends) via SCIP parsing, but no inter-repo dependency edges. Questions like "which repos depend on toast-common?" or "what are the transitive dependencies of toast-analytics?" are unanswerable. Build files already declare these relationships -- we just need to parse them.

## What Changes

- New script `scripts/cross-repo-deps.py` that scans locally cloned repos, parses build files (Gradle, Maven, npm, pip), and extracts declared dependencies as `repo_dependency` edges
- Edges stored in the existing `edges` table with `edge_type='repo_dependency'`, `source_file=<repo_name>`, `target_file=<dependency_identifier>`
- `GraphSidecar` loads `repo_dependency` edges for repo-level traversal
- `dependency_search` MCP tool enhanced with `direction='repo_depends_on'` and `direction='repo_depended_on_by'` for repo-level queries

## Capabilities

### New Capabilities
- `build-file-parser`: Regex-based parsers for Gradle, Maven, npm, and pip build files that extract declared dependency identifiers
- `repo-dependency-search`: Repo-level dependency queries via `dependency_search` tool and `GraphSidecar` traversal

### Modified Capabilities

## Impact

- `src/unified_memory_server.py` -- `dependency_search` tool gains two new direction values; `GraphSidecar` loads repo-level edges
- `scripts/cross-repo-deps.py` -- new script (standalone, no server changes needed to run)
- `edges` table -- new rows with `edge_type='repo_dependency'`; no schema changes
