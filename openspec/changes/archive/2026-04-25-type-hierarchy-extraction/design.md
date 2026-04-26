## Context

The codebase indexer (`scripts/codebase-index.py`) already extracts imports as edges and symbol declarations via `src/ast_parser.py`. The `edges` table stores import relationships with `edge_type` values like `import`, `wildcard_import`, and `static_import`. The `symbols` table stores class/interface/function declarations with name, kind, file path, and line numbers.

Class inheritance and interface implementation are a natural extension of this dependency graph. The tree-sitter grammars for Java and Kotlin are already loaded; Python uses stdlib `ast`. TypeScript grammar is available in the `tree-sitter-languages` package but not yet initialized.

## Goals / Non-Goals

**Goals:**
- Extract extends/implements/delegation relationships from Java, Kotlin, Python, and TypeScript
- Store hierarchy edges in the existing `edges` table with no schema changes
- Resolve parent names to file paths using the import map where possible
- Support cross-repo resolution of unresolved parent FQNs via a second pass

**Non-Goals:**
- Generic type parameter extraction (extract `Bar` from `Foo extends Bar<Baz>`, ignore `Baz`)
- Mixin or trait resolution beyond what the language grammar exposes
- Full type inference or generic type resolution
- Indexing `.tsx` files (only `.ts` for now)

## Decisions

### 1. Single `extract_hierarchy()` entry point dispatching by language

Same pattern as existing `extract_imports()` and `extract_symbols()`. Dispatch by file extension. Returns a uniform list of dicts with `class_name`, `parent_name`, `relationship_type`, `parent_fqn_hint`, `file_path`, and `line`.

**Alternative**: Separate functions per language called directly from codebase-index.py. Rejected because the unified dispatch pattern is already established and keeps the indexer simple.

### 2. Reuse existing `edges` table with new edge_type values

New values: `extends`, `implements`, `delegation`. The `metadata` field stores the parent class/interface name (matching how import names are stored there today). The `target_file` is resolved via the import map when possible, NULL otherwise.

**Alternative**: A dedicated `hierarchy` table. Rejected because edges already model file-to-file relationships and adding new edge types is backward compatible.

### 3. Parent name resolution via import map

After extracting hierarchy, look up each parent name in the file's imports to determine the FQN, then resolve that FQN to a file path using `resolve_import()`. For Python, check both imports and same-file class definitions. Unresolved parents get `target_file = NULL` with the parent name in `metadata`.

### 4. TypeScript support via tree-sitter-languages

Add `'typescript'` to the parser map in `_get_parser()`. The grammar is already bundled in the `tree-sitter-languages` package. Extend `DEP_EXTENSIONS` to include `.ts`. TypeScript import extraction is not included in this change (only hierarchy); import edges for `.ts` files will be added separately.

### 5. Cross-repo resolution as a separate `--resolve-hierarchy` flag

A SQL JOIN matching `edges.metadata` (parent FQN) against `symbols.name` across all codebases. This runs after all repos are indexed and updates `target_file` for previously-NULL hierarchy edges. Deferred to a second pass because it requires all codebases to be indexed first.

### 6. Generic type handling: strip type parameters

For nodes like `Bar<Baz, Qux>`, extract only the base type name `Bar`. In tree-sitter, this means taking the first `type_identifier` or `identifier` child of a `generic_type` or `type_arguments` parent. In Python `ast`, `ast.Subscript` nodes are unwrapped to get the base `Name` or `Attribute`.

## Risks / Trade-offs

- **[Kotlin delegation ambiguity]** Distinguishing superclass from interface in Kotlin relies on `constructor_invocation` vs bare `user_type` heuristic. A class delegating to an interface via `by` is detectable from `explicit_delegation` nodes. Edge case: abstract class with no-arg constructor looks like an interface. Mitigation: acceptable false classification; the edge still captures the relationship.

- **[TypeScript grammar compatibility]** The `tree-sitter-languages` package bundles a specific TypeScript grammar version. Node type names may differ from the latest tree-sitter-typescript. Mitigation: test against actual grammar output and adjust node types as needed.

- **[Unresolved parents]** Many parent names won't resolve to files (stdlib classes, third-party deps). These get `target_file = NULL`. Mitigation: this is expected and consistent with how unresolved imports are handled today.

- **[Performance]** Hierarchy extraction adds a third pass over each file's AST. Mitigation: the AST is already parsed for symbols; hierarchy extraction reuses the same parse tree by accepting source code as a parameter rather than re-reading the file.
