"""
AST-based import, symbol, call site, and hierarchy extraction for Java, Kotlin, Python, and TypeScript.

Uses tree-sitter for Java/Kotlin/TypeScript and stdlib ast for Python.
Extracts imports (with type classification), symbol declarations
(classes, interfaces, enums, functions, methods) with line numbers,
function-level call sites for call graph construction,
and type hierarchy relationships (extends, implements, delegation).
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
# Type hierarchy extraction helpers
# ──────────────────────────────────────────────────────────────

def _strip_generic_type(node) -> str:
    """Extract base type name from a type node, stripping generic parameters.

    For `Bar<Baz>` (generic_type), returns 'Bar'.
    For plain `type_identifier` or `identifier`, returns the text directly.
    """
    if node.type == 'generic_type':
        # First child is the base type identifier
        for child in node.children:
            if child.type in ('type_identifier', 'identifier'):
                return child.text.decode()
        return node.text.decode()
    elif node.type in ('type_identifier', 'identifier'):
        return node.text.decode()
    # Fallback: scoped type like com.example.Foo
    elif node.type == 'scoped_type_identifier':
        return _scoped_identifier_text(node)
    return node.text.decode()


# ──────────────────────────────────────────────────────────────
# Java hierarchy extraction
# ──────────────────────────────────────────────────────────────

def _extract_java_hierarchy(root_node, file_path: str) -> list[dict]:
    """Walk Java AST to extract extends/implements relationships."""
    results: list[dict] = []
    _walk_java_hierarchy(root_node, file_path, results)
    return results


def _walk_java_hierarchy(node, file_path: str, results: list[dict]):
    """Recursively walk Java AST for class/interface hierarchy."""
    if node.type == 'class_declaration':
        # Find class name
        class_name = None
        for child in node.children:
            if child.type in ('identifier', 'type_identifier'):
                class_name = child.text.decode()
                break
        if not class_name:
            for child in node.children:
                _walk_java_hierarchy(child, file_path, results)
            return

        # Extract superclass (extends)
        for child in node.children:
            if child.type == 'superclass':
                for sc_child in child.children:
                    if sc_child.type in ('type_identifier', 'generic_type', 'scoped_type_identifier'):
                        parent = _strip_generic_type(sc_child)
                        results.append({
                            'class_name': class_name,
                            'parent_name': parent,
                            'relationship_type': 'extends',
                            'parent_fqn_hint': None,
                            'file_path': file_path,
                            'line': node.start_point[0] + 1,
                        })

        # Extract interfaces (implements)
        for child in node.children:
            if child.type == 'super_interfaces':
                for si_child in child.children:
                    if si_child.type == 'type_list':
                        for type_node in si_child.children:
                            if type_node.type in ('type_identifier', 'generic_type', 'scoped_type_identifier'):
                                parent = _strip_generic_type(type_node)
                                results.append({
                                    'class_name': class_name,
                                    'parent_name': parent,
                                    'relationship_type': 'implements',
                                    'parent_fqn_hint': None,
                                    'file_path': file_path,
                                    'line': node.start_point[0] + 1,
                                })

    elif node.type == 'interface_declaration':
        # Find interface name
        iface_name = None
        for child in node.children:
            if child.type in ('identifier', 'type_identifier'):
                iface_name = child.text.decode()
                break
        if not iface_name:
            for child in node.children:
                _walk_java_hierarchy(child, file_path, results)
            return

        # Extract extended interfaces
        for child in node.children:
            if child.type == 'extends_interfaces':
                for ei_child in child.children:
                    if ei_child.type == 'type_list':
                        for type_node in ei_child.children:
                            if type_node.type in ('type_identifier', 'generic_type', 'scoped_type_identifier'):
                                parent = _strip_generic_type(type_node)
                                results.append({
                                    'class_name': iface_name,
                                    'parent_name': parent,
                                    'relationship_type': 'extends',
                                    'parent_fqn_hint': None,
                                    'file_path': file_path,
                                    'line': node.start_point[0] + 1,
                                })

    for child in node.children:
        _walk_java_hierarchy(child, file_path, results)


# ──────────────────────────────────────────────────────────────
# Kotlin hierarchy extraction
# ──────────────────────────────────────────────────────────────

def _extract_kotlin_hierarchy(root_node, file_path: str) -> list[dict]:
    """Walk Kotlin AST to extract extends/implements/delegation relationships."""
    results: list[dict] = []
    _walk_kotlin_hierarchy(root_node, file_path, results)
    return results


def _walk_kotlin_hierarchy(node, file_path: str, results: list[dict]):
    """Recursively walk Kotlin AST for class/object hierarchy."""
    if node.type in ('class_declaration', 'object_declaration'):
        # Find class/object name
        class_name = None
        for child in node.children:
            if child.type == 'type_identifier':
                class_name = child.text.decode()
                break
        if not class_name:
            for child in node.children:
                _walk_kotlin_hierarchy(child, file_path, results)
            return

        # Inspect delegation_specifier children
        for child in node.children:
            if child.type != 'delegation_specifier':
                continue

            # Check what kind of delegation this is
            for spec_child in child.children:
                if spec_child.type == 'constructor_invocation':
                    # extends (superclass with constructor call)
                    for ci_child in spec_child.children:
                        if ci_child.type == 'user_type':
                            parent = _extract_kotlin_user_type(ci_child)
                            results.append({
                                'class_name': class_name,
                                'parent_name': parent,
                                'relationship_type': 'extends',
                                'parent_fqn_hint': None,
                                'file_path': file_path,
                                'line': node.start_point[0] + 1,
                            })
                            break

                elif spec_child.type == 'explicit_delegation':
                    # delegation (by keyword)
                    for ed_child in spec_child.children:
                        if ed_child.type == 'user_type':
                            parent = _extract_kotlin_user_type(ed_child)
                            results.append({
                                'class_name': class_name,
                                'parent_name': parent,
                                'relationship_type': 'delegation',
                                'parent_fqn_hint': None,
                                'file_path': file_path,
                                'line': node.start_point[0] + 1,
                            })
                            break

                elif spec_child.type == 'user_type':
                    # implements (bare user_type = interface)
                    parent = _extract_kotlin_user_type(spec_child)
                    results.append({
                        'class_name': class_name,
                        'parent_name': parent,
                        'relationship_type': 'implements',
                        'parent_fqn_hint': None,
                        'file_path': file_path,
                        'line': node.start_point[0] + 1,
                    })

    for child in node.children:
        _walk_kotlin_hierarchy(child, file_path, results)


def _extract_kotlin_user_type(user_type_node) -> str:
    """Extract the type name from a Kotlin user_type node, stripping generics."""
    for child in user_type_node.children:
        if child.type == 'type_identifier':
            return child.text.decode()
    return user_type_node.text.decode()


# ──────────────────────────────────────────────────────────────
# Python hierarchy extraction
# ──────────────────────────────────────────────────────────────

def _extract_python_hierarchy(tree, file_path: str) -> list[dict]:
    """Extract extends relationships from Python AST using stdlib ast."""
    results: list[dict] = []
    for node in python_ast.walk(tree):
        if not isinstance(node, python_ast.ClassDef):
            continue
        for base in node.bases:
            parent = _python_base_name(base)
            if parent:
                results.append({
                    'class_name': node.name,
                    'parent_name': parent,
                    'relationship_type': 'extends',
                    'parent_fqn_hint': None,
                    'file_path': file_path,
                    'line': node.lineno,
                })
    return results


def _python_base_name(node) -> str | None:
    """Extract parent class name from a Python base node.

    Handles ast.Name ('Bar'), ast.Attribute ('module.Bar'),
    and ast.Subscript ('List[str]' -> 'List').
    """
    if isinstance(node, python_ast.Name):
        return node.id
    elif isinstance(node, python_ast.Attribute):
        # Dotted name like module.Bar
        parts = []
        current = node
        while isinstance(current, python_ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, python_ast.Name):
            parts.append(current.id)
        return '.'.join(reversed(parts))
    elif isinstance(node, python_ast.Subscript):
        # Generic like List[str] — unwrap to get base type
        return _python_base_name(node.value)
    return None


# ──────────────────────────────────────────────────────────────
# TypeScript hierarchy extraction
# ──────────────────────────────────────────────────────────────

def _extract_typescript_hierarchy(root_node, file_path: str) -> list[dict]:
    """Walk TypeScript AST to extract extends/implements relationships."""
    results: list[dict] = []
    _walk_typescript_hierarchy(root_node, file_path, results)
    return results


def _walk_typescript_hierarchy(node, file_path: str, results: list[dict]):
    """Recursively walk TypeScript AST for class/interface hierarchy."""
    if node.type == 'class_declaration':
        # Find class name
        class_name = None
        for child in node.children:
            if child.type == 'type_identifier':
                class_name = child.text.decode()
                break
        if not class_name:
            for child in node.children:
                _walk_typescript_hierarchy(child, file_path, results)
            return

        # Process class_heritage children
        for child in node.children:
            if child.type == 'class_heritage':
                for heritage_child in child.children:
                    if heritage_child.type == 'extends_clause':
                        _extract_ts_clause_types(heritage_child, class_name, 'extends', file_path, node, results)
                    elif heritage_child.type == 'implements_clause':
                        _extract_ts_clause_types(heritage_child, class_name, 'implements', file_path, node, results)

    elif node.type == 'interface_declaration':
        # Find interface name
        iface_name = None
        for child in node.children:
            if child.type == 'type_identifier':
                iface_name = child.text.decode()
                break
        if not iface_name:
            for child in node.children:
                _walk_typescript_hierarchy(child, file_path, results)
            return

        # Process extends_type_clause
        for child in node.children:
            if child.type == 'extends_type_clause':
                _extract_ts_clause_types(child, iface_name, 'extends', file_path, node, results)

    for child in node.children:
        _walk_typescript_hierarchy(child, file_path, results)


def _extract_ts_clause_types(
    clause_node, class_name: str, rel_type: str,
    file_path: str, decl_node, results: list[dict],
):
    """Extract type names from a TypeScript extends/implements/extends_type clause."""
    for child in clause_node.children:
        if child.type in ('type_identifier', 'identifier'):
            results.append({
                'class_name': class_name,
                'parent_name': child.text.decode(),
                'relationship_type': rel_type,
                'parent_fqn_hint': None,
                'file_path': file_path,
                'line': decl_node.start_point[0] + 1,
            })
        elif child.type == 'generic_type':
            parent = _strip_generic_type(child)
            results.append({
                'class_name': class_name,
                'parent_name': parent,
                'relationship_type': rel_type,
                'parent_fqn_hint': None,
                'file_path': file_path,
                'line': decl_node.start_point[0] + 1,
            })


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


def extract_hierarchy(file_path: str, source_code: str, language: str) -> list[dict]:
    """Extract type hierarchy (extends/implements/delegation) from source code.

    Dispatches to language-specific extractors. Returns a list of dicts with keys:
    class_name, parent_name, relationship_type, parent_fqn_hint, file_path, line.

    Returns empty list on parse failure or unsupported language.
    """
    try:
        if language == 'java':
            parser = _get_parser('java')
            tree = parser.parse(source_code.encode())
            return _extract_java_hierarchy(tree.root_node, file_path)
        elif language == 'kotlin':
            parser = _get_parser('kotlin')
            tree = parser.parse(source_code.encode())
            return _extract_kotlin_hierarchy(tree.root_node, file_path)
        elif language == 'python':
            py_tree = python_ast.parse(source_code)
            return _extract_python_hierarchy(py_tree, file_path)
        elif language == 'typescript':
            parser = _get_parser('typescript')
            tree = parser.parse(source_code.encode())
            return _extract_typescript_hierarchy(tree.root_node, file_path)
    except Exception:
        return []
    return []
