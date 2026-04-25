## 1. Build File Parsers

- [ ] 1.1 Create `src/build_parser.py` with Gradle Kotlin DSL parser: regex for `implementation("group:artifact:version")`, `api(`, `compileOnly(`, `runtimeOnly(`, `testImplementation(`, `testRuntimeOnly(`, `kapt(` — returns list of `{group, artifact, version, scope, is_internal, module_path}`
- [ ] 1.2 Add Gradle Groovy DSL parser: string notation (`implementation 'g:a:v'`) and map notation (`group:, name:, version:`)
- [ ] 1.3 Add internal project dependency parsing: `implementation(project(":module-name"))` for both Kotlin DSL and Groovy DSL
- [ ] 1.4 Add Gradle version catalog parser: parse `gradle/libs.versions.toml` with `tomllib`, build alias-to-coordinate map, resolve `libs.<alias>` references in build files
- [ ] 1.5 Add Gradle settings file parser: extract `include()` declarations from `settings.gradle.kts` and `settings.gradle`
- [ ] 1.6 Add Maven POM parser: `xml.etree` to extract `<dependencies>`, handle `${property}` interpolation, parse `<parent>` and `<dependencyManagement>`
- [ ] 1.7 Add Python dependency parser: `tomllib` for `pyproject.toml` `[project].dependencies`, line-by-line for `requirements.txt` with `-r` include support
- [ ] 1.8 Add npm dependency parser: JSON parse `package.json` for `dependencies`, `devDependencies`, `peerDependencies`
- [ ] 1.9 Add top-level `parse_build_files(repo_path)` function that discovers all build files in a repo and dispatches to the correct parser, returning a unified list of dependency records

## 2. Build Dependency Edge Storage

- [ ] 2.1 Add `index_build_dependencies()` function in `codebase-index.py` that calls `parse_build_files()` and stores results as edges with `edge_type = 'build_dependency'`
- [ ] 2.2 Store external deps with `target_file = NULL` and `metadata = "group:artifact:version"`
- [ ] 2.3 Store internal project deps with `target_file = "<module-path>"` and `metadata = "project:<module-path>"`
- [ ] 2.4 Support incremental mode: skip build files whose content hash hasn't changed, delete old edges before re-inserting

## 3. Cross-Repo Resolution

- [ ] 3.1 Add `resolve_cross_repo_deps(conn)` function in `src/build_parser.py`: scan `build_dependency` edges with `target_file = NULL`, parse artifact from metadata, match against `codebase_meta` codebase names, update `target_file` to `codebase:<name>/` on match
- [ ] 3.2 Add `resolve_cross_repo_types(conn)` function: scan `extends`/`implements` edges with `target_file = NULL`, extract class name from metadata FQN, search `symbols` table across all codebases, prefer match from a declared build dependency codebase
- [ ] 3.3 Ensure both resolution functions are idempotent (safe to re-run without duplicating or corrupting data)

## 4. CLI Integration

- [ ] 4.1 Add `--build-deps` flag to `codebase-index.py` that triggers `index_build_dependencies()` after file indexing
- [ ] 4.2 Add `--resolve-cross-repo` flag that runs both `resolve_cross_repo_deps()` and `resolve_cross_repo_types()` — does not require `--path` or `--name`
- [ ] 4.3 Wire up `--build-deps` to respect `--update` for incremental mode

## 5. MCP Tool Enhancement

- [ ] 5.1 Add `direction='depended_on_by'` to `dependency_search`: query edges where `metadata LIKE '%<search_term>%'` and `edge_type = 'build_dependency'`
- [ ] 5.2 Add optional `edge_type` parameter to `dependency_search` for filtering results to a specific edge type
- [ ] 5.3 Ensure cross-codebase results are returned when `codebase` parameter is empty

## 6. Testing

- [ ] 6.1 Write unit tests for each build file parser with sample build file content (Gradle KTS, Gradle Groovy, Maven, pip, npm)
- [ ] 6.2 Write integration test for `resolve_cross_repo_deps` with two mock codebases in a temp SQLite DB
- [ ] 6.3 Write integration test for `resolve_cross_repo_types` with cross-codebase symbol matching
- [ ] 6.4 Test `dependency_search` MCP tool with the new `depended_on_by` direction and `edge_type` filter
