"""
Import resolver: maps import strings to file paths within a repository.

Handles Java, Kotlin, and Python import resolution for monorepo layouts
where source roots appear at multiple levels (e.g., subproject/src/main/java/).
"""

import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=32)
def _find_source_roots(repo_path: str) -> dict[str, list[Path]]:
    """Find all source roots in a repo, grouped by language.

    Scans for **/src/main/java/, **/src/main/kotlin/, and Python roots.
    Cached per repo_path.
    """
    repo = Path(repo_path)
    roots: dict[str, list[Path]] = {'java': [], 'kotlin': [], 'python': []}

    for dirpath, dirnames, _ in os.walk(str(repo)):
        p = Path(dirpath)
        rel = p.relative_to(repo)

        # Skip hidden dirs and build outputs
        parts = rel.parts
        if any(part.startswith('.') or part in ('build', 'target', 'node_modules', '__pycache__') for part in parts):
            dirnames[:] = [d for d in dirnames if not d.startswith('.') and d not in ('build', 'target', 'node_modules')]
            continue

        if p.name == 'java' and len(parts) >= 3 and parts[-2] == 'main' and parts[-3] == 'src':
            roots['java'].append(p)
        elif p.name == 'kotlin' and len(parts) >= 3 and parts[-2] == 'main' and parts[-3] == 'src':
            roots['kotlin'].append(p)

    # Python: look for setup.py, pyproject.toml, or src/ layouts
    if (repo / 'setup.py').exists() or (repo / 'pyproject.toml').exists():
        roots['python'].append(repo)
    src = repo / 'src'
    if src.is_dir():
        roots['python'].append(src)

    return roots


def resolve_java_import(import_name: str, repo_path: str) -> str | None:
    """Resolve a Java import to a file path relative to the repo root.

    com.toasttab.analytics.Foo → subproject/src/main/java/com/toasttab/analytics/Foo.java
    Static imports: strip the member, resolve the class.
    Wildcard imports: resolve to the package directory's parent class or None.
    """
    # Strip wildcard suffix
    name = import_name.rstrip('.*').rstrip('.')

    # For static imports, the last component is a method/field — strip it
    # Static imports look like: com.toasttab.Foo.methodName
    # We try both with and without the last component
    parts = name.split('.')
    path_suffix = '/'.join(parts)

    roots = _find_source_roots(repo_path)
    repo = Path(repo_path)

    # Try Java source roots, then Kotlin roots (Java files can be in kotlin dirs and vice versa)
    for root_list in [roots['java'], roots['kotlin']]:
        for root in root_list:
            # Try exact match as .java
            candidate = root / (path_suffix + '.java')
            if candidate.exists():
                return str(candidate.relative_to(repo))

            # Try as .kt (Kotlin file with Java import)
            candidate = root / (path_suffix + '.kt')
            if candidate.exists():
                return str(candidate.relative_to(repo))

            # Static import: try without last component (it's a member name)
            if len(parts) > 1:
                class_suffix = '/'.join(parts[:-1])
                for ext in ('.java', '.kt'):
                    candidate = root / (class_suffix + ext)
                    if candidate.exists():
                        return str(candidate.relative_to(repo))

    return None


def resolve_kotlin_import(import_name: str, repo_path: str) -> str | None:
    """Resolve a Kotlin import to a file path. Same logic as Java."""
    return resolve_java_import(import_name, repo_path)


def resolve_python_import(import_name: str, repo_path: str) -> str | None:
    """Resolve a Python import to a file path relative to the repo root.

    com.toasttab.foo → com/toasttab/foo.py or com/toasttab/foo/__init__.py
    Relative imports (starting with .) are not resolved (need caller context).
    """
    if import_name.startswith('.'):
        return None

    parts = import_name.split('.')
    path_suffix = '/'.join(parts)

    roots = _find_source_roots(repo_path)
    repo = Path(repo_path)

    for root in roots['python']:
        # Try as module.py
        candidate = root / (path_suffix + '.py')
        if candidate.exists():
            return str(candidate.relative_to(repo))

        # Try as package/__init__.py
        candidate = root / path_suffix / '__init__.py'
        if candidate.exists():
            return str(candidate.relative_to(repo))

        # For from X.Y import Z, Z might be a submodule
        if len(parts) > 1:
            parent_suffix = '/'.join(parts[:-1])
            candidate = root / (parent_suffix + '.py')
            if candidate.exists():
                return str(candidate.relative_to(repo))

    return None


def resolve_import(import_name: str, repo_path: str, language: str) -> str | None:
    """Resolve an import string to a file path, dispatching by language."""
    if language in ('java', 'kotlin'):
        return resolve_java_import(import_name, repo_path)
    elif language == 'python':
        return resolve_python_import(import_name, repo_path)
    return None


def clear_cache():
    """Clear the source roots cache (call after repo changes)."""
    _find_source_roots.cache_clear()
