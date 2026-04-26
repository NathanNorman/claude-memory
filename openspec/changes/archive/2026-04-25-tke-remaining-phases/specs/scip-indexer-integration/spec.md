## ADDED Requirements

### Requirement: SCIP indexer execution
The system SHALL support running SCIP indexers as an optional second pass during codebase indexing. Supported indexers: scip-java (Java/Kotlin/Scala), scip-typescript (TypeScript/JavaScript), scip-python (Python).

#### Scenario: SCIP indexing with --scip flag
- **WHEN** `codebase-index.py` is run with `--scip` flag on a repo with a working build
- **THEN** the appropriate SCIP indexer is executed, producing a `.scip` protobuf file

#### Scenario: SCIP indexer not installed
- **WHEN** `--scip` flag is used but the required indexer binary is not found
- **THEN** the system logs a warning with installation instructions and continues with tree-sitter results only

#### Scenario: Build failure during SCIP indexing
- **WHEN** the SCIP indexer fails (e.g., Gradle build error)
- **THEN** the system logs the error, falls back to tree-sitter results, and does not halt the indexing pipeline

### Requirement: SCIP protobuf parsing
The system SHALL parse SCIP `.scip` protobuf output to extract call edges, symbol definitions, and cross-reference information. Edges from SCIP SHALL be stored with a higher confidence score than tree-sitter edges.

#### Scenario: SCIP edge merging
- **WHEN** both tree-sitter and SCIP produce an edge for the same call site
- **THEN** the SCIP edge takes precedence (higher confidence), and the tree-sitter edge is replaced

#### Scenario: SCIP-only edges
- **WHEN** SCIP identifies call edges that tree-sitter missed (e.g., dynamic dispatch, overloaded methods)
- **THEN** the additional edges are inserted into the edges table with `metadata` noting `source=scip`

### Requirement: SCIP language detection
The system SHALL auto-detect which SCIP indexer to use based on build file presence: `build.gradle.kts` or `pom.xml` → scip-java, `package.json` with TypeScript → scip-typescript, `pyproject.toml` or `setup.py` → scip-python.

#### Scenario: Multi-language repo
- **WHEN** a repo contains both `build.gradle.kts` and `package.json`
- **THEN** both scip-java and scip-typescript are run in sequence
