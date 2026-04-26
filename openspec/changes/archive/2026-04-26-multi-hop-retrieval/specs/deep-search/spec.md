## ADDED Requirements

### Requirement: memory_deep_search MCP tool
The system SHALL expose a `memory_deep_search` MCP tool that accepts `query` (string), `maxResults` (int, default 10), and `maxHops` (int, default 1, max 1). It SHALL return a dict with `results` list and `hops_performed` count.

#### Scenario: Basic deep search returns results from both passes
- **WHEN** `memory_deep_search(query="How did search evolve from keyword to hybrid", maxResults=10)` is called
- **THEN** results SHALL include items with `hop: 0` (Pass 1) and `hop: 1` (Pass 2), merged by RRF score descending

#### Scenario: Deep search with no Pass 2 expansion
- **WHEN** Pass 1 top-5 results yield no new entities beyond the original query
- **THEN** the tool SHALL return Pass 1 results only with `hops_performed: 0`

### Requirement: Pass 1 uses existing multi-signal retrieval
Pass 1 SHALL run the same 4-signal retrieval as `memory_search` (keyword, vector, temporal, entity) merged via `merge_rrf_multi()`. Pass 1 SHALL use the same `fetch_limit` calculation as `memory_search`.

#### Scenario: Pass 1 produces same results as memory_search
- **WHEN** `memory_deep_search(query=Q)` is called
- **THEN** Pass 1 results SHALL be identical to `memory_search(query=Q)` results before post-filtering

### Requirement: Pass 2 extracts entities from Pass 1 and retrieves related documents
Pass 2 SHALL call `extract_entities()` on the content of the top-5 Pass 1 results. It SHALL compute new entities as `pass2_entities - query_entities`. If new entities is empty, Pass 2 SHALL be skipped. Otherwise, Pass 2 SHALL run `EntityRetrieval.search()` with the expanded entity set AND `FlatSearchBackend.search_keyword()` with extracted entity values as query terms.

#### Scenario: Entity expansion retrieves related documents
- **WHEN** Pass 1 top-5 results contain entity "sqlite-vec" not in the original query
- **THEN** Pass 2 SHALL retrieve documents mentioning "sqlite-vec" via entity and keyword search

#### Scenario: Pass 2 results are merged with Pass 1 via RRF
- **WHEN** Pass 2 produces results
- **THEN** all Pass 1 results and Pass 2 results SHALL be merged via `merge_rrf_multi()` and deduplicated by chunk ID

### Requirement: Results include hop provenance
Each result SHALL include a `hop` field: `0` if the result appeared in Pass 1, `1` if it appeared only in Pass 2.

#### Scenario: Hop field values
- **WHEN** a result appears in both Pass 1 and Pass 2
- **THEN** its `hop` field SHALL be `0`

### Requirement: Latency budget
The total wall-clock time for `memory_deep_search` SHALL remain under 2 seconds for typical queries (under 1000 indexed chunks).

#### Scenario: Performance within budget
- **WHEN** the index contains fewer than 1000 chunks
- **THEN** `memory_deep_search` SHALL complete in under 2 seconds
