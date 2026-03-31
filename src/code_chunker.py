"""
Code-aware chunking for codebase embedding.

Python files: AST-based chunking (functions, classes).
Other files: File-level chunking with size-based splitting.
"""

import ast
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


def chunk_file(path: str) -> list[dict]:
    """Chunk a file for embedding. Dispatches by extension.

    .py files: AST-aware chunking
    Everything else: file-level chunking with size-based splitting
    """
    if path.endswith('.py'):
        return chunk_python_file(path)
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
