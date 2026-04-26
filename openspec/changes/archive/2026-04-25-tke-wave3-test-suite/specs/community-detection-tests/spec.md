## ADDED Requirements

### Requirement: Louvain community detection tests
The test suite SHALL verify that compute_communities() correctly clusters a synthetic graph.

#### Scenario: Three-cluster graph produces 3 communities
- **GIVEN** a graph with 3 densely-connected clusters and sparse bridge edges
- **WHEN** compute_communities() is called
- **THEN** 3 communities are detected, each containing nodes from the same cluster

#### Scenario: Results stored in communities table
- **GIVEN** a successful community computation
- **WHEN** the communities table is queried
- **THEN** every node has a community_id and updated_at timestamp

#### Scenario: Community meta stored for staleness detection
- **GIVEN** a successful computation on 80 edges producing 3 communities
- **WHEN** the community_meta table is queried
- **THEN** edge_count=80, community_count=3, computed_at is recent

#### Scenario: Empty edge set returns error
- **GIVEN** a DB with no call/import edges for the codebase
- **WHEN** compute_communities() is called
- **THEN** returns dict with 'error' key

### Requirement: Community staleness detection tests
The test suite SHALL verify _communities_are_stale() detects edge drift.

#### Scenario: Stale when never computed
- **GIVEN** no community_meta row for the codebase
- **WHEN** _communities_are_stale() is called
- **THEN** returns True

#### Scenario: Not stale within threshold
- **GIVEN** community_meta with edge_count=100, current edges=105
- **WHEN** _communities_are_stale() is called
- **THEN** returns False (5% drift < 10% threshold)

#### Scenario: Stale beyond threshold
- **GIVEN** community_meta with edge_count=100, current edges=115
- **WHEN** _communities_are_stale() is called
- **THEN** returns True (15% drift > 10% threshold)

### Requirement: community_search MCP tool mode tests
The test suite SHALL verify all three community_search modes return correct results.

#### Scenario: File lookup returns community members
- **GIVEN** communities stored with file A in community 0
- **WHEN** community_search is called with file_path="A"
- **THEN** returns all files in community 0, sorted by degree

#### Scenario: list_all returns all communities with representatives
- **GIVEN** 3 stored communities
- **WHEN** community_search is called with list_all=True
- **THEN** returns 3 entries with file_count and top-3 representative_files each

#### Scenario: show_bridges returns cross-community edges
- **GIVEN** edges between community 0 and community 1
- **WHEN** community_search is called with show_bridges=True
- **THEN** returns those bridge edges with source/target community IDs
