## ADDED Requirements

### Requirement: Louvain community detection on code graph
The system SHALL run Louvain community detection on the igraph graph (or edge data from SQLite) and store community assignments in a `communities` table. Communities SHALL be computed per codebase.

#### Scenario: Community detection on indexed codebase
- **WHEN** `codebase-index.py` is run with `--communities` flag
- **THEN** Louvain clustering is computed on the call+import edge graph for that codebase, and results are stored in the `communities` table

#### Scenario: Community table schema
- **WHEN** communities are stored
- **THEN** each row contains `codebase`, `file_path`, `community_id` (integer), and `updated_at`

### Requirement: community_search MCP tool
The system SHALL expose a `community_search` MCP tool that returns files grouped by architectural community.

#### Scenario: Search by file
- **WHEN** `community_search` is called with a file path
- **THEN** the system returns all files in the same community, sorted by connectivity (most connected first)

#### Scenario: List all communities
- **WHEN** `community_search` is called with `list_all=true` for a codebase
- **THEN** the system returns all community IDs with their file counts and representative files (top 3 by degree)

#### Scenario: Cross-community edges
- **WHEN** `community_search` is called with `show_bridges=true`
- **THEN** the system returns edges that cross community boundaries, useful for identifying coupling points

### Requirement: Community staleness detection
The system SHALL recompute communities when the edge count for a codebase changes by more than 10% since last computation.

#### Scenario: Stale communities detected
- **WHEN** `community_search` is called and edge count has changed >10% since last community computation
- **THEN** communities are recomputed before returning results, and a note is included in the response
