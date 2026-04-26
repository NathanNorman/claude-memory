## ADDED Requirements

### Requirement: Relative import resolution
The system SHALL resolve relative TypeScript/JavaScript imports (starting with `./` or `../`) by trying file extensions in order: `.ts`, `.tsx`, `.js`, `.jsx`, then index files: `index.ts`, `index.tsx`, `index.js`, `index.jsx`.

#### Scenario: Relative import resolves to .ts file
- **WHEN** resolving `'./utils'` and `utils.ts` exists in the same directory
- **THEN** the resolver returns the path to `utils.ts`

#### Scenario: Relative import resolves to index file
- **WHEN** resolving `'./components'` and `components/index.ts` exists
- **THEN** the resolver returns the path to `components/index.ts`

#### Scenario: Relative import with explicit extension
- **WHEN** resolving `'./styles.css'`
- **THEN** the resolver returns `None` (non-JS/TS extension, not resolvable)

### Requirement: Path alias resolution from tsconfig.json
The system SHALL read `compilerOptions.paths` from `tsconfig.json` at the repo root (if present) and use it to resolve path-aliased imports. The `compilerOptions.baseUrl` SHALL be respected if set.

#### Scenario: tsconfig paths alias
- **WHEN** tsconfig.json has `"paths": {"@/*": ["src/*"]}` and import is `'@/utils/helpers'`
- **THEN** the resolver resolves against `src/utils/helpers` with standard extension probing

#### Scenario: No tsconfig.json present
- **WHEN** no tsconfig.json exists in the repo root
- **THEN** the resolver falls back to the `@/ -> src/` convention for `@/` prefixed imports

### Requirement: Bare specifier classification
Imports that are not relative (no `./` or `../` prefix) and do not match a path alias SHALL be classified as external (returning `None`).

#### Scenario: NPM package import
- **WHEN** resolving `'react'`
- **THEN** the resolver returns `None` (external package)

#### Scenario: Scoped NPM package
- **WHEN** resolving `'@toasttab/buffet-pui-button'`
- **THEN** the resolver returns `None` (external package, `@toasttab` is not a path alias)

### Requirement: Source root discovery for TypeScript
The import resolver SHALL discover TypeScript source roots by looking for: `package.json` in the repo root or subdirectories, `src/` directories, and the repo root itself.

#### Scenario: Standard src layout
- **WHEN** the repo has `src/utils/helpers.ts`
- **THEN** resolving `'./utils/helpers'` from a file in `src/` finds `src/utils/helpers.ts`

### Requirement: Language dispatch for TypeScript
The `resolve_import` function SHALL accept `'typescript'` as a language parameter and route to the TypeScript resolver. The codebase indexer SHALL map `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs` extensions to the `'typescript'` language.

#### Scenario: TypeScript extension mapped to resolver
- **WHEN** `resolve_import('react', repo, 'typescript')` is called
- **THEN** the TypeScript import resolver handles the request

### Requirement: DEP_EXTENSIONS and SOURCE_EXTENSIONS updates
`DEP_EXTENSIONS` in `codebase-index.py` SHALL include `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs`. `SOURCE_EXTENSIONS` SHALL include `.tsx`, `.jsx`, `.mjs`, `.cjs` (`.ts` and `.js` are already present).

#### Scenario: TSX files discovered for indexing
- **WHEN** the codebase indexer scans a repo containing `.tsx` files
- **THEN** those files are included in both chunking and dependency extraction
