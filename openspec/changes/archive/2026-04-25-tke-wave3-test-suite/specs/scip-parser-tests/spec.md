## ADDED Requirements

### Requirement: SCIP language detection tests
The test suite SHALL verify detect_scip_languages() identifies correct indexers from build files.

#### Scenario: Java project detected from build.gradle.kts
- **GIVEN** a directory containing build.gradle.kts
- **WHEN** detect_scip_languages() is called
- **THEN** returns ['java']

#### Scenario: TypeScript project detected from tsconfig.json
- **GIVEN** a directory containing tsconfig.json
- **WHEN** detect_scip_languages() is called
- **THEN** returns ['typescript']

#### Scenario: Multi-language repo detects both
- **GIVEN** a directory containing both build.gradle.kts and tsconfig.json
- **WHEN** detect_scip_languages() is called
- **THEN** returns ['java', 'typescript']

#### Scenario: No build files returns empty
- **GIVEN** a directory with no recognized build files
- **WHEN** detect_scip_languages() is called
- **THEN** returns []

### Requirement: SCIP JSON parsing tests
The test suite SHALL verify _parse_scip_json() correctly extracts edges from SCIP JSON output.

#### Scenario: Definition and reference produce edge
- **GIVEN** SCIP JSON with symbol X defined in file A and referenced in file B
- **WHEN** _parse_scip_json() is called
- **THEN** returns edge {source_file: "B", target_file: "A", confidence: 0.95}

#### Scenario: Same-file reference produces no edge
- **GIVEN** SCIP JSON with symbol X both defined and referenced in file A
- **WHEN** _parse_scip_json() is called
- **THEN** no edge is produced (source == target)

#### Scenario: Method call classified as 'calls' edge type
- **GIVEN** a symbol with '()' in its descriptor
- **WHEN** _classify_scip_symbol() is called
- **THEN** returns 'calls'

### Requirement: SCIP edge merging tests
The test suite SHALL verify merge_scip_edges() correctly replaces tree-sitter edges.

#### Scenario: SCIP edge replaces tree-sitter for same pair
- **GIVEN** existing edge (A→B, confidence=0.7) and SCIP edge (A→B, confidence=0.95)
- **WHEN** merge_scip_edges() is called
- **THEN** result has one edge (A→B) with confidence=0.95

#### Scenario: SCIP-only edge added
- **GIVEN** existing edge (A→B) and SCIP edge (A→C) with no existing A→C
- **WHEN** merge_scip_edges() is called
- **THEN** result has both (A→B) and (A→C)

#### Scenario: Existing edges preserved when no SCIP match
- **GIVEN** existing edge (X→Y) with no SCIP edges touching X or Y
- **WHEN** merge_scip_edges() is called
- **THEN** (X→Y) is preserved unchanged
