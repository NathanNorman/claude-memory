## 1. Schema Migration

- [x] 1.1 Add `confidence REAL` column to `edges` table in `ensure_dep_tables()` in `codebase-index.py` (use ALTER TABLE with try/except for idempotency)

## 2. Call Site Extraction in ast_parser.py

- [x] 2.1 Add `_find_enclosing_symbol(symbols, line)` helper that returns the innermost symbol containing a given line number, or `<module>` if none
- [x] 2.2 Add `extract_java_call_sites(file_path, source_code, symbols)` using tree-sitter `method_invocation` and `object_creation_expression` queries
- [x] 2.3 Add `extract_kotlin_call_sites(file_path, source_code, symbols)` using tree-sitter `call_expression` with `navigation_expression` and `simple_identifier` handling
- [x] 2.4 Add `extract_python_call_sites(file_path, source_code, symbols)` using `ast.Call` with `Name`, `Attribute`, and nested attribute resolution
- [x] 2.5 Add unified `extract_call_sites(file_path, source_code, language, symbols)` dispatch function with try/except per file

## 3. Resolution Cascade in src/call_resolver.py

- [x] 3.1 Create `src/call_resolver.py` with `resolve_call_targets(call_sites, symbol_table, import_map)` function signature and data structures
- [x] 3.2 Implement strategy 1: import-map exact match (confidence 0.95)
- [x] 3.3 Implement strategy 2: import-map suffix fallback (confidence 0.85)
- [x] 3.4 Implement strategy 3: same-module prefix match (confidence 0.90)
- [x] 3.5 Implement strategy 4: unique-name project-wide (confidence 0.75)
- [x] 3.6 Implement strategy 5: suffix + import-distance weighted by directory proximity (confidence 0.55)
- [x] 3.7 Implement strategy 6: fuzzy string similarity as last resort (confidence 0.30-0.40)
- [x] 3.8 Wire strategies into ordered cascade that short-circuits on first match and returns resolved edges with confidence and strategy metadata

## 4. Integration with codebase-index.py

- [x] 4.1 Add `--calls` CLI flag to argparse in `main()`
- [x] 4.2 Add `build_symbol_table(conn, codebase_name)` helper that loads all symbols from the DB into the dict format the resolver expects
- [x] 4.3 Add `build_import_map(conn, codebase_name)` helper that loads import edges and builds `(file_path, imported_name) -> target_file` mapping
- [x] 4.4 Add `index_call_graph(conn, name, repo_path, incremental)` function that orchestrates: extract call sites per file, run resolution cascade, store edges
- [x] 4.5 Handle incremental mode: delete old call/calls_unresolved edges for changed files, skip unchanged files using codebase_meta hashes
- [x] 4.6 Store resolved edges as `edge_type='calls'` and unresolved as `edge_type='calls_unresolved'` with JSON metadata including callee_name, callee_receiver, caller_symbol, confidence, strategy

## 5. Testing and Validation

- [x] 5.1 Test call extraction on sample Java/Kotlin/Python files to verify correct caller_symbol, callee_name, callee_receiver, and line numbers
- [x] 5.2 Test resolution cascade with a mock symbol table and import map covering all 6 strategies
- [x] 5.3 End-to-end test: run `--calls` on the claude-memory repo itself and verify edges are stored with expected edge_types and confidence scores
