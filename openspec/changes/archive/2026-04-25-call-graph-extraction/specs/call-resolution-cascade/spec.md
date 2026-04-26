## ADDED Requirements

### Requirement: 6-strategy resolution cascade
The system SHALL resolve call sites to target symbols by applying strategies in order from highest to lowest confidence, returning the first successful match. The strategies and their confidence scores are:

1. Import-map exact match (0.95) -- callee name matches an imported symbol exactly
2. Import-map suffix fallback (0.85) -- callee name matches the last component of an imported symbol
3. Same-module prefix match (0.90) -- target symbol exists in the same package/directory as the caller
4. Unique-name project-wide (0.75) -- exactly one symbol with that name exists across the entire codebase
5. Suffix + import-distance (0.55) -- suffix match weighted by directory proximity to the caller
6. Fuzzy string similarity (0.30-0.40) -- Levenshtein or similar distance as last resort

#### Scenario: Import exact match resolves first
- **WHEN** file A imports `com.example.UserService` and calls `UserService.getUser()`
- **THEN** the cascade SHALL resolve at strategy 1 with confidence 0.95 to the file containing `UserService`

#### Scenario: Same-module takes priority over unique-name
- **WHEN** a function calls `validate()` and a `validate` function exists in the same package AND another `validate` exists elsewhere
- **THEN** the cascade SHALL resolve at strategy 3 (same-module, confidence 0.90) not strategy 4

#### Scenario: Unique-name fallback
- **WHEN** a function calls `calculateTaxRate()` and exactly one function named `calculateTaxRate` exists project-wide, but no import or same-module match
- **THEN** the cascade SHALL resolve at strategy 4 with confidence 0.75

#### Scenario: Fuzzy match as last resort
- **WHEN** no strategies 1-5 produce a match and a symbol `processOrders` exists when the callee is `processOrder`
- **THEN** the cascade SHALL resolve at strategy 6 with confidence between 0.30 and 0.40

#### Scenario: No match produces unresolved edge
- **WHEN** no strategy produces a match for a call site
- **THEN** the system SHALL store the call as `edge_type = 'calls_unresolved'` with `target_file = NULL`

### Requirement: Confidence scoring on resolved edges
Each resolved call edge SHALL carry a `confidence` score (REAL, 0.0-1.0) stored both in the `edges.confidence` column and in the `edges.metadata` JSON. The metadata JSON SHALL also include the `strategy` name that produced the match.

#### Scenario: Confidence stored in both locations
- **WHEN** a call edge is resolved with strategy "import_exact" at confidence 0.95
- **THEN** the `edges` row SHALL have `confidence = 0.95` and `metadata` containing `{"confidence": 0.95, "strategy": "import_exact", ...}`

### Requirement: Resolution cascade input requirements
The `resolve_call_targets(call_sites, symbol_table, import_map)` function SHALL accept:
- `call_sites`: list of dicts from `extract_call_sites()`
- `symbol_table`: dict mapping symbol names to list of `{file_path, kind, start_line, end_line}` across the codebase
- `import_map`: dict mapping `(file_path, imported_name)` to resolved target file paths

#### Scenario: Symbol table with duplicate names
- **WHEN** the symbol table contains two entries for `validate` (in different files)
- **THEN** strategy 4 (unique-name) SHALL NOT match, and the cascade SHALL fall through to strategies 5-6

### Requirement: Resolved call edges stored as edge_type 'calls'
Resolved call edges SHALL be stored in the `edges` table with `edge_type = 'calls'`, `source_file` set to the caller's file path, `target_file` set to the resolved target file path, and metadata containing `callee_name`, `callee_receiver`, `caller_symbol`, `confidence`, and `strategy`.

#### Scenario: Resolved edge storage
- **WHEN** a call from `ServiceA.java:processOrder` to `Repository.java:save` is resolved
- **THEN** an edge SHALL be inserted with `source_file = "path/to/ServiceA.java"`, `target_file = "path/to/Repository.java"`, `edge_type = "calls"`, and metadata with all call details

### Requirement: Unresolved call edges stored as edge_type 'calls_unresolved'
Unresolved call edges SHALL be stored in the `edges` table with `edge_type = 'calls_unresolved'`, `target_file = NULL`, and metadata containing the callee name and receiver for future re-resolution.

#### Scenario: Unresolved edge storage
- **WHEN** a call to `unknownService.doStuff()` cannot be resolved by any strategy
- **THEN** an edge SHALL be inserted with `target_file = NULL`, `edge_type = "calls_unresolved"`, and metadata `{"callee_name": "doStuff", "callee_receiver": "unknownService", ...}`

### Requirement: Schema migration adds confidence column
The system SHALL add a `confidence REAL` column to the `edges` table if it does not already exist. Existing rows SHALL have `confidence = NULL`. The migration SHALL be backward-compatible (no data loss, no breaking changes to existing queries).

#### Scenario: Column added on first run
- **WHEN** `--calls` is run for the first time and the `edges` table lacks a `confidence` column
- **THEN** the system SHALL add the column via `ALTER TABLE edges ADD COLUMN confidence REAL`

#### Scenario: Column already exists
- **WHEN** `--calls` is run and the `confidence` column already exists
- **THEN** the system SHALL proceed without error

### Requirement: Integration with codebase-index.py via --calls flag
The `--calls` flag SHALL trigger call extraction and resolution after the dependency pass. It SHALL:
1. Load or build the per-codebase symbol table from the `symbols` table
2. Load or build the per-codebase import map from existing import edges
3. Extract call sites from each changed file (respecting incremental mode)
4. Run the resolution cascade
5. Store resolved and unresolved edges
6. Delete old call edges for re-indexed files before inserting new ones

#### Scenario: Incremental call extraction
- **WHEN** `--calls --update` is run and only 3 files have changed
- **THEN** the system SHALL only re-extract call sites for those 3 files, leaving call edges from unchanged files intact

#### Scenario: Full call extraction
- **WHEN** `--calls` is run without `--update`
- **THEN** the system SHALL extract call sites from all parseable source files and replace all existing call edges for the codebase
