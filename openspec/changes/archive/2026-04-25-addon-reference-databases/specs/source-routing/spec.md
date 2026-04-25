## ADDED Requirements

### Requirement: Source parameter routes to addon databases
When `memory_search` is called with a `source` value that matches a registered addon source name, the query SHALL be executed against that addon's database exclusively. The primary `memory.db` SHALL NOT be queried.

#### Scenario: Search addon by source name
- **WHEN** `memory_search(query="window functions", source="spark-sql")` is called
- **AND** addon source `spark-sql` is registered
- **THEN** FTS5 keyword search runs against `spark-sql.db`
- **AND** vector similarity search runs against `spark-sql.db`
- **AND** results are merged via RRF
- **AND** `memory.db` is not queried

#### Scenario: Search addon by fully-qualified plugin name
- **WHEN** `memory_search(query="materialization", source="toast-analytics-engineering:analytics")` is called
- **AND** addon source `toast-analytics-engineering:analytics` is registered
- **THEN** the query runs against that addon's database exclusively

#### Scenario: Unknown addon source
- **WHEN** `memory_search(query="test", source="nonexistent-addon")` is called
- **AND** no addon named `nonexistent-addon` is registered
- **THEN** the response includes an error: "Unknown source: nonexistent-addon"
- **AND** no search is performed

### Requirement: Empty source queries primary database only
When `memory_search` is called with an empty `source` parameter, the query SHALL run against `memory.db` only. Addon databases SHALL NOT be included.

#### Scenario: Default search excludes addons
- **WHEN** `memory_search(query="what did I debug", source="")` is called
- **AND** 3 addon databases are registered
- **THEN** only `memory.db` is searched
- **AND** results contain no addon content

### Requirement: Existing source filters still work on primary database
The existing `source` values (`curated`, `conversations`, `codebase`) SHALL continue to work as before, filtering results from the primary `memory.db` post-search.

#### Scenario: Source=curated still works
- **WHEN** `memory_search(query="architecture", source="curated")` is called
- **THEN** the primary `memory.db` is searched
- **AND** results are filtered to non-conversation, non-codebase files

#### Scenario: Source=conversations still works
- **WHEN** `memory_search(query="debugging session", source="conversations")` is called
- **THEN** the primary `memory.db` is searched
- **AND** results are filtered to conversation archives

### Requirement: Addon results use consistent format
Search results from addon databases SHALL use the same response format as primary database results, with the addition of a `source` field identifying the addon.

#### Scenario: Addon result format
- **WHEN** a search against addon `spark-sql` returns results
- **THEN** each result includes `path`, `score`, `snippet`, `startLine`, `endLine`, `title`
- **AND** each result includes `source: "spark-sql"`
