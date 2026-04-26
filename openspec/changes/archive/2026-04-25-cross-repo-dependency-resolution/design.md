## Context

The codebase indexer (`scripts/codebase-index.py`) indexes source files from individual repos into the `chunks` table for semantic search, and extracts import edges + symbols via `--deps`. The `edges` table stores `(codebase, source_file, target_file, edge_type, metadata)` where `target_file` is NULL when an import can't be resolved within the same repo. The `symbols` table stores class/interface/function declarations with `(codebase, file_path, name, kind)`.

Currently there's no mechanism to:
1. Parse build files to know what external libraries/repos a project depends on
2. Link unresolved edges across repos that are both indexed
3. Answer "which repos depend on X?" at the build-dependency level

## Goals / Non-Goals

**Goals:**
- Parse Gradle (Kotlin DSL + Groovy), Maven, pip, and npm build files to extract declared dependencies
- Store build-level dependencies as edges in the existing `edges` table
- Resolve cross-repo dependencies by matching coordinates against indexed codebases
- Resolve cross-repo type references (extends/implements) by searching symbols across codebases
- Expose cross-repo queries through the existing `dependency_search` MCP tool

**Non-Goals:**
- Executing build tools (Gradle, Maven, pip) to get resolved dependency trees
- Transitive dependency resolution (only direct declared dependencies)
- Handling dynamic Gradle DSL (custom plugins, conditional dependencies, buildSrc logic)
- Version conflict resolution or compatibility checking
- Indexing build files for semantic search (only parsing for dependency edges)

## Decisions

### 1. Regex-based parsing over build tool execution

Parse build files with regex and stdlib XML/TOML/JSON parsers. This avoids requiring Gradle/Maven/pip to be installed, avoids network calls, and keeps indexing fast. Trade-off: complex Gradle DSL (custom functions, conditional blocks, buildSrc) won't parse. This is acceptable — the 80% case of `implementation("group:artifact:version")` covers most Toast repos.

**Alternative**: Shell out to `gradle dependencies --configuration compileClasspath`. Rejected — requires Gradle wrapper, network access, and takes 30+ seconds per project.

### 2. Store build deps in existing edges table (not a new table)

Use `edge_type = 'build_dependency'` in the existing `edges` table. The `metadata` column stores the full dependency coordinate (e.g., `com.toasttab:toast-common:1.2.3`). The `source_file` is the build file path (e.g., `build.gradle.kts`). The `target_file` starts as NULL and gets populated by the cross-repo resolution pass.

**Alternative**: New `build_dependencies` table with richer columns (group, artifact, version, scope). Rejected — adds schema complexity and the edges table already has the right shape. Structured fields can be parsed from the metadata string when needed.

### 3. Cross-repo resolution as a separate pass

Resolution runs after all repos are indexed (`--resolve-cross-repo` flag). It scans all `build_dependency` edges with `target_file = NULL`, parses the coordinate from metadata, and searches `codebase_meta` for a matching codebase. This is idempotent — safe to re-run as new repos are indexed.

**Matching strategy**: Extract artifact ID from the coordinate (e.g., `toast-common` from `com.toasttab:toast-common:1.2.3`), then check if any indexed codebase name matches. For internal project deps (`implementation(project(":module-name"))`), the target is within the same codebase.

### 4. Cross-repo type resolution via symbols table

For `extends`/`implements` edges with `target_file = NULL`, extract the simple class name from the FQN in metadata (e.g., `Money` from `com.toasttab.common.Money`), search the `symbols` table across all codebases for a matching class/interface. If exactly one match, update `target_file`.

**Ambiguity handling**: If multiple symbols match the same name across codebases, prefer the one whose codebase is a declared build dependency. If still ambiguous, leave NULL.

### 5. dependency_search gains direction and edge_type filtering

Add `direction='depended_on_by'` — queries edges where `metadata LIKE '%<search_term>%'` to find which build files declare a dependency on a given artifact. Add `edge_type` parameter to filter (e.g., only `build_dependency` edges). Results can span codebases when `codebase` parameter is empty.

## Risks / Trade-offs

**[Regex misses complex Gradle DSL]** Complex build scripts with variables, custom functions, or conditional blocks won't parse correctly.
  -> Mitigation: Log unparsed lines at debug level. Users can always add missing deps manually. This is best-effort by design.

**[Artifact-to-codebase matching is heuristic]** The artifact ID `toast-common` might not exactly match the indexed codebase name.
  -> Mitigation: Try multiple matching strategies: exact codebase name match, then search `codebase_meta` for repos containing a build file that declares that artifact as its own group:artifact.

**[Cross-repo resolution requires all repos indexed first]** Resolution only works for repos that are already in the index.
  -> Mitigation: The resolution pass is idempotent — re-run after indexing new repos. Document the workflow: index all repos, then resolve.

**[Version catalog indirection]** Gradle version catalogs (`libs.versions.toml`) use aliases like `libs.toastCommon` that map to coordinates. Parsing requires resolving the alias chain.
  -> Mitigation: Parse the TOML file to build an alias-to-coordinate map, then use it when encountering `implementation(libs.xyz)` patterns.
