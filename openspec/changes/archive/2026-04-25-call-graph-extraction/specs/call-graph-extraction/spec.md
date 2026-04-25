## ADDED Requirements

### Requirement: Java call site extraction
The system SHALL extract call sites from Java files by querying tree-sitter for `method_invocation` nodes. Each call site SHALL include the caller symbol name, callee method name, callee receiver expression (if present), source line number, and file path.

#### Scenario: Simple method call
- **WHEN** a Java file contains `repository.findById(id)` inside method `getUser`
- **THEN** the extractor SHALL return a call site with `caller_symbol = "getUser"`, `callee_name = "findById"`, `callee_receiver = "repository"`, and the correct line number

#### Scenario: Static method call
- **WHEN** a Java file contains `Collections.sort(list)` inside method `processData`
- **THEN** the extractor SHALL return a call site with `caller_symbol = "processData"`, `callee_name = "sort"`, `callee_receiver = "Collections"`

#### Scenario: Chained method call
- **WHEN** a Java file contains `list.stream().filter(x -> x > 0).collect(Collectors.toList())`
- **THEN** the extractor SHALL return separate call sites for `stream`, `filter`, and `collect`

#### Scenario: Constructor call
- **WHEN** a Java file contains `new ArrayList<>()`
- **THEN** the extractor SHALL return a call site with `callee_name = "ArrayList"` and `callee_receiver = null`

### Requirement: Kotlin call site extraction
The system SHALL extract call sites from Kotlin files by querying tree-sitter for `call_expression` nodes, handling both `navigation_expression` (receiver.method) and `simple_identifier` (bare function call) forms.

#### Scenario: Navigation expression call
- **WHEN** a Kotlin file contains `service.fetchData()` inside function `loadPage`
- **THEN** the extractor SHALL return a call site with `caller_symbol = "loadPage"`, `callee_name = "fetchData"`, `callee_receiver = "service"`

#### Scenario: Bare function call
- **WHEN** a Kotlin file contains `println("hello")` inside function `greet`
- **THEN** the extractor SHALL return a call site with `caller_symbol = "greet"`, `callee_name = "println"`, `callee_receiver = null`

#### Scenario: Extension function call
- **WHEN** a Kotlin file contains `myList.isEmpty()` inside function `validate`
- **THEN** the extractor SHALL return a call site with `callee_name = "isEmpty"`, `callee_receiver = "myList"`

### Requirement: Python call site extraction
The system SHALL extract call sites from Python files using `ast.Call` nodes, resolving the `func` attribute for `Name` (bare calls), `Attribute` (dotted calls), and nested attribute access.

#### Scenario: Bare function call
- **WHEN** a Python file contains `process_data(items)` inside function `main`
- **THEN** the extractor SHALL return a call site with `caller_symbol = "main"`, `callee_name = "process_data"`, `callee_receiver = null`

#### Scenario: Attribute call
- **WHEN** a Python file contains `self.db.execute(query)` inside method `run_query`
- **THEN** the extractor SHALL return a call site with `caller_symbol = "run_query"`, `callee_name = "execute"`, `callee_receiver = "self.db"`

#### Scenario: Nested class method call
- **WHEN** a Python file contains `MyClass.static_method()` inside function `setup`
- **THEN** the extractor SHALL return a call site with `callee_name = "static_method"`, `callee_receiver = "MyClass"`

### Requirement: Caller symbol identification
The system SHALL determine the enclosing function or method for each call site by finding the innermost symbol (from the file's symbol list) whose line range contains the call's line number. If no enclosing symbol is found, the caller SHALL be set to `<module>`.

#### Scenario: Call inside a method
- **WHEN** a call occurs at line 25 and symbol `processOrder` spans lines 20-35
- **THEN** the call site's `caller_symbol` SHALL be `"processOrder"`

#### Scenario: Call at module level
- **WHEN** a call occurs at line 5 and no symbol's line range contains line 5
- **THEN** the call site's `caller_symbol` SHALL be `"<module>"`

### Requirement: Unified dispatch for call extraction
The system SHALL provide an `extract_call_sites(file_path, source_code, language)` function that dispatches to the correct language-specific extractor based on file extension (`.java` -> Java, `.kt` -> Kotlin, `.py` -> Python). Unsupported extensions SHALL return an empty list.

#### Scenario: Dispatch by extension
- **WHEN** `extract_call_sites` is called with a `.java` file
- **THEN** it SHALL use the Java tree-sitter call extraction logic

#### Scenario: Unsupported extension
- **WHEN** `extract_call_sites` is called with a `.sql` file
- **THEN** it SHALL return an empty list

### Requirement: Graceful parse failure handling
The system SHALL catch parse errors during call extraction and return an empty list, logging a warning to stderr. A single file's parse failure SHALL NOT halt extraction for other files.

#### Scenario: Malformed source file
- **WHEN** a Java file has a syntax error that prevents tree-sitter parsing
- **THEN** the extractor SHALL log a warning and return an empty list for that file
