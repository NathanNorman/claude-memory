## 1. TypeScript AST Parsing (ast_parser.py)

- [ ] 1.1 Add grammar selection helper: `_ts_grammar_for_ext(file_path)` returning `'typescript'` or `'tsx'` based on extension
- [ ] 1.2 Implement `extract_typescript_imports(file_path)` -- parse with tree-sitter, walk `import_statement`, `export_statement` (re-exports), and `call_expression` (require) nodes to extract import_string, import_type, source_module
- [ ] 1.3 Implement `extract_typescript_symbols(file_path)` and `_walk_typescript_symbols(node, symbols, exported)` -- extract classes, interfaces, functions, enums, type aliases, arrow functions at module level, and methods within classes
- [ ] 1.4 Update `extract_imports()` dispatch to route `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs` to TypeScript extractor
- [ ] 1.5 Update `extract_symbols()` dispatch to route same extensions to TypeScript extractor

## 2. TypeScript Code Chunking (code_chunker.py)

- [ ] 2.1 Implement `chunk_typescript_file(path)` -- use tree-sitter to find declaration nodes, convert to chunks with title/content/start_line/end_line
- [ ] 2.2 Add small declaration merging logic -- merge adjacent declarations under 5 lines into a single chunk
- [ ] 2.3 Add `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs` to `_EXT_CHUNKERS` dispatch map pointing to `chunk_typescript_file`
- [ ] 2.4 Handle fallback: return `_chunk_file_level` on tree-sitter parse failure or when no declarations found
- [ ] 2.5 Handle small files (10 or fewer lines) as single file-level chunk

## 3. TypeScript Import Resolution (import_resolver.py)

- [ ] 3.1 Add TypeScript source root discovery in `_find_source_roots()` -- look for `package.json`, `src/` dirs
- [ ] 3.2 Implement `_read_tsconfig_paths(repo_path)` -- read `tsconfig.json` compilerOptions.paths and baseUrl, cached with lru_cache
- [ ] 3.3 Implement `resolve_typescript_import(import_name, repo_path, source_file)` -- handle relative imports with extension probing (.ts, .tsx, .js, .jsx, index files), path alias resolution, bare specifier classification
- [ ] 3.4 Update `resolve_import()` dispatch to handle `language='typescript'`

## 4. Codebase Indexer Updates (codebase-index.py)

- [ ] 4.1 Add `.tsx`, `.jsx`, `.mjs`, `.cjs` to `SOURCE_EXTENSIONS`
- [ ] 4.2 Add `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs` to `DEP_EXTENSIONS`
- [ ] 4.3 Update language dispatch map to route TS/JS extensions to `'typescript'` language

## 5. Testing

- [ ] 5.1 Create test TypeScript files (class, interface, function, arrow fn, enum, type alias, various import styles)
- [ ] 5.2 Test import extraction covers all 6 import types (named, namespace, default, require, reexport, side-effect)
- [ ] 5.3 Test symbol extraction covers all declaration types with correct exported flag
- [ ] 5.4 Test chunking produces declaration-boundary chunks and merges small adjacent items
- [ ] 5.5 Test import resolution: relative imports, tsconfig paths, bare specifier returns None
- [ ] 5.6 Test graceful fallback on unparseable files
