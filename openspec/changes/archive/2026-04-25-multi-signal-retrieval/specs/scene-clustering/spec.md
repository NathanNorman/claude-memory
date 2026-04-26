## ADDED Requirements

### Requirement: Chunks SHALL be assigned to semantic scenes
The system SHALL group related chunks into scenes via nearest-centroid clustering on pre-computed embeddings. A chunk SHALL be assigned to the scene whose centroid has highest cosine similarity above threshold τ, or a new singleton scene SHALL be created if no scene exceeds τ.

#### Scenario: Assignment to existing scene
- **WHEN** a new chunk's embedding has cosine similarity 0.78 to Scene A's centroid and τ=0.70
- **THEN** the chunk SHALL be assigned to Scene A and the centroid SHALL be updated incrementally

#### Scenario: New scene creation
- **WHEN** a new chunk's embedding has max cosine similarity 0.62 to any existing scene centroid and τ=0.70
- **THEN** a new singleton scene SHALL be created with the chunk's embedding as the initial centroid

#### Scenario: Centroid update
- **WHEN** a chunk is assigned to a scene with n existing members and centroid μ
- **THEN** the new centroid SHALL be (n·μ + e_new)/(n+1), re-normalized to unit length

### Requirement: Search results SHALL be expanded with scene neighbors
After RRF merge produces top-k results, the system SHALL look up the scene membership of each result chunk and include additional chunks from the same scenes that are not already in results, scored by similarity to the query.

#### Scenario: Scene expansion adds relevant context
- **WHEN** RRF returns chunk C1 from Scene A, and Scene A contains chunks C1, C2, C3
- **AND** C2 and C3 are not already in the top-k results
- **THEN** C2 and C3 SHALL be scored against the query and added to results if above a minimum similarity threshold

#### Scenario: Expansion limit
- **WHEN** scene expansion produces candidates
- **THEN** at most 5 expansion candidates SHALL be added to preserve result quality

### Requirement: Scene clustering SHALL use no LLM calls
All clustering operations SHALL use pre-computed chunk embeddings and cosine similarity. No LLM API calls SHALL be made for scene assignment or expansion.

#### Scenario: Clustering performance
- **WHEN** assigning a new chunk with k existing scenes
- **THEN** assignment SHALL complete in O(k) time (sub-millisecond for k < 1000)

### Requirement: Clustering threshold SHALL be tunable
The threshold τ SHALL be configurable and benchmarked at values 0.60, 0.65, 0.70, 0.75, 0.80 to find the optimal setting.

#### Scenario: Threshold sweep
- **WHEN** running the retrieval benchmark with τ=0.65 vs τ=0.75
- **THEN** R@5 and R@10 SHALL be reported per-category for each threshold
