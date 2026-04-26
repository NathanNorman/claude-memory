#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""Tests for cross-repo-deps.py parsers and edge storage."""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cross_repo_deps import (
    parse_gradle,
    parse_maven,
    parse_npm,
    parse_pip_requirements,
    parse_pyproject,
    discover_build_files,
    index_repo_deps,
)


class TestGradleParser(unittest.TestCase):
    def test_implementation_dep(self):
        content = '''
        dependencies {
            implementation 'com.google.guava:guava:31.1-jre'
            api "com.toasttab:toast-common:2.0"
        }
        '''
        deps = parse_gradle(content, 'build.gradle')
        self.assertEqual(len(deps), 2)
        self.assertEqual(deps[0]['group'], 'com.google.guava')
        self.assertEqual(deps[0]['name'], 'guava')
        self.assertEqual(deps[0]['version'], '31.1-jre')
        self.assertEqual(deps[1]['group'], 'com.toasttab')
        self.assertEqual(deps[1]['name'], 'toast-common')

    def test_kts_format(self):
        content = '''
        dependencies {
            implementation("org.jetbrains.kotlin:kotlin-stdlib:1.8.0")
            testImplementation("junit:junit:4.13.2")
        }
        '''
        deps = parse_gradle(content, 'build.gradle.kts')
        self.assertEqual(len(deps), 2)
        self.assertEqual(deps[0]['name'], 'kotlin-stdlib')
        self.assertEqual(deps[1]['name'], 'junit')

    def test_project_reference(self):
        content = '''
        dependencies {
            implementation project(':submodule-core')
        }
        '''
        deps = parse_gradle(content, 'build.gradle')
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0]['group'], 'project')
        self.assertEqual(deps[0]['name'], 'submodule-core')

    def test_empty_file(self):
        deps = parse_gradle('', 'build.gradle')
        self.assertEqual(deps, [])


class TestMavenParser(unittest.TestCase):
    def test_basic_dependency(self):
        content = '''
        <dependencies>
            <dependency>
                <groupId>com.google.guava</groupId>
                <artifactId>guava</artifactId>
                <version>31.1-jre</version>
            </dependency>
        </dependencies>
        '''
        deps = parse_maven(content, 'pom.xml')
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0]['group'], 'com.google.guava')
        self.assertEqual(deps[0]['name'], 'guava')
        self.assertEqual(deps[0]['version'], '31.1-jre')

    def test_dependency_with_scope(self):
        content = '''
        <dependency>
            <groupId>junit</groupId>
            <artifactId>junit</artifactId>
            <version>4.13.2</version>
            <scope>test</scope>
        </dependency>
        '''
        deps = parse_maven(content, 'pom.xml')
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0]['scope'], 'test')

    def test_dependency_no_version(self):
        content = '''
        <dependency>
            <groupId>org.apache.commons</groupId>
            <artifactId>commons-lang3</artifactId>
        </dependency>
        '''
        deps = parse_maven(content, 'pom.xml')
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0]['version'], '')


class TestNpmParser(unittest.TestCase):
    def test_package_json(self):
        content = json.dumps({
            'name': 'my-app',
            'dependencies': {'react': '^18.0.0', 'express': '4.18.2'},
            'devDependencies': {'jest': '^29.0.0'},
        })
        deps = parse_npm(content, 'package.json')
        self.assertEqual(len(deps), 3)
        runtime = [d for d in deps if d['scope'] == 'runtime']
        dev = [d for d in deps if d['scope'] == 'dev']
        self.assertEqual(len(runtime), 2)
        self.assertEqual(len(dev), 1)

    def test_invalid_json(self):
        deps = parse_npm('not valid json', 'package.json')
        self.assertEqual(deps, [])

    def test_empty_deps(self):
        content = json.dumps({'name': 'empty-app'})
        deps = parse_npm(content, 'package.json')
        self.assertEqual(deps, [])


class TestPipParser(unittest.TestCase):
    def test_requirements_txt(self):
        content = '''
requests>=2.28.0
flask==2.3.0
# comment
-e git+https://example.com
numpy
        '''
        deps = parse_pip_requirements(content, 'requirements.txt')
        self.assertEqual(len(deps), 3)
        names = {d['name'] for d in deps}
        self.assertEqual(names, {'requests', 'flask', 'numpy'})

    def test_empty_requirements(self):
        deps = parse_pip_requirements('', 'requirements.txt')
        self.assertEqual(deps, [])


class TestDiscoverBuildFiles(unittest.TestCase):
    def test_discovers_multiple_types(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / 'build.gradle').write_text("implementation 'foo:bar:1.0'")
            (root / 'package.json').write_text('{"dependencies": {}}')
            (root / 'sub').mkdir()
            (root / 'sub' / 'pom.xml').write_text('<dependency></dependency>')

            results = discover_build_files(root)
            fnames = {r[1].name for r in results}
            self.assertEqual(fnames, {'build.gradle', 'package.json', 'pom.xml'})

    def test_skips_node_modules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / 'node_modules').mkdir()
            (root / 'node_modules' / 'package.json').write_text('{}')
            (root / 'package.json').write_text('{}')

            results = discover_build_files(root)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0][1].name, 'package.json')


class TestIndexRepoDeps(unittest.TestCase):
    def test_full_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / 'build.gradle').write_text(
                "implementation 'com.google.guava:guava:31.1'\n"
                "api 'com.toasttab:toast-common:2.0'"
            )

            db_path = root / 'test.db'
            result = index_repo_deps(db_path, root, 'test-repo')

            self.assertEqual(result['build_files'], 1)
            self.assertEqual(result['deps_found'], 2)
            self.assertEqual(result['edges_written'], 2)

            # Verify edges in DB
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM edges WHERE edge_type = 'repo_dependency'"
            ).fetchall()
            self.assertEqual(len(rows), 2)
            targets = {r['target_file'] for r in rows}
            self.assertIn('com.google.guava:guava', targets)
            self.assertIn('com.toasttab:toast-common', targets)
            conn.close()

    def test_update_skips_unchanged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / 'build.gradle').write_text("implementation 'foo:bar:1.0'")

            db_path = root / 'test.db'
            index_repo_deps(db_path, root, 'test-repo')

            # Second run with --update should skip
            result = index_repo_deps(db_path, root, 'test-repo', update=True)
            self.assertEqual(result['skipped'], 1)
            self.assertEqual(result['edges_written'], 0)


class TestDependencySearchNewDirections(unittest.TestCase):
    """Test repo_depends_on and repo_depended_on_by via raw SQL."""

    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('''
            CREATE TABLE edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codebase TEXT NOT NULL, source_file TEXT NOT NULL,
                target_file TEXT, edge_type TEXT NOT NULL,
                metadata TEXT, updated_at INTEGER NOT NULL
            )
        ''')
        # toast-analytics depends on guava and toast-common
        self.conn.execute(
            'INSERT INTO edges VALUES (NULL, ?, ?, ?, ?, ?, ?)',
            ('toast-analytics', 'toast-analytics', 'com.google.guava:guava',
             'repo_dependency', '{"scope":"implementation"}', 1000),
        )
        self.conn.execute(
            'INSERT INTO edges VALUES (NULL, ?, ?, ?, ?, ?, ?)',
            ('toast-analytics', 'toast-analytics', 'com.toasttab:toast-common',
             'repo_dependency', '{"scope":"api"}', 1000),
        )
        # toast-web also depends on guava
        self.conn.execute(
            'INSERT INTO edges VALUES (NULL, ?, ?, ?, ?, ?, ?)',
            ('toast-web', 'toast-web', 'com.google.guava:guava',
             'repo_dependency', '{"scope":"implementation"}', 1000),
        )
        self.conn.commit()

    def test_repo_depends_on(self):
        """Find what toast-analytics depends on."""
        rows = self.conn.execute(
            "SELECT target_file FROM edges "
            "WHERE source_file = ? AND edge_type = 'repo_dependency'",
            ('toast-analytics',),
        ).fetchall()
        targets = {r['target_file'] for r in rows}
        self.assertEqual(targets, {'com.google.guava:guava', 'com.toasttab:toast-common'})

    def test_repo_depended_on_by(self):
        """Find who depends on guava."""
        rows = self.conn.execute(
            "SELECT source_file, codebase FROM edges "
            "WHERE target_file LIKE ? AND edge_type = 'repo_dependency'",
            ('%guava%',),
        ).fetchall()
        codebases = {r['codebase'] for r in rows}
        self.assertEqual(codebases, {'toast-analytics', 'toast-web'})


if __name__ == '__main__':
    unittest.main()
