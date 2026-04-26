## Why

The codebase indexer currently resolves imports only within a single repository. When Toast services depend on each other (e.g., `toast-orders` importing types from `toast-common`), these cross-repo edges are unresolvable — `target_file` stays NULL. This means dependency_search can't answer "which repos depend on toast-common?" or trace a type like `com.toasttab.common.Money` back to its defining file in another indexed repo. Building a cross-repo dependency graph unlocks blast-radius analysis across the entire service mesh.

## What Changes

- **New build file parsers** (`src/build_parser.py`): regex-based extraction of declared dependencies from Gradle (Kotlin DSL + Groovy DSL, including version catalogs and settings files), Maven (`pom.xml`), Python (`pyproject.toml`, `requirements.txt`), and npm (`package.json`).
- **Build dependency edges**: store parsed dependencies in the existing `edges` table with `edge_type = 'build_dependency'` and the dependency coordinate in `metadata`.
- **Cross-repo linking pass**: after multiple repos are indexed, match unresolved `build_dependency` edges against known indexed codebases by artifact ID / repo name.
- **Cross-repo type resolution**: for unresolved `extends`/`implements` edges, search the `symbols` table across all codebases to find the defining file.
- **CLI flags**: `--build-deps` and `--resolve-cross-repo` on `codebase-index.py`.
- **MCP tool enhancement**: `dependency_search` gains `direction='depended_on_by'` and `edge_type` filter, plus cross-codebase result support.

## Capabilities

### New Capabilities
- `build-file-parsing`: Parse Gradle, Maven, pip, and npm build files to extract declared dependencies as structured records.
- `cross-repo-resolution`: After indexing multiple repos, resolve NULL target_file references by matching dependency coordinates and FQN type names against indexed codebases.

### Modified Capabilities
- (none — existing specs cover chunking and file indexing, not dependency graph features)

## Impact

- **New file**: `src/build_parser.py` (build file parsers)
- **Modified file**: `scripts/codebase-index.py` (new `--build-deps` and `--resolve-cross-repo` flags, integration calls)
- **Modified file**: `src/unified_memory_server.py` (`dependency_search` tool gains new direction and edge_type filter)
- **Database**: no schema changes — uses existing `edges`, `symbols`, and `codebase_meta` tables
- **Dependencies**: `tomllib` (stdlib 3.11+), `xml.etree` (stdlib) — no new pip packages
