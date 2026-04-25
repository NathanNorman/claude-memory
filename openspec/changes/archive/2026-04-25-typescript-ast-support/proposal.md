## Why

TypeScript/JavaScript is Toast's second most common language (24% of repos), but the codebase indexer currently falls back to file-level chunking for TS/JS files. This means no AST-aware symbol extraction, no import resolution, and no structured code chunking -- resulting in lower-quality search results for TypeScript codebases compared to Java/Kotlin/Python.

## What Changes

- Add tree-sitter-based TypeScript/JavaScript AST parsing to `src/ast_parser.py` for import and symbol extraction
- Add tree-sitter-based TypeScript code chunking to `src/code_chunker.py`, replacing file-level fallback for `.ts`/`.tsx`/`.js`/`.jsx`/`.mjs`/`.cjs` files
- Add TypeScript import resolution to `src/import_resolver.py` with support for relative imports, `@/` path aliases, `tsconfig.json` paths, and bare specifier detection
- Add `.tsx`, `.jsx`, `.mjs`, `.cjs` to `SOURCE_EXTENSIONS` in `scripts/codebase-index.py`
- Add TypeScript to the language dispatch map in `scripts/codebase-index.py` for dependency extraction

## Capabilities

### New Capabilities
- `typescript-ast-parsing`: Tree-sitter-based import and symbol extraction for TypeScript/JavaScript files (classes, interfaces, functions, enums, type aliases, arrow functions, ES module imports, CommonJS requires, re-exports)
- `typescript-code-chunking`: AST-aware code chunking for TypeScript/JavaScript files using tree-sitter, replacing file-level fallback with declaration-boundary chunks
- `typescript-import-resolution`: Resolve TypeScript/JavaScript import paths within a repository (relative imports, path aliases from tsconfig.json, bare specifier classification)

### Modified Capabilities


## Impact

- **Files modified**: `src/ast_parser.py`, `src/code_chunker.py`, `src/import_resolver.py`, `scripts/codebase-index.py`
- **Dependencies**: Uses `tree-sitter-languages` package (already installed -- provides `typescript` and `tsx` grammars)
- **Backward compatible**: No changes to existing Java/Kotlin/Python parsing. New TypeScript support is additive.
- **Graceful degradation**: If tree-sitter fails on a file, falls back to existing file-level chunking
