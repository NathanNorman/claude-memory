## ADDED Requirements

### Requirement: Java hierarchy extraction
The system SHALL extract extends and implements relationships from Java source files using tree-sitter. For `class_declaration` nodes, it SHALL extract the `superclass` child as an `extends` edge and each type in `super_interfaces` → `type_list` as an `implements` edge. For `interface_declaration` nodes, it SHALL extract each type in `extends_interfaces` → `type_list` as an `extends` edge. Generic type parameters SHALL be stripped (e.g., `Bar<Baz>` yields parent name `Bar`).

#### Scenario: Java class extending another class
- **WHEN** a Java file contains `class Foo extends Bar`
- **THEN** the system extracts one edge with `class_name=Foo`, `parent_name=Bar`, `relationship_type=extends`

#### Scenario: Java class implementing interfaces
- **WHEN** a Java file contains `class Foo implements Baz, Qux`
- **THEN** the system extracts two edges with `relationship_type=implements`, one for `Baz` and one for `Qux`

#### Scenario: Java class with generic superclass
- **WHEN** a Java file contains `class Foo extends Bar<String>`
- **THEN** the system extracts one `extends` edge with `parent_name=Bar` (type parameter stripped)

#### Scenario: Java interface extending interfaces
- **WHEN** a Java file contains `interface Foo extends Bar, Baz`
- **THEN** the system extracts two `extends` edges, one for `Bar` and one for `Baz`

### Requirement: Kotlin hierarchy extraction
The system SHALL extract extends, implements, and delegation relationships from Kotlin source files using tree-sitter. For `class_declaration` and `object_declaration` nodes, it SHALL inspect `delegation_specifier` children: a child with `constructor_invocation` is an `extends` edge, a child with bare `user_type` is an `implements` edge, and a child with `explicit_delegation` is a `delegation` edge. Only one superclass is allowed in Kotlin; the rest are interfaces.

#### Scenario: Kotlin class extending a class and implementing an interface
- **WHEN** a Kotlin file contains `class Foo : Bar(), Baz`
- **THEN** the system extracts one `extends` edge for `Bar` (has constructor invocation) and one `implements` edge for `Baz` (bare user_type)

#### Scenario: Kotlin class with delegation
- **WHEN** a Kotlin file contains `class Foo : Bar by delegate`
- **THEN** the system extracts one `delegation` edge with `parent_name=Bar`

#### Scenario: Kotlin object declaration with supertype
- **WHEN** a Kotlin file contains `object Singleton : Base()`
- **THEN** the system extracts one `extends` edge with `class_name=Singleton`, `parent_name=Base`

### Requirement: Python hierarchy extraction
The system SHALL extract extends relationships from Python source files using stdlib `ast`. For each `ast.ClassDef`, it SHALL iterate `bases` and extract parent names. `ast.Name` nodes yield simple names (e.g., `Bar`). `ast.Attribute` nodes yield dotted names (e.g., `module.Bar`). `ast.Subscript` nodes SHALL be unwrapped to the base type.

#### Scenario: Python class with single base
- **WHEN** a Python file contains `class Foo(Bar):`
- **THEN** the system extracts one `extends` edge with `parent_name=Bar`

#### Scenario: Python class with dotted base
- **WHEN** a Python file contains `class Foo(module.Bar):`
- **THEN** the system extracts one `extends` edge with `parent_name=module.Bar`

#### Scenario: Python class with generic base
- **WHEN** a Python file contains `class Foo(List[str]):`
- **THEN** the system extracts one `extends` edge with `parent_name=List` (subscript stripped)

#### Scenario: Python class with multiple bases
- **WHEN** a Python file contains `class Foo(Bar, Baz):`
- **THEN** the system extracts two `extends` edges, one for each base

### Requirement: TypeScript hierarchy extraction
The system SHALL extract extends and implements relationships from TypeScript source files using tree-sitter with the `typescript` grammar. For `class_declaration` nodes, it SHALL extract `extends_clause` children as `extends` edges and `implements_clause` children as `implements` edges. For `interface_declaration` nodes, it SHALL extract `extends_type_clause` children as `extends` edges. Generic type parameters SHALL be stripped.

#### Scenario: TypeScript class extending and implementing
- **WHEN** a TypeScript file contains `class Foo extends Bar implements Baz`
- **THEN** the system extracts one `extends` edge for `Bar` and one `implements` edge for `Baz`

#### Scenario: TypeScript interface extending another interface
- **WHEN** a TypeScript file contains `interface Foo extends Bar`
- **THEN** the system extracts one `extends` edge with `parent_name=Bar`

#### Scenario: TypeScript class with generic superclass
- **WHEN** a TypeScript file contains `class Foo extends Bar<string>`
- **THEN** the system extracts one `extends` edge with `parent_name=Bar` (type parameter stripped)

### Requirement: Unified dispatch for hierarchy extraction
The system SHALL provide an `extract_hierarchy(file_path, source_code, language)` function that dispatches to the correct language-specific extractor. It SHALL return a list of dicts with keys: `class_name`, `parent_name`, `relationship_type`, `parent_fqn_hint`, `file_path`, `line`. Files that fail to parse SHALL return an empty list without raising exceptions.

#### Scenario: Dispatch by file extension
- **WHEN** `extract_hierarchy` is called with a `.java` file
- **THEN** it delegates to Java hierarchy extraction

#### Scenario: Unsupported file type
- **WHEN** `extract_hierarchy` is called with a `.scala` file
- **THEN** it returns an empty list

#### Scenario: Unparseable file
- **WHEN** `extract_hierarchy` is called with a syntactically invalid file
- **THEN** it returns an empty list without raising an exception

### Requirement: Integration with codebase indexer
The `index_dependencies()` function in `codebase-index.py` SHALL call `extract_hierarchy` after import and symbol extraction when the `--deps` flag is used. Hierarchy edges SHALL be stored in the `edges` table with `edge_type` in (`extends`, `implements`, `delegation`). The `metadata` field SHALL contain the parent name. The system SHALL attempt to resolve parent names to file paths using the file's import map; unresolved parents SHALL have `target_file = NULL`.

#### Scenario: Hierarchy edges stored during dependency indexing
- **WHEN** `--deps` flag is used and a Java file contains `class Foo extends Bar`
- **THEN** an edge is inserted with `edge_type=extends`, `metadata=Bar`, and `target_file` resolved if `Bar` is imported

#### Scenario: Unresolved parent stored with NULL target
- **WHEN** a class extends a type not found in imports or the repo
- **THEN** the edge is stored with `target_file = NULL` and the parent name in `metadata`

### Requirement: TypeScript dependency extraction support
The `DEP_EXTENSIONS` set SHALL include `.ts` so that TypeScript files are included in dependency extraction.

#### Scenario: TypeScript files included in deps scan
- **WHEN** `--deps` flag is used on a repo containing `.ts` files
- **THEN** TypeScript files are processed for hierarchy extraction

### Requirement: Cross-repo hierarchy resolution
A `--resolve-hierarchy` flag SHALL trigger a second pass that resolves unresolved hierarchy edges by matching parent names in `metadata` against the `symbols` table across all codebases. Resolved edges SHALL have their `target_file` updated.

#### Scenario: Cross-repo parent resolution
- **WHEN** `--resolve-hierarchy` is run and an unresolved `extends` edge has `metadata=BaseService`
- **THEN** the system looks up `BaseService` in the `symbols` table across all codebases and updates `target_file` if a match is found

#### Scenario: No match found during resolution
- **WHEN** `--resolve-hierarchy` is run and no symbol matches the parent name
- **THEN** the edge `target_file` remains NULL
