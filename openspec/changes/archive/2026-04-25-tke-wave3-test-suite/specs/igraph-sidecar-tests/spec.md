## ADDED Requirements

### Requirement: GraphSidecar load and traverse tests
The test suite SHALL verify that GraphSidecar correctly loads edges from SQLite, performs BFS traversal, and handles edge cases.

#### Scenario: Load edges into igraph
- **GIVEN** a SQLite DB with 50 edges across 20 nodes
- **WHEN** GraphSidecar.load() is called
- **THEN** the graph contains 20 nodes and 50 edges, and is_loaded is True

#### Scenario: Downstream BFS traversal
- **GIVEN** a loaded graph with path A→B→C→D
- **WHEN** traverse(start_file="A", direction="downstream", max_depth=3) is called
- **THEN** results contain B (depth 1), C (depth 2), D (depth 3)

#### Scenario: Upstream BFS traversal
- **GIVEN** a loaded graph with path A→B→C
- **WHEN** traverse(start_file="C", direction="upstream", max_depth=2) is called
- **THEN** results contain B (depth 1), A (depth 2)

#### Scenario: Edge type filtering
- **GIVEN** a graph with both "calls" and "import" edges from A
- **WHEN** traverse(start_file="A", edge_types=["calls"]) is called
- **THEN** only nodes reachable via "calls" edges are returned

#### Scenario: Empty graph fallback
- **GIVEN** a SQLite DB with no edges
- **WHEN** GraphSidecar.load() is called
- **THEN** is_loaded is False and traverse returns empty list

### Requirement: GraphSidecar staleness detection tests
The test suite SHALL verify staleness detection triggers rebuild when edge count drifts >10%.

#### Scenario: No staleness when unchanged
- **GIVEN** a loaded graph with 100 edges, and the DB still has 100 edges
- **WHEN** is_stale() is called
- **THEN** returns False

#### Scenario: Staleness detected at >10% drift
- **GIVEN** a loaded graph with 100 edges, and the DB now has 115 edges
- **WHEN** is_stale() is called
- **THEN** returns True

### Requirement: GraphSidecar memory cap tests
The test suite SHALL verify that edge loading respects the max_edges limit.

#### Scenario: Edge limit enforced
- **GIVEN** a DB with 200 edges and MAX_EDGES set to 50
- **WHEN** GraphSidecar.load() is called
- **THEN** the graph contains at most 50 edges

### Requirement: GraphSidecar atomic rebuild tests
The test suite SHALL verify that rebuild() swaps the graph atomically.

#### Scenario: Successful rebuild
- **GIVEN** a loaded graph, then new edges are added to the DB
- **WHEN** rebuild() is called
- **THEN** the new graph reflects the added edges

#### Scenario: Failed rebuild preserves old graph
- **GIVEN** a loaded graph
- **WHEN** rebuild() is called but loading fails (e.g., corrupt DB path)
- **THEN** the old graph is still usable and is_loaded remains True
