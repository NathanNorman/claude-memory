## ADDED Requirements

### Requirement: High-value node identification tests
The test suite SHALL verify identify_high_value_nodes() finds the correct candidates.

#### Scenario: Nodes with many incoming edges selected
- **GIVEN** symbol A with 10 incoming edges and symbol B with 2 incoming edges, min_incoming_edges=5
- **WHEN** identify_high_value_nodes() is called
- **THEN** A is in candidates, B is not

#### Scenario: Entry point files selected
- **GIVEN** symbols in files main.py, index.ts, and utils.py
- **WHEN** identify_high_value_nodes() is called
- **THEN** symbols from main.py and index.ts are in candidates (entry points)

#### Scenario: No duplicates when both criteria match
- **GIVEN** symbol in main.py that also has 10 incoming edges
- **WHEN** identify_high_value_nodes() is called
- **THEN** the symbol appears exactly once in candidates

### Requirement: Label caching tests
The test suite SHALL verify that unchanged nodes are not re-labeled.

#### Scenario: Already-labeled node with same hash skipped
- **GIVEN** symbol with metadata containing label="foo" and content_hash="abc", and codebase_meta has content_hash="abc"
- **WHEN** label_nodes_batch() is called
- **THEN** the symbol is skipped (no API call), skipped count increments

#### Scenario: Changed node re-labeled
- **GIVEN** symbol with metadata containing content_hash="abc", but codebase_meta now has content_hash="xyz"
- **WHEN** label_nodes_batch() is called
- **THEN** the symbol is re-labeled (API call made)

#### Scenario: Unlabeled node gets labeled
- **GIVEN** symbol with no metadata
- **WHEN** label_nodes_batch() is called
- **THEN** the symbol gets a label stored in metadata JSON

### Requirement: Label surfacing in search results tests
The test suite SHALL verify labels appear in symbol_search and codebase_search results.

#### Scenario: symbol_search includes label field
- **GIVEN** a symbol with metadata={"label": "Handles user authentication"}
- **WHEN** symbol_search returns this symbol
- **THEN** the result dict contains 'label': 'Handles user authentication'

#### Scenario: symbol_search omits label when absent
- **GIVEN** a symbol with no metadata
- **WHEN** symbol_search returns this symbol
- **THEN** the result dict does not contain a 'label' key
