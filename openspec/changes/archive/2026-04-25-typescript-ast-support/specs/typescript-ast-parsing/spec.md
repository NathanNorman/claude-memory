## ADDED Requirements

### Requirement: TypeScript import extraction
The system SHALL extract imports from TypeScript and JavaScript files using tree-sitter. The following import patterns MUST be recognized:
- ES module named imports: `import { Foo, Bar } from './module'`
- ES module namespace imports: `import * as X from 'package'`
- ES module default imports: `import X from 'package'`
- CommonJS requires: `require('package')` (call_expression with `require` identifier)
- Re-exports: `export { Foo } from './module'`
- Side-effect imports: `import './styles.css'`

Each extracted import SHALL include: `import_string` (the full specifier string), `import_type` (one of: `import`, `namespace_import`, `default_import`, `require`, `reexport`, `side_effect`), and `source_module` (the module path string).

#### Scenario: Named ES module import
- **WHEN** a TypeScript file contains `import { Foo, Bar } from './utils'`
- **THEN** the extractor returns one import with `import_string='Foo, Bar'`, `import_type='import'`, `source_module='./utils'`

#### Scenario: Namespace import
- **WHEN** a TypeScript file contains `import * as React from 'react'`
- **THEN** the extractor returns one import with `import_string='* as React'`, `import_type='namespace_import'`, `source_module='react'`

#### Scenario: Default import
- **WHEN** a TypeScript file contains `import React from 'react'`
- **THEN** the extractor returns one import with `import_string='React'`, `import_type='default_import'`, `source_module='react'`

#### Scenario: CommonJS require
- **WHEN** a JavaScript file contains `const fs = require('fs')`
- **THEN** the extractor returns one import with `import_string='fs'`, `import_type='require'`, `source_module='fs'`

#### Scenario: Re-export
- **WHEN** a TypeScript file contains `export { Foo } from './module'`
- **THEN** the extractor returns one import with `import_string='Foo'`, `import_type='reexport'`, `source_module='./module'`

#### Scenario: Side-effect import
- **WHEN** a TypeScript file contains `import './styles.css'`
- **THEN** the extractor returns one import with `import_string=''`, `import_type='side_effect'`, `source_module='./styles.css'`

### Requirement: TypeScript symbol extraction
The system SHALL extract symbol declarations from TypeScript and JavaScript files using tree-sitter. The following declaration types MUST be recognized:
- Classes: `class_declaration`, `abstract_class_declaration`
- Interfaces: `interface_declaration`
- Functions: `function_declaration`
- Arrow functions assigned to module-level const/let: `lexical_declaration` containing `arrow_function`
- Enums: `enum_declaration`
- Type aliases: `type_alias_declaration`
- Methods within classes/interfaces

Each extracted symbol SHALL include: `name`, `kind` (one of: `class`, `interface`, `function`, `enum`, `type_alias`, `method`), `start_line`, `end_line`, and `exported` (boolean).

#### Scenario: Exported class with methods
- **WHEN** a TypeScript file contains `export class UserService { getName() { ... } }`
- **THEN** the extractor returns a symbol with `name='UserService'`, `kind='class'`, `exported=True` and a symbol with `name='UserService.getName'`, `kind='method'`

#### Scenario: Interface declaration
- **WHEN** a TypeScript file contains `interface Config { timeout: number; }`
- **THEN** the extractor returns a symbol with `name='Config'`, `kind='interface'`, `exported=False`

#### Scenario: Arrow function assigned to const
- **WHEN** a TypeScript file contains `export const fetchData = async () => { ... }`
- **THEN** the extractor returns a symbol with `name='fetchData'`, `kind='function'`, `exported=True`

#### Scenario: Enum declaration
- **WHEN** a TypeScript file contains `enum Status { Active, Inactive }`
- **THEN** the extractor returns a symbol with `name='Status'`, `kind='enum'`

#### Scenario: Type alias
- **WHEN** a TypeScript file contains `export type UserId = string`
- **THEN** the extractor returns a symbol with `name='UserId'`, `kind='type_alias'`, `exported=True`

### Requirement: File extension dispatch
The `extract_imports` and `extract_symbols` dispatch functions SHALL route the following extensions to the TypeScript extractor:
- `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs`

#### Scenario: TypeScript file routed to TS extractor
- **WHEN** `extract_imports('foo.ts')` is called
- **THEN** the TypeScript import extractor is invoked

#### Scenario: JSX file routed to TS extractor
- **WHEN** `extract_symbols('Component.jsx')` is called
- **THEN** the TypeScript symbol extractor is invoked

### Requirement: Grammar selection by extension
The system SHALL use the `typescript` tree-sitter grammar for `.ts`, `.js`, `.mjs`, `.cjs` files and the `tsx` grammar for `.tsx`, `.jsx` files.

#### Scenario: TSX file uses tsx grammar
- **WHEN** parsing a `.tsx` file
- **THEN** the `tsx` tree-sitter grammar is used

#### Scenario: Plain TS file uses typescript grammar
- **WHEN** parsing a `.ts` file
- **THEN** the `typescript` tree-sitter grammar is used

### Requirement: Graceful fallback on parse failure
If tree-sitter fails to parse a TypeScript/JavaScript file, the extractor SHALL return an empty list rather than raising an exception.

#### Scenario: Malformed TypeScript file
- **WHEN** a `.ts` file contains syntax that tree-sitter cannot parse
- **THEN** `extract_imports` returns `[]` and `extract_symbols` returns `[]`
