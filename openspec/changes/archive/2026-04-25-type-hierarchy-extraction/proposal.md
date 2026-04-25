## Why

The codebase indexer extracts imports and symbols but has no knowledge of class inheritance or interface implementation. This means queries like "what extends BaseService?" or "which classes implement EventHandler?" cannot be answered. Type hierarchy is fundamental to understanding OOP codebases and resolving cross-file dependencies.

## What Changes

- Add `extract_hierarchy(file_path, source_code, language)` function to `src/ast_parser.py` that extracts extends/implements/delegation relationships for Java, Kotlin, Python, and TypeScript
- Add TypeScript tree-sitter grammar support to `ast_parser.py`
- Integrate hierarchy extraction into `scripts/codebase-index.py` under the existing `--deps` flag
- Store hierarchy edges in the existing `edges` table with new `edge_type` values: `extends`, `implements`, `delegation`
- Add a `--resolve-hierarchy` flag for cross-repo resolution of unresolved parent FQNs against the `symbols` table
- Update `DEP_EXTENSIONS` to include `.ts` files for dependency extraction

## Capabilities

### New Capabilities
- `type-hierarchy-extraction`: Extract class inheritance and interface implementation relationships from Java, Kotlin, Python, and TypeScript source code, storing them as edges in the dependency graph

### Modified Capabilities

## Impact

- `src/ast_parser.py` — new `extract_hierarchy()` function, TypeScript grammar loading, new per-language hierarchy walkers
- `scripts/codebase-index.py` — hierarchy extraction call in `index_dependencies()`, new `--resolve-hierarchy` flag, expanded `DEP_EXTENSIONS`
- `edges` table — new `edge_type` values (`extends`, `implements`, `delegation`); no schema changes needed
- `tree-sitter-languages` package — already a dependency; TypeScript grammar included but not yet used
