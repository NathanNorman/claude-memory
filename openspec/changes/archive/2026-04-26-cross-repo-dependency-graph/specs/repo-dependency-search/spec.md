## ADDED Requirements

### Requirement: Repo-level dependency search directions
The `dependency_search` MCP tool SHALL support two new direction values: `repo_depends_on` (what does repo X depend on) and `repo_depended_on_by` (what repos depend on artifact X).

#### Scenario: Forward repo dependency lookup
- **WHEN** `dependency_search(file_path="toast-analytics", direction="repo_depends_on")` is called
- **THEN** the tool returns all `repo_dependency` edges where `source_file='toast-analytics'`, showing the repo's declared dependencies

#### Scenario: Reverse repo dependency lookup
- **WHEN** `dependency_search(file_path="com.toasttab:toast-common", direction="repo_depended_on_by")` is called
- **THEN** the tool returns all `repo_dependency` edges where `target_file` matches, showing which repos depend on that artifact

#### Scenario: Codebase scoping
- **WHEN** `dependency_search(file_path="toast-common", codebase="toast-analytics", direction="repo_depends_on")` is called
- **THEN** results are filtered to edges from the `toast-analytics` codebase only

### Requirement: GraphSidecar repo-dependency loading
`GraphSidecar` SHALL load `repo_dependency` edges into the igraph graph. Repo names and dependency identifiers SHALL be added as vertices, enabling BFS/DFS traversal across the repo dependency graph.

#### Scenario: Transitive dependency traversal
- **WHEN** `GraphSidecar` is loaded with repo_dependency edges and a BFS query starts from vertex `toast-analytics`
- **THEN** the traversal follows `repo_dependency` edges to discover all direct and transitive dependencies

### Requirement: Edge type filtering
Repo-level queries SHALL respect the `edge_type` filter parameter. When `edge_type='repo_dependency'` is specified, only repo-level edges are returned, excluding intra-repo import/call edges.

#### Scenario: Mixed edge types filtered
- **WHEN** `dependency_search(file_path="toast-analytics", direction="repo_depends_on", edge_type="repo_dependency")` is called
- **THEN** only `repo_dependency` edges are returned, not `imports` or `calls` edges
