#!/usr/bin/env python3
"""
Build file parsers for cross-repo dependency resolution.

Extracts dependency declarations from Gradle (KTS + Groovy), Maven, pip, and npm
build files. Returns a unified list of dependency records.
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]


# ──────────────────────────────────────────────────────────────
# Gradle Kotlin DSL
# ──────────────────────────────────────────────────────────────

# Matches: implementation("group:artifact:version"), api("g:a:v"), etc.
_GRADLE_KTS_DEP_RE = re.compile(
    r'(?:implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly|kapt|annotationProcessor)'
    r'\s*\(\s*"([^"]+)"\s*\)',
)

# Matches: implementation(project(":module-name"))
_GRADLE_KTS_PROJECT_RE = re.compile(
    r'(?:implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly|kapt|annotationProcessor)'
    r'\s*\(\s*project\s*\(\s*"([^"]+)"\s*\)\s*\)',
)

# Matches: implementation(libs.foo.bar) — version catalog references
_GRADLE_KTS_CATALOG_RE = re.compile(
    r'(?:implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly|kapt|annotationProcessor)'
    r'\s*\(\s*libs\.([a-zA-Z0-9_.]+)\s*\)',
)


def _parse_scope(line: str) -> str:
    """Extract the Gradle configuration name (scope) from a dependency line."""
    for scope in ('testRuntimeOnly', 'testImplementation', 'runtimeOnly',
                  'compileOnly', 'annotationProcessor', 'kapt', 'api', 'implementation'):
        if scope in line:
            return scope
    return 'implementation'


def _parse_coordinate(coord: str) -> dict[str, str]:
    """Parse 'group:artifact:version' into components."""
    parts = coord.split(':')
    return {
        'group': parts[0] if len(parts) > 0 else '',
        'artifact': parts[1] if len(parts) > 1 else '',
        'version': parts[2] if len(parts) > 2 else '',
    }


def parse_gradle_kts(content: str, file_path: str) -> list[dict]:
    """Parse a build.gradle.kts file for dependencies."""
    deps: list[dict] = []

    for match in _GRADLE_KTS_PROJECT_RE.finditer(content):
        module_path = match.group(1)
        deps.append({
            'group': '', 'artifact': module_path, 'version': '',
            'scope': _parse_scope(match.group(0)),
            'is_internal': True,
            'module_path': module_path,
            'source_file': file_path,
            'coordinate': f'project:{module_path}',
        })

    for match in _GRADLE_KTS_DEP_RE.finditer(content):
        coord_str = match.group(1)
        # Skip if this was already captured as a project dep
        if 'project(' in match.group(0):
            continue
        coord = _parse_coordinate(coord_str)
        deps.append({
            **coord,
            'scope': _parse_scope(match.group(0)),
            'is_internal': False,
            'module_path': None,
            'source_file': file_path,
            'coordinate': coord_str,
        })

    return deps


# ──────────────────────────────────────────────────────────────
# Gradle Groovy DSL
# ──────────────────────────────────────────────────────────────

# String notation: implementation 'group:artifact:version'
_GRADLE_GROOVY_STRING_RE = re.compile(
    r"(?:implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly|kapt|annotationProcessor)"
    r"\s+'([^']+)'",
)

# Double-quoted variant
_GRADLE_GROOVY_DSTRING_RE = re.compile(
    r'(?:implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly|kapt|annotationProcessor)'
    r'\s+"([^"]+)"',
)

# Map notation: implementation group: 'g', name: 'a', version: 'v'
_GRADLE_GROOVY_MAP_RE = re.compile(
    r"(?:implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly)"
    r"\s+group:\s*'([^']+)'\s*,\s*name:\s*'([^']+)'\s*(?:,\s*version:\s*'([^']*)')?"
)

# Project deps: implementation project(':module')
_GRADLE_GROOVY_PROJECT_RE = re.compile(
    r"(?:implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly)"
    r"""\s+project\s*\(\s*['"]([^'"]+)['"]\s*\)""",
)


def parse_gradle_groovy(content: str, file_path: str) -> list[dict]:
    """Parse a build.gradle file for dependencies."""
    deps: list[dict] = []

    for match in _GRADLE_GROOVY_PROJECT_RE.finditer(content):
        module_path = match.group(1)
        deps.append({
            'group': '', 'artifact': module_path, 'version': '',
            'scope': _parse_scope(match.group(0)),
            'is_internal': True,
            'module_path': module_path,
            'source_file': file_path,
            'coordinate': f'project:{module_path}',
        })

    for pattern in (_GRADLE_GROOVY_STRING_RE, _GRADLE_GROOVY_DSTRING_RE):
        for match in pattern.finditer(content):
            coord_str = match.group(1)
            coord = _parse_coordinate(coord_str)
            deps.append({
                **coord,
                'scope': _parse_scope(match.group(0)),
                'is_internal': False,
                'module_path': None,
                'source_file': file_path,
                'coordinate': coord_str,
            })

    for match in _GRADLE_GROOVY_MAP_RE.finditer(content):
        group, name, version = match.group(1), match.group(2), match.group(3) or ''
        coord_str = f'{group}:{name}:{version}'.rstrip(':')
        deps.append({
            'group': group, 'artifact': name, 'version': version,
            'scope': _parse_scope(match.group(0)),
            'is_internal': False,
            'module_path': None,
            'source_file': file_path,
            'coordinate': coord_str,
        })

    return deps


# ──────────────────────────────────────────────────────────────
# Gradle Version Catalog (libs.versions.toml)
# ──────────────────────────────────────────────────────────────

def parse_version_catalog(content: str) -> dict[str, str]:
    """Parse gradle/libs.versions.toml and return alias -> coordinate map."""
    try:
        data = tomllib.loads(content)
    except Exception:
        return {}

    versions = data.get('versions', {})
    libraries = data.get('libraries', {})
    catalog: dict[str, str] = {}

    for alias, lib in libraries.items():
        if isinstance(lib, str):
            catalog[alias] = lib
            continue
        if isinstance(lib, dict):
            module = lib.get('module', '')
            if not module:
                group = lib.get('group', '')
                name = lib.get('name', '')
                module = f'{group}:{name}' if group and name else ''
            version = lib.get('version', '')
            if isinstance(version, dict):
                ref = version.get('ref', '')
                version = versions.get(ref, ref)
            elif isinstance(version, str) and version in versions:
                version = versions[version]
            coord = f'{module}:{version}' if version else module
            # Normalize alias: foo-bar and foo.bar both become foo.bar
            normalized = alias.replace('-', '.').replace('_', '.')
            catalog[normalized] = coord

    return catalog


def resolve_catalog_refs(
    deps: list[dict], catalog: dict[str, str], file_path: str,
) -> list[dict]:
    """Resolve libs.X.Y references in Gradle KTS using a version catalog."""
    if not catalog:
        return deps

    resolved: list[dict] = []
    for dep in deps:
        if dep.get('coordinate', '').startswith('libs.'):
            alias = dep['coordinate'][5:]  # strip 'libs.'
            coord_str = catalog.get(alias, '')
            if coord_str:
                coord = _parse_coordinate(coord_str)
                dep = {**dep, **coord, 'coordinate': coord_str, 'is_internal': False}
        resolved.append(dep)
    return resolved


# ──────────────────────────────────────────────────────────────
# Gradle Settings (include declarations)
# ──────────────────────────────────────────────────────────────

_SETTINGS_INCLUDE_RE = re.compile(r'''include\s*\(\s*["']([^"']+)["']''')
_SETTINGS_INCLUDE_MULTI_RE = re.compile(r"include\s*\(([^)]+)\)")


def parse_settings_gradle(content: str) -> list[str]:
    """Extract included module paths from settings.gradle[.kts]."""
    modules: list[str] = []

    for match in _SETTINGS_INCLUDE_MULTI_RE.finditer(content):
        block = match.group(1)
        for m in re.finditer(r"""["']([^"']+)["']""", block):
            modules.append(m.group(1))

    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for mod in modules:
        if mod not in seen:
            seen.add(mod)
            result.append(mod)
    return result


# ──────────────────────────────────────────────────────────────
# Maven POM
# ──────────────────────────────────────────────────────────────

def parse_maven_pom(content: str, file_path: str) -> list[dict]:
    """Parse a pom.xml for dependencies using xml.etree."""
    import xml.etree.ElementTree as ET

    deps: list[dict] = []
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return deps

    # Determine namespace
    ns = ''
    if root.tag.startswith('{'):
        ns = root.tag.split('}')[0] + '}'

    # Collect properties for ${...} interpolation
    props: dict[str, str] = {}
    props_el = root.find(f'{ns}properties')
    if props_el is not None:
        for child in props_el:
            tag = child.tag.replace(ns, '')
            if child.text:
                props[tag] = child.text.strip()

    # Also check parent for version
    parent = root.find(f'{ns}parent')
    if parent is not None:
        pv = parent.find(f'{ns}version')
        if pv is not None and pv.text:
            props['project.parent.version'] = pv.text.strip()
            props['project.version'] = pv.text.strip()

    def interpolate(text: str | None) -> str:
        if not text:
            return ''
        text = text.strip()
        # Resolve ${property.name}
        for key, val in props.items():
            text = text.replace(f'${{{key}}}', val)
        return text

    # Parse <dependencies> section
    for deps_section in root.iter(f'{ns}dependency'):
        group = interpolate(deps_section.findtext(f'{ns}groupId'))
        artifact = interpolate(deps_section.findtext(f'{ns}artifactId'))
        version = interpolate(deps_section.findtext(f'{ns}version'))
        scope = deps_section.findtext(f'{ns}scope') or 'compile'

        if group and artifact:
            coord = f'{group}:{artifact}:{version}'.rstrip(':')
            deps.append({
                'group': group, 'artifact': artifact, 'version': version,
                'scope': scope.strip(),
                'is_internal': False,
                'module_path': None,
                'source_file': file_path,
                'coordinate': coord,
            })

    return deps


# ──────────────────────────────────────────────────────────────
# Python (pyproject.toml + requirements.txt)
# ──────────────────────────────────────────────────────────────

_PIP_REQ_RE = re.compile(r'^([A-Za-z0-9][A-Za-z0-9._-]*)')


def parse_pyproject_toml(content: str, file_path: str) -> list[dict]:
    """Parse pyproject.toml [project].dependencies."""
    deps: list[dict] = []
    try:
        data = tomllib.loads(content)
    except Exception:
        return deps

    dep_list = data.get('project', {}).get('dependencies', [])
    for dep_str in dep_list:
        match = _PIP_REQ_RE.match(dep_str)
        if match:
            name = match.group(1)
            deps.append({
                'group': 'pypi', 'artifact': name, 'version': '',
                'scope': 'runtime',
                'is_internal': False,
                'module_path': None,
                'source_file': file_path,
                'coordinate': f'pypi:{name}',
            })

    return deps


def parse_requirements_txt(content: str, file_path: str) -> list[dict]:
    """Parse requirements.txt line by line."""
    deps: list[dict] = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('-'):
            continue
        match = _PIP_REQ_RE.match(line)
        if match:
            name = match.group(1)
            deps.append({
                'group': 'pypi', 'artifact': name, 'version': '',
                'scope': 'runtime',
                'is_internal': False,
                'module_path': None,
                'source_file': file_path,
                'coordinate': f'pypi:{name}',
            })
    return deps


# ──────────────────────────────────────────────────────────────
# npm (package.json)
# ──────────────────────────────────────────────────────────────

def parse_package_json(content: str, file_path: str) -> list[dict]:
    """Parse package.json for dependencies."""
    deps: list[dict] = []
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return deps

    scope_map = {
        'dependencies': 'runtime',
        'devDependencies': 'dev',
        'peerDependencies': 'peer',
    }

    for section, scope in scope_map.items():
        for name, version in data.get(section, {}).items():
            deps.append({
                'group': 'npm', 'artifact': name, 'version': version or '',
                'scope': scope,
                'is_internal': False,
                'module_path': None,
                'source_file': file_path,
                'coordinate': f'npm:{name}',
            })

    return deps


# ──────────────────────────────────────────────────────────────
# Top-level dispatcher
# ──────────────────────────────────────────────────────────────

# Build file patterns to discover
_BUILD_FILE_PATTERNS = [
    ('**/build.gradle.kts', parse_gradle_kts),
    ('**/build.gradle', parse_gradle_groovy),
    ('**/pom.xml', parse_maven_pom),
    ('**/pyproject.toml', parse_pyproject_toml),
    ('**/requirements.txt', parse_requirements_txt),
    ('**/package.json', parse_package_json),
]


def parse_build_files(repo_path: str | Path) -> list[dict]:
    """Discover all build files in a repo and parse dependencies.

    Returns a unified list of dependency records.
    """
    repo = Path(repo_path)
    all_deps: list[dict] = []

    # Load version catalog if present
    catalog: dict[str, str] = {}
    catalog_path = repo / 'gradle' / 'libs.versions.toml'
    if catalog_path.exists():
        try:
            catalog = parse_version_catalog(catalog_path.read_text())
        except Exception as e:
            print(f'[build-parser] Warning: failed to parse version catalog: {e}', file=sys.stderr)

    for pattern, parser in _BUILD_FILE_PATTERNS:
        for build_file in repo.glob(pattern):
            # Skip node_modules, build dirs, etc.
            parts = build_file.parts
            if any(p in ('.gradle', 'node_modules', 'build', '.git', 'target') for p in parts):
                continue

            try:
                content = build_file.read_text()
            except Exception:
                continue

            rel_path = str(build_file.relative_to(repo))
            deps = parser(content, rel_path)

            # Resolve catalog references for Gradle KTS files
            if parser == parse_gradle_kts and catalog:
                deps = resolve_catalog_refs(deps, catalog, rel_path)

            all_deps.extend(deps)

    return all_deps


# ──────────────────────────────────────────────────────────────
# Cross-repo resolution
# ──────────────────────────────────────────────────────────────

def resolve_cross_repo_deps(conn: Any) -> dict:
    """Match build_dependency edges with NULL target_file against indexed codebases.

    For each unresolved external dep, check if its artifact name matches a codebase
    name in codebase_meta. If so, set target_file = 'codebase:<name>/'.
    """
    rows = conn.execute(
        "SELECT id, metadata FROM edges WHERE edge_type = 'build_dependency' AND target_file IS NULL"
    ).fetchall()

    # Get all indexed codebase names
    codebases = {
        row[0] for row in conn.execute('SELECT DISTINCT codebase FROM codebase_meta').fetchall()
    }

    resolved = 0
    for row in rows:
        edge_id = row[0]
        metadata = row[1] or ''
        # Extract artifact from coordinate (group:artifact:version or project:name)
        parts = metadata.split(':')
        artifact = parts[1] if len(parts) > 1 else parts[0]

        # Match artifact against codebase names (case-insensitive)
        for cb in codebases:
            if cb.lower() == artifact.lower() or artifact.lower() in cb.lower():
                conn.execute(
                    'UPDATE edges SET target_file = ? WHERE id = ?',
                    (f'codebase:{cb}/', edge_id),
                )
                resolved += 1
                break

    conn.commit()
    return {'unresolved_checked': len(rows), 'resolved': resolved}


def resolve_cross_repo_types(conn: Any) -> dict:
    """Match unresolved extends/implements edges against symbols across codebases.

    For edges with target_file NULL and edge_type in (extends, implements),
    extract the class name from metadata FQN and search symbols table.
    """
    rows = conn.execute(
        "SELECT id, codebase, metadata FROM edges "
        "WHERE edge_type IN ('extends', 'implements') AND target_file IS NULL"
    ).fetchall()

    resolved = 0
    for row in rows:
        edge_id = row[0]
        source_codebase = row[1]
        fqn = row[2] or ''

        # Extract simple class name from FQN (last segment after .)
        class_name = fqn.rsplit('.', 1)[-1] if '.' in fqn else fqn
        if not class_name:
            continue

        # Search symbols in all codebases (prefer ones from declared build dependencies)
        matches = conn.execute(
            'SELECT file_path, codebase FROM symbols WHERE name = ? AND codebase != ?',
            (class_name, source_codebase),
        ).fetchall()

        if not matches:
            continue

        # Prefer a match from a codebase that is a declared build dependency
        dep_codebases = {
            r[0].replace('codebase:', '').rstrip('/')
            for r in conn.execute(
                "SELECT target_file FROM edges WHERE codebase = ? AND edge_type = 'build_dependency' AND target_file IS NOT NULL",
                (source_codebase,),
            ).fetchall()
        }

        best = None
        for m in matches:
            if m[1] in dep_codebases:
                best = m
                break
        if best is None:
            best = matches[0]

        conn.execute(
            'UPDATE edges SET target_file = ? WHERE id = ?',
            (best[0], edge_id),
        )
        resolved += 1

    conn.commit()
    return {'unresolved_checked': len(rows), 'resolved': resolved}
