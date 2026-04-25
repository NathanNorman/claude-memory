## Context

The codebase indexer (`scripts/codebase-index.py`) already extracts import edges and symbol declarations via `src/ast_parser.py` and `src/import_resolver.py`. Import edges tell us file-level dependencies; symbols tell us what each file defines. The missing layer is function-level call relationships: which function calls which other function, and in which file does the target live.

The existing infrastructure provides a strong foundation:
- `extract_symbols()` yields `{name, kind, start_line, end_line}` per file
- `extract_imports()` yields `{import_name, import_type, is_static, is_wildcard}` per file
- `resolve_import()` maps import strings to file paths within the repo
- The `edges` table stores directional relationships between files with metadata
- The `symbols` table stores per-file symbol declarations with line ranges
- Tree-sitter parsers are already loaded for Java and Kotlin; Python uses stdlib `ast`

Call resolution is fundamentally harder than import resolution because call sites reference short names (e.g., `service.getData()`) that must be mapped back to fully-qualified symbols. The 6-strategy cascade is adapted from Codebase-Memory (arXiv 2603.27277), which reports ~80% resolution on well-structured codebases.

## Goals / Non-Goals

**Goals:**
- Extract function-level call sites from Java, Kotlin, and Python ASTs
- Resolve call targets to specific files/symbols using a multi-strategy cascade with confidence scores
- Store call edges in the existing `edges` table, reusing the same schema pattern as import edges
- Integrate into the existing `codebase-index.py` pipeline with a `--calls` flag
- Support incremental indexing (only re-extract calls for files whose content hash changed)

**Non-Goals:**
- Type inference or full semantic analysis (no build/compile step)
- Resolving calls to external libraries outside the indexed codebase
- Control flow analysis (if/else branches, loop iterations)
- Call graph visualization (downstream concern)
- Dynamic dispatch resolution (virtual method tables, reflection)

## Decisions

### 1. Call extraction lives in `ast_parser.py` alongside existing extractors

**Rationale:** Follows the established pattern where `extract_imports()` and `extract_symbols()` live together. The new `extract_call_sites()` uses the same tree-sitter/ast infrastructure and lazy-loaded parsers.

**Alternative considered:** Separate `call_extractor.py` module. Rejected because it would duplicate parser setup and the functions are small enough to colocate.

### 2. Resolution cascade lives in a new `src/call_resolver.py` module

**Rationale:** The 6-strategy cascade is complex enough to warrant its own module (~200 lines). It depends on both the symbol table and import map, making it a cross-cutting concern distinct from import resolution. Keeping `import_resolver.py` focused on file-path resolution avoids bloating it.

**Alternative considered:** Extending `import_resolver.py`. Rejected because call resolution operates on different inputs (call sites + symbol table vs. import strings + file system).

### 3. Caller symbol identification uses enclosing scope lookup

To determine which function a call site belongs to (the "caller"), we find the innermost symbol whose line range contains the call's line number. This reuses the already-extracted symbols list for the file.

**Alternative considered:** Tracking scope during AST walk. Rejected as more complex and duplicates the symbol extraction pass.

### 4. Confidence scores stored in edge metadata JSON

The `edges.metadata` column already stores a TEXT field. For call edges, metadata will be a JSON string: `{"callee_name": "foo", "callee_receiver": "bar", "confidence": 0.85, "strategy": "import_exact"}`. This avoids schema changes beyond the optional `confidence` column.

The dedicated `confidence` column is added for efficient filtering/sorting without JSON parsing.

### 5. Unresolved calls stored with `edge_type = 'calls_unresolved'`

**Rationale:** Storing unresolved calls preserves the full call graph even when resolution fails. Downstream consumers can filter by edge_type to include or exclude them. The callee info in metadata enables future re-resolution when more context is available.

### 6. Integration via `--calls` flag, runs after `--deps`

The `--calls` flag triggers call extraction after the dependency pass completes (since it needs the symbol table and import map). If `--deps` wasn't run in the same invocation, `--calls` loads existing symbols/edges from the database.

## Risks / Trade-offs

**[Risk] Resolution accuracy varies by codebase structure** -- Well-structured codebases with clear package hierarchies will see ~80% resolution. Flat Python projects with many short function names may see lower accuracy. Mitigation: confidence scores let consumers filter low-quality edges.

**[Risk] Performance on large codebases** -- The resolution cascade runs per-call-site with up to 6 strategies. For a codebase with 100K call sites, strategies 5-6 (fuzzy matching) could be slow. Mitigation: strategies are ordered by confidence and short-circuit on first match; fuzzy matching uses a precomputed name index.

**[Risk] Tree-sitter grammar differences across versions** -- Node types like `method_invocation` or `call_expression` could change. Mitigation: wrap extraction in try/except per file; log warnings on parse failures.

**[Trade-off] No `confidence` column migration for existing rows** -- Existing import edges will have `confidence = NULL`. This is acceptable since confidence is only meaningful for call edges.
