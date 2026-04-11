"""
Code-aware chunking for codebase embedding.

Python files: AST-based chunking (functions, classes).
Other files: File-level chunking with size-based splitting.
"""

import ast
import re
from pathlib import Path


def chunk_python_file(path: str) -> list[dict]:
    """Extract top-level functions and classes from a Python file.

    Each function/class becomes one chunk with title, content, and line numbers.
    Functions shorter than 3 lines are skipped. Nested functions are included
    in their parent, not as separate chunks.
    """
    source = Path(path).read_text(errors='replace')
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # Fall back to file-level chunking on parse failure
        return _chunk_file_level(path, source)

    lines = source.split('\n')
    chunks = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno
            end = node.end_lineno or start
            if end - start + 1 < 3:
                continue
            # Include decorators
            if node.decorator_list:
                start = min(d.lineno for d in node.decorator_list)
            title = f'def {node.name}'
            content = '\n'.join(lines[start - 1:end])
            chunks.append({
                'title': title,
                'content': content,
                'start_line': start,
                'end_line': end,
            })

        elif isinstance(node, ast.ClassDef):
            start = node.lineno
            end = node.end_lineno or start
            # Include decorators
            if node.decorator_list:
                start = min(d.lineno for d in node.decorator_list)
            title = f'class {node.name}'
            content = '\n'.join(lines[start - 1:end])
            chunks.append({
                'title': title,
                'content': content,
                'start_line': start,
                'end_line': end,
            })

    # If no functions/classes found, fall back to file-level
    if not chunks:
        return _chunk_file_level(path, source)

    return chunks


def chunk_java_file(path: str) -> list[dict]:
    """Extract classes, interfaces, and methods from a Java file.

    Uses regex to split on top-level declarations. Each class/interface
    becomes one chunk; standalone methods become separate chunks.
    """
    source = Path(path).read_text(errors='replace')
    lines = source.split('\n')
    if len(lines) <= 10:
        return _chunk_file_level(path, source)

    # Pattern matches class/interface/enum declarations and method signatures
    decl_re = re.compile(
        r'^(\s*)((?:public|protected|private|static|abstract|final|synchronized)\s+)*'
        r'(?:class|interface|enum)\s+(\w+)',
    )
    method_re = re.compile(
        r'^(\s{0,4})((?:public|protected|private|static|abstract|final|synchronized|default)\s+)+'
        r'(?:<[^>]+>\s+)?(\w[\w<>\[\], ]*)\s+(\w+)\s*\(',
    )

    boundaries = []  # (line_idx, title, indent_level)
    for i, line in enumerate(lines):
        m = decl_re.match(line)
        if m:
            indent = len(m.group(1))
            name = m.group(3)
            kind = 'class' if 'class' in line else 'interface' if 'interface' in line else 'enum'
            boundaries.append((i, f'{kind} {name}', indent))
            continue
        m = method_re.match(line)
        if m:
            indent = len(m.group(1))
            name = m.group(4)
            boundaries.append((i, f'{name}()', indent))

    if not boundaries:
        return _chunk_file_level(path, source)

    return _boundaries_to_chunks(lines, boundaries, path)


def chunk_kotlin_file(path: str) -> list[dict]:
    """Extract classes, objects, and functions from a Kotlin file.

    Uses regex to split on top-level declarations.
    """
    source = Path(path).read_text(errors='replace')
    lines = source.split('\n')
    if len(lines) <= 10:
        return _chunk_file_level(path, source)

    decl_re = re.compile(
        r'^(\s*)((?:public|private|internal|protected|open|abstract|sealed|data|inline|value|'
        r'override|suspend|actual|expect)\s+)*'
        r'(?:class|interface|object|enum\s+class)\s+(\w+)',
    )
    fun_re = re.compile(
        r'^(\s*)((?:public|private|internal|protected|open|override|suspend|inline|actual|expect)\s+)*'
        r'fun\s+(?:<[^>]+>\s+)?(\w+)\s*[\(<]',
    )

    boundaries = []
    for i, line in enumerate(lines):
        m = decl_re.match(line)
        if m:
            indent = len(m.group(1))
            name = m.group(3)
            kind = 'class'
            for kw in ('interface', 'object', 'enum'):
                if kw in line:
                    kind = kw
                    break
            boundaries.append((i, f'{kind} {name}', indent))
            continue
        m = fun_re.match(line)
        if m:
            indent = len(m.group(1))
            name = m.group(3)
            boundaries.append((i, f'fun {name}', indent))

    if not boundaries:
        return _chunk_file_level(path, source)

    return _boundaries_to_chunks(lines, boundaries, path)


def chunk_shell_file(path: str) -> list[dict]:
    """Extract functions from a shell script.

    Splits on function declarations (both `function name` and `name()` styles).
    """
    source = Path(path).read_text(errors='replace')
    lines = source.split('\n')
    if len(lines) <= 10:
        return _chunk_file_level(path, source)

    fun_re = re.compile(
        r'^(\s*)(?:function\s+(\w+)|(\w+)\s*\(\s*\))\s*\{?\s*$'
    )

    boundaries = []
    for i, line in enumerate(lines):
        m = fun_re.match(line)
        if m:
            indent = len(m.group(1))
            name = m.group(2) or m.group(3)
            boundaries.append((i, f'function {name}', indent))

    if not boundaries:
        return _chunk_file_level(path, source)

    return _boundaries_to_chunks(lines, boundaries, path)


def _boundaries_to_chunks(
    lines: list[str],
    boundaries: list[tuple[int, str, int]],
    path: str,
) -> list[dict]:
    """Convert declaration boundaries into chunks.

    Classes/interfaces (indent 0-1) become single chunks that include all
    their methods. Top-level functions become separate chunks.
    """
    chunks = []
    total = len(lines)
    used = set()  # track line ranges consumed by class-level chunks

    # First pass: class-level declarations get everything until next same-indent boundary
    for idx, (start, title, indent) in enumerate(boundaries):
        is_type_decl = any(title.startswith(k) for k in ('class ', 'interface ', 'enum ', 'object '))
        if not is_type_decl:
            continue

        # Extend to next boundary at same or lower indent level
        end = total
        for next_start, _, next_indent in boundaries[idx + 1:]:
            if next_indent <= indent:
                end = next_start
                break

        content = '\n'.join(lines[start:end]).rstrip()
        if len(content.strip()) < 20:
            continue

        chunks.append({
            'title': title,
            'content': content,
            'start_line': start + 1,
            'end_line': end,
        })
        for ln in range(start, end):
            used.add(ln)

    # Second pass: standalone functions not inside a class
    for idx, (start, title, indent) in enumerate(boundaries):
        if start in used:
            continue
        is_type_decl = any(title.startswith(k) for k in ('class ', 'interface ', 'enum ', 'object '))
        if is_type_decl:
            continue

        end = total
        for next_start, _, next_indent in boundaries[idx + 1:]:
            if next_indent <= indent:
                end = next_start
                break
        else:
            if idx + 1 < len(boundaries):
                end = boundaries[idx + 1][0]

        content = '\n'.join(lines[start:end]).rstrip()
        if len(content.strip()) < 20:
            continue

        chunks.append({
            'title': title,
            'content': content,
            'start_line': start + 1,
            'end_line': end,
        })

    chunks.sort(key=lambda c: c['start_line'])

    if not chunks:
        return _chunk_file_level(path, '\n'.join(lines))

    return chunks


# Extension mapping for dispatch
_EXT_CHUNKERS = {
    '.java': chunk_java_file,
    '.kt': chunk_kotlin_file,
    '.sh': chunk_shell_file,
}


def chunk_file(path: str) -> list[dict]:
    """Chunk a file for embedding. Dispatches by extension.

    .py files: AST-aware chunking
    .java/.kt: Regex-based class/method chunking
    .sh: Regex-based function chunking
    Everything else: file-level chunking with size-based splitting
    """
    if path.endswith('.py'):
        return chunk_python_file(path)

    for ext, chunker in _EXT_CHUNKERS.items():
        if path.endswith(ext):
            return chunker(path)

    return _chunk_file_level(path)


def _chunk_file_level(path: str, source: str = None) -> list[dict]:
    """File-level chunking for non-Python files.

    Files ≤ 200 lines: one chunk.
    Files > 200 lines: split at blank-line boundaries into ~100-150 line chunks.
    """
    if source is None:
        source = Path(path).read_text(errors='replace')

    lines = source.split('\n')
    filename = Path(path).name
    total = len(lines)

    if total <= 200:
        return [{
            'title': filename,
            'content': source,
            'start_line': 1,
            'end_line': total,
        }]

    # Split at blank-line boundaries
    chunks = []
    chunk_start = 0
    last_blank = 0

    for i, line in enumerate(lines):
        if line.strip() == '':
            last_blank = i

        chunk_len = i - chunk_start + 1
        if chunk_len >= 100 and last_blank > chunk_start:
            # Split at the last blank line
            chunk_content = '\n'.join(lines[chunk_start:last_blank])
            if chunk_content.strip():
                chunks.append({
                    'title': f'{filename}:{chunk_start + 1}',
                    'content': chunk_content,
                    'start_line': chunk_start + 1,
                    'end_line': last_blank,
                })
            chunk_start = last_blank + 1
            last_blank = chunk_start

    # Remaining lines
    if chunk_start < total:
        chunk_content = '\n'.join(lines[chunk_start:])
        if chunk_content.strip():
            chunks.append({
                'title': f'{filename}:{chunk_start + 1}',
                'content': chunk_content,
                'start_line': chunk_start + 1,
                'end_line': total,
            })

    return chunks if chunks else [{
        'title': filename,
        'content': source,
        'start_line': 1,
        'end_line': total,
    }]
