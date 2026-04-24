"""
AST-based import, symbol, and call site extraction for Java, Kotlin, and Python.

Uses tree-sitter for Java/Kotlin and stdlib ast for Python.
Extracts imports (with type classification), symbol declarations
(classes, interfaces, enums, functions, methods) with line numbers,
and function-level call sites for call graph construction.
"""

import ast as python_ast
import logging
import warnings
from pathlib import Path

logger = logging.getLogger(__name__)

warnings.filterwarnings('ignore', category=FutureWarning, module='tree_sitter')

# Lazy-loaded parsers (one per language)
_parsers: dict = {}


def _get_parser(language: str):
    """Get or create a tree-sitter parser for the given language."""
    if language not in _parsers:
        from tree_sitter_languages import get_parser
        _parsers[language] = get_parser(language)
    return _parsers[language]


def _scoped_identifier_text(node) -> str:
    """Extract dotted name from a scoped_identifier or identifier node."""
    parts = []
    for child in node.children:
        if child.type in ('identifier', 'simple_identifier', 'type_identifier'):
            parts.append(child.text.decode())
        elif child.type == 'scoped_identifier':
            parts.append(_scoped_identifier_text(child))
    return '.'.join(parts)


# ──────────────────────────────────────────────────────────────
# Java imports & symbols
# ──────────────────────────────────────────────────────────────

def extract_java_imports(file_path: str) -> list[dict]:
    """Extract imports from a Java file using tree-sitter."""
    source = Path(file_path).read_bytes()
    parser = _get_parser('java')
    tree = parser.parse(source)

    imports = []
    for node in tree.root_node.children:
        if node.type != 'import_declaration':
            continue

        is_static = any(c.type == 'static' for c in node.children)
        is_wildcard = any(c.type == 'asterisk' for c in node.children)

        # The scoped_identifier contains the import path
        scoped = None
        for child in node.children:
            if child.type == 'scoped_identifier':
                scoped = child
                break

        if scoped is None:
            continue

        import_name = _scoped_identifier_text(scoped)
        if is_wildcard:
            import_name += '.*'

        import_type = 'static_import' if is_static else ('wildcard_import' if is_wildcard else 'import')
        imports.append({
            'import_name': import_name,
            'import_type': import_type,
            'is_static': is_static,
            'is_wildcard': is_wildcard,
        })

    return imports


def extract_java_symbols(file_path: str) -> list[dict]:
    """Extract class/interface/enum/method declarations from a Java file."""
    source = Path(file_path).read_bytes()
    parser = _get_parser('java')
    tree = parser.parse(source)
    symbols = []
    _walk_java_symbols(tree.root_node, symbols)
    return symbols


def _walk_java_symbols(node, symbols: list[dict]):
    """Recursively walk Java AST to extract symbol declarations."""
    type_decl_kinds = {
        'class_declaration': 'class',
        'interface_declaration': 'interface',
        'enum_declaration': 'enum',
    }
    method_kinds = {
        'method_declaration': 'method',
        'constructor_declaration': 'method',
    }

    if node.type in type_decl_kinds:
        # Type declarations: name is identifier or type_identifier
        name_node = None
        for child in node.children:
            if child.type in ('identifier', 'type_identifier'):
                name_node = child
                break
        if name_node:
            symbols.append({
                'name': name_node.text.decode(),
                'kind': type_decl_kinds[node.type],
                'start_line': node.start_point[0] + 1,
                'end_line': node.end_point[0] + 1,
            })

    elif node.type in method_kinds:
        # Methods: name is 'identifier' (not 'type_identifier' which is the return type)
        name_node = None
        for child in node.children:
            if child.type == 'identifier':
                name_node = child
                break
        if name_node:
            symbols.append({
                'name': name_node.text.decode(),
                'kind': method_kinds[node.type],
                'start_line': node.start_point[0] + 1,
                'end_line': node.end_point[0] + 1,
            })

    for child in node.children:
        _walk_java_symbols(child, symbols)


# ──────────────────────────────────────────────────────────────
# Kotlin imports & symbols
# ──────────────────────────────────────────────────────────────

def extract_kotlin_imports(file_path: str) -> list[dict]:
    """Extract imports from a Kotlin file using tree-sitter."""
    source = Path(file_path).read_bytes()
    parser = _get_parser('kotlin')
    tree = parser.parse(source)

    imports = []
    for node in tree.root_node.children:
        if node.type == 'import_list':
            for child in node.children:
                if child.type == 'import_header':
                    imports.append(_parse_kotlin_import(child))
        elif node.type == 'import_header':
            imports.append(_parse_kotlin_import(node))

    return [i for i in imports if i is not None]


def _parse_kotlin_import(node) -> dict | None:
    """Parse a single Kotlin import_header node."""
    ident_node = None
    alias = None
    is_wildcard = False

    for child in node.children:
        if child.type == 'identifier':
            ident_node = child
        elif child.type == 'import_alias':
            for ac in child.children:
                if ac.type == 'type_identifier':
                    alias = ac.text.decode()
        elif child.type == '*':
            is_wildcard = True

    if ident_node is None:
        return None

    import_name = _scoped_identifier_text(ident_node)
    if is_wildcard:
        import_name += '.*'

    return {
        'import_name': import_name,
        'import_type': 'wildcard_import' if is_wildcard else 'import',
        'is_static': False,
        'is_wildcard': is_wildcard,
        'alias': alias,
    }


def extract_kotlin_symbols(file_path: str) -> list[dict]:
    """Extract class/interface/object/enum/function declarations from a Kotlin file."""
    source = Path(file_path).read_bytes()
    parser = _get_parser('kotlin')
    tree = parser.parse(source)
    symbols = []
    _walk_kotlin_symbols(tree.root_node, symbols)
    return symbols


def _walk_kotlin_symbols(node, symbols: list[dict]):
    """Recursively walk Kotlin AST to extract symbol declarations."""
    if node.type == 'class_declaration':
        # Determine kind from keyword: class, interface, object, enum
        kind = 'class'
        for child in node.children:
            if child.type == 'interface':
                kind = 'interface'
            elif child.type == 'object':
                kind = 'object'
            elif child.type == 'enum':
                kind = 'enum'

        name_node = None
        for child in node.children:
            if child.type == 'type_identifier':
                name_node = child
                break
        if name_node:
            symbols.append({
                'name': name_node.text.decode(),
                'kind': kind,
                'start_line': node.start_point[0] + 1,
                'end_line': node.end_point[0] + 1,
            })

    elif node.type == 'object_declaration':
        name_node = None
        for child in node.children:
            if child.type == 'type_identifier':
                name_node = child
                break
        if name_node:
            symbols.append({
                'name': name_node.text.decode(),
                'kind': 'object',
                'start_line': node.start_point[0] + 1,
                'end_line': node.end_point[0] + 1,
            })

    elif node.type == 'function_declaration':
        name_node = None
        for child in node.children:
            if child.type == 'simple_identifier':
                name_node = child
                break
        if name_node:
            symbols.append({
                'name': name_node.text.decode(),
                'kind': 'function',
                'start_line': node.start_point[0] + 1,
                'end_line': node.end_point[0] + 1,
            })

    for child in node.children:
        _walk_kotlin_symbols(child, symbols)


# ──────────────────────────────────────────────────────────────
# Python imports & symbols
# ──────────────────────────────────────────────────────────────

def extract_python_imports(file_path: str) -> list[dict]:
    """Extract imports from a Python file using stdlib ast."""
    source = Path(file_path).read_text(errors='replace')
    try:
        tree = python_ast.parse(source)
    except SyntaxError:
        return []

    imports = []
    for node in python_ast.walk(tree):
        if isinstance(node, python_ast.Import):
            for alias in node.names:
                imports.append({
                    'import_name': alias.name,
                    'import_type': 'import',
                    'is_static': False,
                    'is_wildcard': False,
                })
        elif isinstance(node, python_ast.ImportFrom):
            module = node.module or ''
            # Handle relative imports
            if node.level and node.level > 0:
                module = '.' * node.level + module

            if node.names and len(node.names) == 1 and node.names[0].name == '*':
                imports.append({
                    'import_name': module + '.*',
                    'import_type': 'wildcard_import',
                    'is_static': False,
                    'is_wildcard': True,
                })
            else:
                for alias in node.names:
                    imports.append({
                        'import_name': f'{module}.{alias.name}' if module else alias.name,
                        'import_type': 'import',
                        'is_static': False,
                        'is_wildcard': False,
                    })

    return imports


def extract_python_symbols(file_path: str) -> list[dict]:
    """Extract class/function declarations from a Python file."""
    source = Path(file_path).read_text(errors='replace')
    try:
        tree = python_ast.parse(source)
    except SyntaxError:
        return []

    symbols = []
    for node in python_ast.iter_child_nodes(tree):
        if isinstance(node, python_ast.ClassDef):
            symbols.append({
                'name': node.name,
                'kind': 'class',
                'start_line': node.lineno,
                'end_line': node.end_lineno or node.lineno,
            })
            # Extract methods within classes
            for child in python_ast.iter_child_nodes(node):
                if isinstance(child, (python_ast.FunctionDef, python_ast.AsyncFunctionDef)):
                    symbols.append({
                        'name': f'{node.name}.{child.name}',
                        'kind': 'method',
                        'start_line': child.lineno,
                        'end_line': child.end_lineno or child.lineno,
                    })
        elif isinstance(node, (python_ast.FunctionDef, python_ast.AsyncFunctionDef)):
            symbols.append({
                'name': node.name,
                'kind': 'function',
                'start_line': node.lineno,
                'end_line': node.end_lineno or node.lineno,
            })

    return symbols


# ──────────────────────────────────────────────────────────────
# Unified dispatch
# ──────────────────────────────────────────────────────────────

def extract_imports(file_path: str) -> list[dict]:
    """Extract imports from a file, dispatching by extension."""
    if file_path.endswith('.java'):
        return extract_java_imports(file_path)
    elif file_path.endswith('.kt'):
        return extract_kotlin_imports(file_path)
    elif file_path.endswith('.py'):
        return extract_python_imports(file_path)
    return []


def extract_symbols(file_path: str) -> list[dict]:
    """Extract symbol declarations from a file, dispatching by extension."""
    if file_path.endswith('.java'):
        return extract_java_symbols(file_path)
    elif file_path.endswith('.kt'):
        return extract_kotlin_symbols(file_path)
    elif file_path.endswith('.py'):
        return extract_python_symbols(file_path)
    return []


# ──────────────────────────────────────────────────────────────
# Call site extraction
# ──────────────────────────────────────────────────────────────

def _find_enclosing_symbol(symbols: list[dict], line: int) -> str:
    """Return the innermost symbol whose line range contains the given line.

    Returns '<module>' if no enclosing symbol is found.
    """
    best = None
    best_span = float('inf')
    for sym in symbols:
        if sym['start_line'] <= line <= sym['end_line']:
            span = sym['end_line'] - sym['start_line']
            if span < best_span:
                best = sym['name']
                best_span = span
    return best if best is not None else '<module>'


def extract_java_call_sites(file_path: str, source_code: bytes, symbols: list[dict]) -> list[dict]:
    """Extract call sites from a Java file using tree-sitter.

    Queries for method_invocation and object_creation_expression nodes.
    """
    parser = _get_parser('java')
    tree = parser.parse(source_code)
    calls: list[dict] = []
    _walk_java_calls(tree.root_node, file_path, symbols, calls)
    return calls


def _walk_java_calls(node, file_path: str, symbols: list[dict], calls: list[dict]):
    """Recursively walk Java AST to extract call sites."""
    if node.type == 'method_invocation':
        # Structure: [object.]method(args)
        # Children: [receiver, '.', method_name, argument_list]
        callee_name = None
        callee_receiver = None

        # Find the method name (identifier child) and receiver
        children = list(node.children)
        for i, child in enumerate(children):
            if child.type == 'identifier' and i > 0:
                # This identifier follows something — it's the method name
                callee_name = child.text.decode()
            elif child.type == 'identifier' and i == 0:
                # First identifier could be receiver or bare method name
                # Check if next non-dot child is also an identifier
                callee_name = child.text.decode()
            elif child.type == 'field_access':
                callee_receiver = child.text.decode()

        # If there's a dot, the first part is receiver, second is method
        dot_indices = [i for i, c in enumerate(children) if c.type == '.']
        if dot_indices:
            # Everything before the dot is receiver
            receiver_parts = []
            for c in children[:dot_indices[0]]:
                if c.type not in ('argument_list', '(', ')', '.'):
                    receiver_parts.append(c.text.decode())
            if receiver_parts:
                callee_receiver = '.'.join(receiver_parts)
            # Method name is identifier after last dot
            for c in children[dot_indices[-1] + 1:]:
                if c.type == 'identifier':
                    callee_name = c.text.decode()
                    break

        if callee_name:
            line = node.start_point[0] + 1
            calls.append({
                'file_path': file_path,
                'caller_symbol': _find_enclosing_symbol(symbols, line),
                'callee_name': callee_name,
                'callee_receiver': callee_receiver,
                'line': line,
            })

    elif node.type == 'object_creation_expression':
        # Constructor call: new ClassName(args)
        # Find the type_identifier child
        for child in node.children:
            if child.type == 'type_identifier':
                line = node.start_point[0] + 1
                calls.append({
                    'file_path': file_path,
                    'caller_symbol': _find_enclosing_symbol(symbols, line),
                    'callee_name': child.text.decode(),
                    'callee_receiver': None,
                    'line': line,
                })
                break

    for child in node.children:
        _walk_java_calls(child, file_path, symbols, calls)


def extract_kotlin_call_sites(file_path: str, source_code: bytes, symbols: list[dict]) -> list[dict]:
    """Extract call sites from a Kotlin file using tree-sitter.

    Handles call_expression with navigation_expression (receiver.method)
    and simple_identifier (bare function call) forms.
    """
    parser = _get_parser('kotlin')
    tree = parser.parse(source_code)
    calls: list[dict] = []
    _walk_kotlin_calls(tree.root_node, file_path, symbols, calls)
    return calls


def _walk_kotlin_calls(node, file_path: str, symbols: list[dict], calls: list[dict]):
    """Recursively walk Kotlin AST to extract call sites."""
    if node.type == 'call_expression':
        callee_name = None
        callee_receiver = None

        # First child is what's being called
        if node.children:
            target = node.children[0]
            if target.type == 'navigation_expression':
                # receiver.method form
                parts = []
                for child in target.children:
                    if child.type == 'simple_identifier':
                        parts.append(child.text.decode())
                    elif child.type == 'navigation_suffix':
                        for sc in child.children:
                            if sc.type == 'simple_identifier':
                                parts.append(sc.text.decode())
                if len(parts) >= 2:
                    callee_receiver = '.'.join(parts[:-1])
                    callee_name = parts[-1]
                elif len(parts) == 1:
                    callee_name = parts[0]
            elif target.type == 'simple_identifier':
                callee_name = target.text.decode()

        if callee_name:
            line = node.start_point[0] + 1
            calls.append({
                'file_path': file_path,
                'caller_symbol': _find_enclosing_symbol(symbols, line),
                'callee_name': callee_name,
                'callee_receiver': callee_receiver,
                'line': line,
            })

    for child in node.children:
        _walk_kotlin_calls(child, file_path, symbols, calls)


def _resolve_python_call_func(node) -> tuple[str | None, str | None]:
    """Resolve a Python ast.Call func node into (callee_name, callee_receiver).

    Handles Name (bare), Attribute (dotted), and nested attribute access.
    """
    if isinstance(node, python_ast.Name):
        return node.id, None
    elif isinstance(node, python_ast.Attribute):
        # Recursively resolve the value to build the receiver chain
        parts = []
        current = node
        while isinstance(current, python_ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, python_ast.Name):
            parts.append(current.id)
        parts.reverse()
        if len(parts) >= 2:
            return parts[-1], '.'.join(parts[:-1])
        elif len(parts) == 1:
            return parts[0], None
    return None, None


def extract_python_call_sites(file_path: str, source_code: str, symbols: list[dict]) -> list[dict]:
    """Extract call sites from a Python file using stdlib ast.Call."""
    try:
        tree = python_ast.parse(source_code)
    except SyntaxError:
        return []

    calls: list[dict] = []
    for node in python_ast.walk(tree):
        if isinstance(node, python_ast.Call):
            callee_name, callee_receiver = _resolve_python_call_func(node.func)
            if callee_name:
                line = node.lineno
                calls.append({
                    'file_path': file_path,
                    'caller_symbol': _find_enclosing_symbol(symbols, line),
                    'callee_name': callee_name,
                    'callee_receiver': callee_receiver,
                    'line': line,
                })

    return calls


def extract_call_sites(file_path: str, source_code: str | bytes, language: str, symbols: list[dict]) -> list[dict]:
    """Extract call sites from a file, dispatching by language.

    Args:
        file_path: Path to the source file
        source_code: File contents (str for Python, bytes for Java/Kotlin)
        language: One of 'java', 'kotlin', 'python'
        symbols: Symbol list from extract_symbols() for caller identification

    Returns:
        List of call site dicts with keys: file_path, caller_symbol, callee_name,
        callee_receiver (may be None), line
    """
    try:
        if language == 'java':
            if isinstance(source_code, str):
                source_code = source_code.encode()
            return extract_java_call_sites(file_path, source_code, symbols)
        elif language == 'kotlin':
            if isinstance(source_code, str):
                source_code = source_code.encode()
            return extract_kotlin_call_sites(file_path, source_code, symbols)
        elif language == 'python':
            if isinstance(source_code, bytes):
                source_code = source_code.decode(errors='replace')
            return extract_python_call_sites(file_path, source_code, symbols)
    except Exception as e:
        logger.warning(f'Call extraction failed for {file_path}: {e}')
        return []
    return []
