## Why

The codebase indexer currently extracts import edges and symbol declarations but has no visibility into function-level call relationships. This means `dependency_search` can answer "which files import which" but not "which function calls which function." Call graph edges are essential for impact analysis (what breaks if I change this function?) and for understanding code flow across module boundaries.

## What Changes

- Add call site extraction from source ASTs for Java (tree-sitter `method_invocation`), Kotlin (tree-sitter `call_expression`), and Python (`ast.Call`)
- Implement a 6-strategy resolution cascade that maps extracted call sites to their target symbols using import maps, same-module heuristics, unique-name matching, directory proximity, and fuzzy string similarity
- Add a `--calls` flag to `codebase-index.py` that runs call extraction and resolution after the existing chunking/embedding pass
- Store resolved call edges in the existing `edges` table with `edge_type = 'calls'` and unresolved calls as `edge_type = 'calls_unresolved'`
- Add a `confidence` column to the `edges` table for resolution quality scoring

## Capabilities

### New Capabilities
- `call-graph-extraction`: Extract function-level call sites from Java, Kotlin, and Python source using tree-sitter and stdlib ast
- `call-resolution-cascade`: Resolve extracted call sites to target symbols using a 6-strategy cascade with confidence scoring

### Modified Capabilities

## Impact

- `src/ast_parser.py` -- new `extract_call_sites()` function and per-language call extraction helpers
- `src/import_resolver.py` -- extended with call resolution cascade logic (or new module)
- `scripts/codebase-index.py` -- new `--calls` flag, call extraction pass integrated into indexing pipeline
- `edges` table schema -- new `confidence REAL` column (nullable, backward compatible)
- No new dependencies -- uses existing tree-sitter and Python ast infrastructure
