"""
Import resolver: maps import strings to file paths within a repository.

Handles Java, Kotlin, Python, and TypeScript/JavaScript import resolution
for monorepo layouts where source roots appear at multiple levels.
"""

import json
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
    roots: dict[str, list[Path]] = {'java': [], 'kotlin': [], 'python': [], 'typescript': []}

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

    # TypeScript: look for package.json, src/ dirs
    if (repo / 'package.json').exists():
        roots['typescript'].append(repo)
    ts_src = repo / 'src'
    if ts_src.is_dir() and ts_src not in roots['typescript']:
        roots['typescript'].append(ts_src)

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


# ──────────────────────────────────────────────────────────────
# TypeScript/JavaScript import resolution
# ──────────────────────────────────────────────────────────────

# Extensions to probe when resolving TS/JS imports
_TS_PROBE_EXTENSIONS = ['.ts', '.tsx', '.js', '.jsx']
_TS_INDEX_FILES = ['index.ts', 'index.tsx', 'index.js', 'index.jsx']


@lru_cache(maxsize=32)
def _read_tsconfig_paths(repo_path: str) -> tuple[str, dict[str, list[str]]]:
    """Read compilerOptions.paths and baseUrl from tsconfig.json at repo root.

    Returns (baseUrl, paths_dict). Both default to empty if tsconfig is absent
    or doesn't contain these fields.
    """
    tsconfig_path = Path(repo_path) / 'tsconfig.json'
    if not tsconfig_path.exists():
        return ('', {})

    try:
        text = tsconfig_path.read_text(errors='replace')
        config = json.loads(text)
        compiler_opts = config.get('compilerOptions', {})
        base_url = compiler_opts.get('baseUrl', '')
        paths = compiler_opts.get('paths', {})
        return (base_url, paths)
    except Exception:
        return ('', {})


def _probe_ts_file(base: Path) -> Path | None:
    """Try to resolve a base path to an actual TS/JS file.

    Tries: base.ts, base.tsx, base.js, base.jsx, base/index.ts, etc.
    Also returns base directly if it already has a recognized extension and exists.
    """
    # If the path already has an extension and exists
    if base.suffix in ('.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs') and base.exists():
        return base

    # Try adding extensions
    for ext in _TS_PROBE_EXTENSIONS:
        candidate = base.parent / (base.name + ext)
        if candidate.exists():
            return candidate

    # Try as directory with index file
    if base.is_dir():
        for idx in _TS_INDEX_FILES:
            candidate = base / idx
            if candidate.exists():
                return candidate

    return None


def resolve_typescript_import(
    import_name: str,
    repo_path: str,
    source_file: str | None = None,
) -> str | None:
    """Resolve a TypeScript/JavaScript import to a file path relative to repo root.

    Handles:
    - Relative imports (./foo, ../bar) with extension probing
    - Path aliases from tsconfig.json (e.g., @/* -> src/*)
    - Falls back to @/ -> src/ convention if no tsconfig
    - Bare specifiers (react, lodash) return None (external)
    """
    repo = Path(repo_path)

    # Relative imports
    if import_name.startswith('./') or import_name.startswith('../'):
        if source_file:
            source_dir = (repo / source_file).parent
        else:
            source_dir = repo
        target_base = (source_dir / import_name).resolve()
        resolved = _probe_ts_file(target_base)
        if resolved and str(resolved).startswith(str(repo)):
            return str(resolved.relative_to(repo))
        return None

    # Path alias resolution
    base_url, paths = _read_tsconfig_paths(repo_path)
    base_dir = repo / base_url if base_url else repo

    # Check configured path aliases
    for pattern, targets in paths.items():
        if pattern.endswith('/*'):
            prefix = pattern[:-2]  # e.g., '@' from '@/*'
            if import_name.startswith(prefix + '/'):
                suffix = import_name[len(prefix) + 1:]
                for target_pattern in targets:
                    if target_pattern.endswith('/*'):
                        target_dir = target_pattern[:-2]  # e.g., 'src' from 'src/*'
                        target_base = (base_dir / target_dir / suffix).resolve()
                        resolved = _probe_ts_file(target_base)
                        if resolved and str(resolved).startswith(str(repo)):
                            return str(resolved.relative_to(repo))
                return None
        elif pattern == import_name:
            # Exact match alias
            for target in targets:
                target_base = (base_dir / target).resolve()
                resolved = _probe_ts_file(target_base)
                if resolved and str(resolved).startswith(str(repo)):
                    return str(resolved.relative_to(repo))
            return None

    # Fallback: @/ -> src/ convention (when no tsconfig paths matched)
    if import_name.startswith('@/'):
        suffix = import_name[2:]
        target_base = (repo / 'src' / suffix).resolve()
        resolved = _probe_ts_file(target_base)
        if resolved and str(resolved).startswith(str(repo)):
            return str(resolved.relative_to(repo))
        return None

    # Bare specifier (npm package) -- not resolvable within repo
    return None


def resolve_import(import_name: str, repo_path: str, language: str) -> str | None:
    """Resolve an import string to a file path, dispatching by language."""
    if language in ('java', 'kotlin'):
        return resolve_java_import(import_name, repo_path)
    elif language == 'python':
        return resolve_python_import(import_name, repo_path)
    elif language == 'typescript':
        return resolve_typescript_import(import_name, repo_path)
    return None


def clear_cache():
    """Clear the source roots cache (call after repo changes)."""
    _find_source_roots.cache_clear()
    _read_tsconfig_paths.cache_clear()
