#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""Cross-repo dependency graph builder.

Parses build files (Gradle, Maven, npm, pip) to extract inter-repo
dependency edges and stores them in the unified-memory edges table.

Usage:
    source ~/.claude-memory/graphiti-venv/bin/activate
    python3 scripts/cross_repo_deps.py --path ~/toast-analytics --name toast-analytics
    python3 scripts/cross_repo_deps.py --path ~/toast-analytics --name toast-analytics --update
    python3 scripts/cross_repo_deps.py --list
    python3 scripts/cross_repo_deps.py --remove --name toast-analytics
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

DEFAULT_DB = Path.home() / '.claude-memory' / 'index' / 'memory.db'

# ── Parsers ──────────────────────────────────────────────────────────────

# Gradle: implementation, api, compileOnly, runtimeOnly, testImplementation, etc.
_GRADLE_DEP_RE = re.compile(
    r'''(?:implementation|api|compileOnly|runtimeOnly|testImplementation|'''
    r'''testRuntimeOnly|kapt|annotationProcessor)\s*[\(]?\s*['"]([^'"]+)['"]\s*[\)]?''',
    re.MULTILINE,
)

# Gradle project(':subproject') references
_GRADLE_PROJECT_RE = re.compile(
    r'''(?:implementation|api|compileOnly|runtimeOnly|testImplementation)\s*[\(]?\s*project\s*\(\s*['":]+([^'")\s]+)['"]*\s*\)''',
    re.MULTILINE,
)


def parse_gradle(content: str, build_file: str) -> list[dict]:
    """Parse Gradle/Gradle KTS build file for dependencies."""
    deps = []
    for m in _GRADLE_DEP_RE.finditer(content):
        coord = m.group(1)
        parts = coord.split(':')
        if len(parts) >= 2:
            scope = 'implementation'
            # Try to detect actual scope from the match
            line_start = content.rfind('\n', 0, m.start()) + 1
            line = content[line_start:m.start() + len(m.group(0))]
            for s in ('api', 'compileOnly', 'runtimeOnly', 'testImplementation',
                      'testRuntimeOnly', 'kapt', 'annotationProcessor'):
                if s in line:
                    scope = s
                    break
            deps.append({
                'artifact': coord,
                'group': parts[0],
                'name': parts[1],
                'version': parts[2] if len(parts) > 2 else '',
                'scope': scope,
                'build_file': build_file,
            })

    for m in _GRADLE_PROJECT_RE.finditer(content):
        subproject = m.group(1).strip(':')
        deps.append({
            'artifact': f'project:{subproject}',
            'group': 'project',
            'name': subproject,
            'version': '',
            'scope': 'implementation',
            'build_file': build_file,
        })

    return deps


# Maven: <dependency> blocks
_MAVEN_DEP_RE = re.compile(
    r'<dependency>\s*'
    r'<groupId>([^<]+)</groupId>\s*'
    r'<artifactId>([^<]+)</artifactId>\s*'
    r'(?:<version>([^<]*)</version>)?\s*'
    r'(?:<scope>([^<]*)</scope>)?',
    re.DOTALL,
)


def parse_maven(content: str, build_file: str) -> list[dict]:
    """Parse Maven pom.xml for dependencies."""
    deps = []
    for m in _MAVEN_DEP_RE.finditer(content):
        group = m.group(1).strip()
        artifact = m.group(2).strip()
        version = (m.group(3) or '').strip()
        scope = (m.group(4) or 'compile').strip()
        deps.append({
            'artifact': f'{group}:{artifact}:{version}' if version else f'{group}:{artifact}',
            'group': group,
            'name': artifact,
            'version': version,
            'scope': scope,
            'build_file': build_file,
        })
    return deps


def parse_npm(content: str, build_file: str) -> list[dict]:
    """Parse package.json for dependencies."""
    deps = []
    try:
        pkg = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return []

    for dep_type in ('dependencies', 'devDependencies', 'peerDependencies'):
        scope = {
            'dependencies': 'runtime',
            'devDependencies': 'dev',
            'peerDependencies': 'peer',
        }[dep_type]
        for name, version in (pkg.get(dep_type) or {}).items():
            deps.append({
                'artifact': f'{name}@{version}',
                'group': 'npm',
                'name': name,
                'version': str(version),
                'scope': scope,
                'build_file': build_file,
            })
    return deps


def parse_pip_requirements(content: str, build_file: str) -> list[dict]:
    """Parse requirements.txt for dependencies."""
    deps = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('-'):
            continue
        # Split on version specifiers
        m = re.match(r'^([A-Za-z0-9_.-]+)\s*(.*)$', line)
        if m:
            name = m.group(1)
            version = m.group(2).strip()
            deps.append({
                'artifact': f'{name}{version}' if version else name,
                'group': 'pip',
                'name': name,
                'version': version,
                'scope': 'runtime',
                'build_file': build_file,
            })
    return deps


def parse_pyproject(content: str, build_file: str) -> list[dict]:
    """Parse pyproject.toml [project].dependencies for dependencies."""
    deps = []
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            return []

    try:
        data = tomllib.loads(content)
    except Exception:
        return []

    for dep_str in data.get('project', {}).get('dependencies', []):
        m = re.match(r'^([A-Za-z0-9_.-]+)\s*(.*)$', dep_str)
        if m:
            name = m.group(1)
            version = m.group(2).strip()
            deps.append({
                'artifact': f'{name}{version}' if version else name,
                'group': 'pip',
                'name': name,
                'version': version,
                'scope': 'runtime',
                'build_file': build_file,
            })
    return deps


# ── Build file discovery ─────────────────────────────────────────────────

BUILD_FILE_PARSERS = {
    'build.gradle': parse_gradle,
    'build.gradle.kts': parse_gradle,
    'pom.xml': parse_maven,
    'package.json': parse_npm,
    'requirements.txt': parse_pip_requirements,
    'pyproject.toml': parse_pyproject,
}


def discover_build_files(repo_path: Path) -> list[tuple[callable, Path]]:
    """Walk repo directory and detect build files with their parsers."""
    results = []
    for root, dirs, files in repo_path.walk():
        # Skip common non-build directories
        dirs[:] = [d for d in dirs if d not in (
            '.git', 'node_modules', '__pycache__', '.gradle', 'build',
            'target', 'dist', '.venv', 'venv',
        )]
        for fname in files:
            if fname in BUILD_FILE_PARSERS:
                fpath = root / fname
                results.append((BUILD_FILE_PARSERS[fname], fpath))
    return results


# ── Edge storage ─────────────────────────────────────────────────────────


def index_repo_deps(
    db_path: Path, repo_path: Path, repo_name: str, update: bool = False,
) -> dict:
    """Parse build files and store repo_dependency edges."""
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.execute('PRAGMA busy_timeout = 5000')
    conn.execute('PRAGMA journal_mode = WAL')
    conn.row_factory = sqlite3.Row

    # Ensure edges table exists (in case running standalone)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codebase TEXT NOT NULL,
            source_file TEXT NOT NULL,
            target_file TEXT,
            edge_type TEXT NOT NULL,
            metadata TEXT,
            updated_at INTEGER NOT NULL
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS codebase_meta (
            codebase TEXT NOT NULL,
            file_path TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            indexed_at TEXT NOT NULL,
            PRIMARY KEY (codebase, file_path)
        )
    ''')

    build_files = discover_build_files(repo_path)
    if not build_files:
        conn.close()
        return {'build_files': 0, 'deps_found': 0, 'edges_written': 0, 'skipped': 0}

    # Content-hash staleness check for --update mode
    existing_hashes = {}
    if update:
        rows = conn.execute(
            'SELECT file_path, content_hash FROM codebase_meta WHERE codebase = ?',
            (f'repo_deps:{repo_name}',),
        ).fetchall()
        existing_hashes = {r['file_path']: r['content_hash'] for r in rows}

    all_deps = []
    skipped = 0
    now = int(time.time())

    for parser, fpath in build_files:
        rel_path = str(fpath.relative_to(repo_path))
        content = fpath.read_text(errors='replace')
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        if update and existing_hashes.get(rel_path) == content_hash:
            skipped += 1
            continue

        deps = parser(content, rel_path)
        all_deps.extend(deps)

        # Update content hash
        conn.execute(
            'INSERT INTO codebase_meta (codebase, file_path, content_hash, indexed_at) '
            'VALUES (?, ?, ?, ?) '
            'ON CONFLICT (codebase, file_path) DO UPDATE SET '
            'content_hash = excluded.content_hash, indexed_at = excluded.indexed_at',
            (f'repo_deps:{repo_name}', rel_path, content_hash, str(now)),
        )

    if not update:
        # Full re-index: delete old repo_dependency edges for this repo
        conn.execute(
            "DELETE FROM edges WHERE codebase = ? AND edge_type = 'repo_dependency'",
            (repo_name,),
        )

    edges_written = 0
    for dep in all_deps:
        artifact = dep['artifact']
        metadata = json.dumps({
            'build_file': dep['build_file'],
            'scope': dep['scope'],
            'version': dep['version'],
            'group': dep['group'],
        })
        conn.execute(
            'INSERT INTO edges (codebase, source_file, target_file, edge_type, metadata, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (repo_name, repo_name, f'{dep["group"]}:{dep["name"]}',
             'repo_dependency', metadata, now),
        )
        edges_written += 1

    conn.commit()
    conn.close()

    return {
        'build_files': len(build_files),
        'deps_found': len(all_deps),
        'edges_written': edges_written,
        'skipped': skipped,
    }


def remove_repo_deps(db_path: Path, repo_name: str) -> dict:
    """Remove all repo_dependency edges for a given repo."""
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.execute('PRAGMA busy_timeout = 5000')

    deleted = conn.execute(
        "DELETE FROM edges WHERE codebase = ? AND edge_type = 'repo_dependency'",
        (repo_name,),
    ).rowcount
    conn.execute(
        'DELETE FROM codebase_meta WHERE codebase = ?',
        (f'repo_deps:{repo_name}',),
    )
    conn.commit()
    conn.close()
    return {'deleted': deleted}


def list_repo_deps(db_path: Path) -> list[dict]:
    """List indexed repos and their dependency edge counts."""
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.execute('PRAGMA busy_timeout = 5000')
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT codebase, COUNT(*) as edge_count "
        "FROM edges WHERE edge_type = 'repo_dependency' "
        "GROUP BY codebase ORDER BY edge_count DESC",
    ).fetchall()
    conn.close()
    return [{'codebase': r['codebase'], 'edge_count': r['edge_count']} for r in rows]


# ── CLI ──────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description='Cross-repo dependency graph builder')
    parser.add_argument('--path', type=Path, help='Path to repository root')
    parser.add_argument('--name', help='Repository name')
    parser.add_argument('--update', action='store_true', help='Incremental update (skip unchanged files)')
    parser.add_argument('--list', action='store_true', help='List indexed repos')
    parser.add_argument('--remove', action='store_true', help='Remove repo dependency edges')
    parser.add_argument('--db', type=Path, default=DEFAULT_DB, help='Database path')
    args = parser.parse_args()

    if args.list:
        repos = list_repo_deps(args.db)
        if not repos:
            print('No repos indexed', file=sys.stderr)
        for r in repos:
            print(f"  {r['codebase']}: {r['edge_count']} dependencies")
        return

    if not args.name:
        print('--name is required', file=sys.stderr)
        sys.exit(1)

    if args.remove:
        result = remove_repo_deps(args.db, args.name)
        print(f"Removed {result['deleted']} edges for {args.name}", file=sys.stderr)
        return

    if not args.path:
        print('--path is required for indexing', file=sys.stderr)
        sys.exit(1)

    if not args.path.is_dir():
        print(f'Not a directory: {args.path}', file=sys.stderr)
        sys.exit(1)

    print(f'Indexing dependencies for {args.name} from {args.path}...', file=sys.stderr)
    result = index_repo_deps(args.db, args.path, args.name, update=args.update)
    print(
        f"Done: {result['build_files']} build files, {result['deps_found']} deps, "
        f"{result['edges_written']} edges written, {result['skipped']} skipped",
        file=sys.stderr,
    )


if __name__ == '__main__':
    main()
