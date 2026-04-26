## ADDED Requirements

### Requirement: Cross-repo build dependency resolution
The system SHALL resolve `build_dependency` edges with `target_file = NULL` by matching the dependency coordinate's artifact ID against indexed codebase names in `codebase_meta`. When a match is found, `target_file` SHALL be updated to `codebase:<matched-name>/` (the codebase root).

#### Scenario: Matching artifact to indexed codebase
- **WHEN** an edge has `metadata = "com.toasttab:toast-common:1.2.3"` and `target_file = NULL`, and a codebase named `toast-common` exists in `codebase_meta`
- **THEN** the system SHALL update `target_file` to `codebase:toast-common/`

#### Scenario: No matching codebase
- **WHEN** an edge has `metadata = "org.apache:commons-lang3:3.12"` and no indexed codebase matches `commons-lang3`
- **THEN** the system SHALL leave `target_file` as NULL

#### Scenario: Idempotent re-resolution
- **WHEN** the resolution pass runs multiple times on the same database
- **THEN** the results SHALL be identical — already-resolved edges are not duplicated or corrupted

### Requirement: Cross-repo type resolution
The system SHALL resolve `extends` and `implements` edges with `target_file = NULL` by searching the `symbols` table across all codebases for a symbol matching the FQN or simple name in the edge's `metadata`.

#### Scenario: Unique type match across codebases
- **WHEN** an edge has `edge_type = "extends"`, `metadata = "com.toasttab.common.Money"`, `target_file = NULL`, and the `symbols` table contains exactly one class named `Money` in codebase `toast-common` at file `src/main/java/com/toasttab/common/Money.java`
- **THEN** the system SHALL update `target_file` to `src/main/java/com/toasttab/common/Money.java` and set `codebase` context to indicate the cross-repo reference

#### Scenario: Ambiguous type match with build dependency hint
- **WHEN** multiple codebases define a symbol named `Money`, but the source file's codebase has a `build_dependency` edge pointing to `toast-common`
- **THEN** the system SHALL prefer the `Money` symbol from the `toast-common` codebase

#### Scenario: Ambiguous type match without hint
- **WHEN** multiple codebases define a symbol named `Money` and no build dependency disambiguates
- **THEN** the system SHALL leave `target_file` as NULL

### Requirement: CLI --resolve-cross-repo flag
The system SHALL add a `--resolve-cross-repo` flag to `codebase-index.py` that runs the cross-repo resolution pass across all indexed codebases. This flag SHALL NOT require `--path` or `--name`.

#### Scenario: Run cross-repo resolution
- **WHEN** the user runs `codebase-index.py --resolve-cross-repo`
- **THEN** the system SHALL scan all unresolved `build_dependency`, `extends`, and `implements` edges and attempt resolution

#### Scenario: Resolution after new repo indexed
- **WHEN** a new repo is indexed and then `--resolve-cross-repo` is run
- **THEN** previously unresolvable edges that now match the new repo SHALL be resolved

### Requirement: dependency_search supports depended_on_by direction
The `dependency_search` MCP tool SHALL support `direction='depended_on_by'` which finds all codebases/files that declare a build dependency matching a given search term.

#### Scenario: Find repos depending on a library
- **WHEN** `dependency_search(file_path="toast-common", direction="depended_on_by")` is called
- **THEN** the system SHALL return all edges where `edge_type = 'build_dependency'` and `metadata` contains `toast-common`

#### Scenario: Empty results for unknown library
- **WHEN** `dependency_search(file_path="nonexistent-lib", direction="depended_on_by")` is called
- **THEN** the system SHALL return an empty results list with count 0

### Requirement: dependency_search supports edge_type filter
The `dependency_search` MCP tool SHALL accept an optional `edge_type` parameter to filter results to a specific edge type (e.g., `build_dependency`, `import`, `extends`, `implements`).

#### Scenario: Filter to build dependencies only
- **WHEN** `dependency_search(file_path="build.gradle.kts", direction="imports", edge_type="build_dependency")` is called
- **THEN** the system SHALL return only edges with `edge_type = 'build_dependency'`, excluding import edges

#### Scenario: No edge_type filter returns all types
- **WHEN** `dependency_search(file_path="Foo.java", direction="imports")` is called without `edge_type`
- **THEN** the system SHALL return edges of all types (import, extends, implements, build_dependency)

### Requirement: Cross-codebase dependency_search results
When `codebase` parameter is empty, `dependency_search` SHALL return results spanning all indexed codebases, including resolved cross-repo edges.

#### Scenario: Cross-codebase reverse dependency lookup
- **WHEN** `dependency_search(file_path="src/main/java/com/toasttab/common/Money.java", direction="imported_by")` is called with no codebase filter
- **THEN** the system SHALL return files from any indexed codebase that import or extend `Money`
