## 1. Java Hierarchy Extraction

- [ ] 1.1 Add `_extract_java_hierarchy(root_node, file_path)` function to `src/ast_parser.py` that walks the AST for `class_declaration` and `interface_declaration` nodes, extracting superclass/super_interfaces/extends_interfaces relationships
- [ ] 1.2 Handle generic type stripping for Java (extract base type from `generic_type` nodes)

## 2. Kotlin Hierarchy Extraction

- [ ] 2.1 Add `_extract_kotlin_hierarchy(root_node, file_path)` function to `src/ast_parser.py` that walks `class_declaration` and `object_declaration` nodes, inspecting `delegation_specifier` children for `constructor_invocation` (extends), bare `user_type` (implements), and `explicit_delegation` (delegation)
- [ ] 2.2 Handle generic type stripping for Kotlin

## 3. Python Hierarchy Extraction

- [ ] 3.1 Add `_extract_python_hierarchy(tree, file_path)` function to `src/ast_parser.py` using stdlib `ast` to iterate `ClassDef.bases`, handling `ast.Name`, `ast.Attribute`, and `ast.Subscript` nodes

## 4. TypeScript Support

- [ ] 4.1 Add TypeScript grammar loading to `_get_parser()` (language string `'typescript'`)
- [ ] 4.2 Add `_extract_typescript_hierarchy(root_node, file_path)` function to `src/ast_parser.py` that walks `class_declaration` and `interface_declaration` nodes for `extends_clause`, `implements_clause`, and `extends_type_clause`
- [ ] 4.3 Handle generic type stripping for TypeScript

## 5. Unified Dispatch

- [ ] 5.1 Add `extract_hierarchy(file_path, source_code, language)` function to `src/ast_parser.py` that dispatches to language-specific extractors, wrapping each in try/except to return empty list on parse failure

## 6. Indexer Integration

- [ ] 6.1 Add `.ts` to `DEP_EXTENSIONS` in `scripts/codebase-index.py` and add `'ts': 'typescript'` to the language detection dict
- [ ] 6.2 In `index_dependencies()`, after import and symbol extraction, call `extract_hierarchy()` and build an import map from the file's imports for parent name resolution
- [ ] 6.3 Store hierarchy edges in the `edges` table with `edge_type` in (`extends`, `implements`, `delegation`), `metadata` containing the parent name, and `target_file` resolved via the import map (NULL if unresolved)

## 7. Cross-Repo Resolution

- [ ] 7.1 Add `--resolve-hierarchy` CLI flag to `codebase-index.py`
- [ ] 7.2 Implement `resolve_hierarchy_edges()` function that queries unresolved hierarchy edges (target_file IS NULL) and matches metadata against the symbols table across all codebases, updating target_file for matches

## 8. Testing

- [ ] 8.1 Test Java hierarchy extraction with classes, interfaces, generics, and combined extends+implements
- [ ] 8.2 Test Kotlin hierarchy extraction with class, object, delegation, and mixed supertypes
- [ ] 8.3 Test Python hierarchy extraction with simple, dotted, generic, and multiple bases
- [ ] 8.4 Test TypeScript hierarchy extraction with classes, interfaces, and generics
- [ ] 8.5 Test graceful handling of unparseable files (returns empty list, no exception)
