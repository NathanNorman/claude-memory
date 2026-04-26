## Context

claude-memory indexes intra-repo edges (imports, calls, extends) from SCIP data via `codebase-index.py`. These edges live in the `edges` table and are loaded into `GraphSidecar` (igraph) for fast traversal. The `dependency_search` MCP tool already supports `depended_on_by` direction for build_dependency edges.

Inter-repo dependencies are declared in build files (Gradle, Maven, npm, pip) but never extracted. A new script parses these files and writes `repo_dependency` edges into the same `edges` table, making them queryable through existing infrastructure.

## Goals / Non-Goals

**Goals:**
- Parse Gradle, Maven, npm, and pip build files to extract declared dependencies
- Store inter-repo dependency edges in the existing `edges` table
- Enable "what depends on X repo" and "what does X repo depend on" queries
- Work with locally cloned repos (no network calls required)

**Non-Goals:**
- Full AST parsing of build files (regex is sufficient for declared dependencies)
- Resolving transitive dependencies (only direct declarations)
- Auto-cloning repos from GitHub (user provides local paths)
- Version resolution or conflict detection
- Matching dependency identifiers to repo names (that's a future mapping layer)

## Decisions

### 1. Regex-based parsing over AST parsing
Build file formats (Gradle Groovy/Kotlin, Maven XML, JSON, TOML/text) vary widely. Regex extraction of dependency declarations covers 90%+ of patterns with zero external parser dependencies. Full AST parsing (e.g., Gradle tooling API) would add complexity and JVM dependencies for marginal accuracy gains.

### 2. Reuse existing `edges` table with `edge_type='repo_dependency'`
No schema changes needed. `source_file` holds the repo name, `target_file` holds the dependency identifier (e.g., `com.toasttab:toast-common` for Maven/Gradle, `@toast/utils` for npm). `metadata` stores JSON with build file path, scope (implementation/test/dev), and raw version string.

### 3. Standalone script (`scripts/cross-repo-deps.py`) over integration into `codebase-index.py`
Keeps codebase indexing (SCIP-based, per-file) separate from repo dependency extraction (build-file-based, per-repo). Different cadence -- codebase indexing runs on every change, repo deps change rarely. Can be composed: run codebase-index first, then cross-repo-deps.

### 4. `codebase` column stores repo name for scoping
Each repo's dependency edges use `codebase=<repo_name>`, consistent with how codebase-index.py scopes edges. GraphSidecar can load all repo_dependency edges across codebases for cross-repo traversal.

## Risks / Trade-offs

- [Regex misses dynamic dependencies] -> Acceptable: dynamic/computed dependencies (e.g., Gradle `dependencies { project(":${name}") }`) are rare. Document known gaps.
- [Dependency identifiers don't match repo names] -> Store raw identifiers (group:artifact for Maven/Gradle, package name for npm). Future work can add an identifier-to-repo mapping table.
- [Stale edges after build file changes] -> Script supports `--update` flag with content-hash checking, same pattern as codebase-index.py. Include in reindex hook.
- [Large monorepos with many subprojects] -> Gradle multi-project builds: parse root `settings.gradle` for subproject list, then each subproject's `build.gradle`. Cap at MAX_EDGES same as GraphSidecar.

## Open Questions

- Should `cross-repo-deps.py` auto-discover repos under a parent directory (e.g., `~/repos/`), or require explicit `--path` per repo?
- Should we add an `artifact_to_repo` mapping table now or defer to a future change?
