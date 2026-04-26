## Context

The codebase indexer (`scripts/codebase-index.py`) already supports AST-based parsing for Java/Kotlin (tree-sitter) and Python (stdlib ast) via `src/ast_parser.py`, `src/code_chunker.py`, and `src/import_resolver.py`. TypeScript/JavaScript files (`.ts`, `.tsx`, `.js`, `.jsx`) are currently indexed with file-level chunking only -- no symbol extraction, no import resolution, no declaration-boundary chunking.

The `tree-sitter-languages` package already provides `typescript` and `tsx` grammars. The existing parser infrastructure uses lazy-loaded tree-sitter parsers keyed by grammar name.

## Goals / Non-Goals

**Goals:**
- Full AST-based import extraction for TypeScript/JavaScript (ES modules, CommonJS, re-exports)
- Full symbol extraction (classes, interfaces, functions, enums, type aliases, arrow functions)
- Declaration-boundary code chunking replacing file-level fallback for TS/JS
- Import resolution with relative paths, tsconfig.json path aliases, and bare specifier detection
- Support for `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs` file extensions

**Non-Goals:**
- Flow type annotation support (Facebook's Flow is not used at Toast)
- Vue/Svelte SFC parsing (single-file components with embedded `<script>` blocks)
- Full TypeScript type-checking or semantic analysis
- Resolving imports through `node_modules` to actual file paths (bare specifiers are classified as external)

## Decisions

### 1. Use `typescript` grammar for `.ts`/`.js`/`.mjs`/`.cjs`, `tsx` grammar for `.tsx`/`.jsx`

**Rationale:** The tree-sitter `typescript` grammar handles plain JavaScript as a strict subset. The `tsx` grammar additionally handles JSX syntax. Using `tsx` for `.tsx`/`.jsx` and `typescript` for the rest avoids parse failures on JSX-containing files while keeping non-JSX parsing strict.

**Alternative considered:** Using `tsx` grammar for all files. Rejected because `tsx` grammar can misparse some valid TypeScript generics syntax (e.g., `f<T>(x)` can be ambiguous with JSX).

### 2. Tree-sitter-based chunking (not regex)

**Rationale:** Java and Kotlin chunking currently use regex patterns. For TypeScript, tree-sitter is preferred because: (a) TypeScript has more syntactic variation (arrow functions, const exports, decorators, generics), (b) the tree-sitter grammar is already loaded for import/symbol extraction so there is no extra cost, (c) tree-sitter gives accurate line ranges without brace-counting.

**Alternative considered:** Regex-based chunking (matching existing Java/Kotlin pattern). Rejected because TypeScript's arrow function syntax (`export const Foo = () => { ... }`) is hard to capture boundaries for via regex.

### 3. Export detection via parent `export_statement` node

**Rationale:** In tree-sitter's TypeScript grammar, exported declarations are wrapped in an `export_statement` node. Checking `node.parent.type == 'export_statement'` reliably determines export status for all declaration types.

### 4. Optional tsconfig.json for import resolution

**Rationale:** Many TS projects use path aliases configured in `tsconfig.json` (e.g., `@/` mapping to `src/`). Reading this is valuable but must be optional -- not all repos have it, and it can extend other configs. We read `compilerOptions.paths` from the repo root's `tsconfig.json` if present, falling back to the `@/ -> src/` convention.

### 5. Merge small adjacent declarations into single chunks

**Rationale:** TypeScript files often have sequences of small type aliases, constants, and interface declarations at the top of a file. Each being its own chunk would create many tiny, low-value chunks. Adjacent small items (under a configurable line threshold) are merged into a single chunk.

## Risks / Trade-offs

- **[Grammar availability]** If `tree-sitter-languages` does not include `typescript`/`tsx` grammars in the installed version, parsing will fail. Mitigation: graceful fallback to file-level chunking (same as current behavior).
- **[JSX ambiguity]** The `tsx` grammar may misparse some edge-case TypeScript generics. Mitigation: use `tsx` only for `.tsx`/`.jsx` files; use `typescript` grammar for `.ts`/`.js`.
- **[tsconfig extends]** We do not resolve `extends` chains in tsconfig.json. Mitigation: only read the root `tsconfig.json`; missing paths fall back to `@/ -> src/` convention.
- **[Performance]** Tree-sitter parsing adds overhead vs. regex for chunking. Mitigation: the parser is lazy-loaded and cached per grammar, same as Java/Kotlin. Parsing is fast (sub-millisecond for typical files).
