## ADDED Requirements

### Requirement: High-value node identification
The system SHALL identify high-value nodes for semantic labeling based on: symbols with >N incoming edges (configurable, default 5), symbols with public API annotations (`@RestController`, `@GetMapping`, `@PostMapping`, `export`), and module entry points (files named `main`, `index`, `app`).

#### Scenario: Identify candidates
- **WHEN** `codebase-index.py` is run with `--label` flag
- **THEN** the system queries the symbols and edges tables to identify high-value nodes and reports the candidate count before labeling

### Requirement: LLM batch labeling via GPT-4o-mini
The system SHALL send function signature + docstring (max 500 tokens) to GPT-4o-mini for each candidate node and receive a 1-sentence semantic label (max 100 characters).

#### Scenario: Successful labeling
- **WHEN** a candidate node is sent to GPT-4o-mini
- **THEN** the response is parsed and stored in the symbols table's `metadata` column as JSON with a `label` key

#### Scenario: API failure
- **WHEN** the GPT-4o-mini API returns an error or times out
- **THEN** the node is skipped, a warning is logged, and labeling continues with the next candidate

#### Scenario: Rate limiting
- **WHEN** batch labeling processes >100 nodes
- **THEN** requests are sent with a configurable delay (default 100ms) between calls to respect API rate limits

### Requirement: Label caching and deduplication
Labels SHALL be cached permanently in the symbols metadata. A node SHALL NOT be re-labeled unless its content hash has changed since the last labeling.

#### Scenario: Already-labeled node unchanged
- **WHEN** `--label` is run and a candidate node already has a label and its content hash matches
- **THEN** the node is skipped (no API call)

#### Scenario: Node content changed
- **WHEN** a labeled node's content hash differs from the stored hash
- **THEN** the node is re-labeled with a fresh API call

### Requirement: Labels surfaced in search results
The `symbol_search` and `codebase_search` MCP tools SHALL include the semantic label in results when available.

#### Scenario: Symbol search with labels
- **WHEN** `symbol_search` returns a symbol that has a label
- **THEN** the result includes a `label` field with the semantic description
