## ADDED Requirements

### Requirement: Tree-sitter-based TypeScript chunking
The system SHALL chunk TypeScript and JavaScript files using tree-sitter AST parsing instead of file-level fallback. The chunker SHALL identify the following declaration types as chunk boundaries:
- `class_declaration` and `abstract_class_declaration`
- `function_declaration`
- `arrow_function` or `function` assigned to module-level `const`/`let` (via `lexical_declaration`)
- `interface_declaration`
- `enum_declaration`
- `type_alias_declaration`

Each chunk SHALL include `title`, `content`, `start_line`, and `end_line`.

#### Scenario: File with multiple declarations
- **WHEN** a TypeScript file contains a class, two functions, and an interface
- **THEN** the chunker produces at least 4 chunks, one per declaration

#### Scenario: Class includes its methods
- **WHEN** a TypeScript file contains a class with methods
- **THEN** the class and all its methods are included in a single chunk

#### Scenario: Exported arrow function
- **WHEN** a TypeScript file contains `export const handler = async (req, res) => { ... }`
- **THEN** the chunker produces a chunk with title containing `handler`

### Requirement: Small declaration merging
Adjacent small declarations (type aliases, single-line constants, short interfaces) that are each fewer than 5 lines SHALL be merged into a single chunk to avoid excessive fragmentation.

#### Scenario: Adjacent type aliases
- **WHEN** a TypeScript file has 4 consecutive single-line type aliases
- **THEN** they are merged into one chunk rather than 4 separate chunks

#### Scenario: Large interface not merged
- **WHEN** a TypeScript file has a 20-line interface followed by a type alias
- **THEN** the interface is its own chunk (not merged with the type alias)

### Requirement: File extension routing
The code chunker SHALL route `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs` files to the TypeScript tree-sitter chunker.

#### Scenario: JavaScript file chunked with tree-sitter
- **WHEN** `chunk_file('utils.js')` is called
- **THEN** the TypeScript tree-sitter chunker processes the file

#### Scenario: MJS file chunked with tree-sitter
- **WHEN** `chunk_file('server.mjs')` is called
- **THEN** the TypeScript tree-sitter chunker processes the file

### Requirement: Fallback on parse failure
If tree-sitter fails to parse a TypeScript/JavaScript file, the chunker SHALL fall back to file-level chunking (same as current behavior for unknown extensions).

#### Scenario: Unparseable TypeScript file
- **WHEN** a `.ts` file cannot be parsed by tree-sitter
- **THEN** the file is chunked at file level using `_chunk_file_level`

### Requirement: Empty or small file handling
Files with 10 or fewer lines SHALL be returned as a single file-level chunk.

#### Scenario: Tiny TypeScript file
- **WHEN** a `.ts` file has 8 lines
- **THEN** it is returned as a single chunk covering the entire file
