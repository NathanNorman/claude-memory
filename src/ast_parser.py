"""
AST-based import and symbol extraction for Java, Kotlin, and Python.

Uses tree-sitter for Java/Kotlin and stdlib ast for Python.
Extracts imports (with type classification) and symbol declarations
(classes, interfaces, enums, functions, methods) with line numbers.
"""

import ast as python_ast
import warnings
from pathlib import Path

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
