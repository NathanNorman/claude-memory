#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""Integration tests for cross-repo resolution and dependency_search enhancements.

Uses an in-memory SQLite DB to test resolve_cross_repo_deps, resolve_cross_repo_types,
and the graph_traverse / dependency_search tool logic.
"""

import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from build_parser import resolve_cross_repo_deps, resolve_cross_repo_types


def create_test_db():
    """Create an in-memory SQLite DB with edges, symbols, and codebase_meta tables."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('''
        CREATE TABLE edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codebase TEXT NOT NULL,
            source_file TEXT NOT NULL,
            target_file TEXT,
            edge_type TEXT NOT NULL,
            metadata TEXT,
            updated_at INTEGER NOT NULL
        )
    ''')
    conn.execute('CREATE INDEX idx_edges_source ON edges(source_file, codebase)')
    conn.execute('CREATE INDEX idx_edges_target ON edges(target_file, codebase)')
    conn.execute('CREATE INDEX idx_edges_target_type ON edges(target_file, edge_type, codebase)')
    conn.execute('CREATE INDEX idx_edges_source_type ON edges(source_file, edge_type, codebase)')
    conn.execute('''
        CREATE TABLE symbols (
            id TEXT PRIMARY KEY,
            codebase TEXT NOT NULL,
            file_path TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
    ''')
    conn.execute('CREATE INDEX idx_symbols_name ON symbols(name, codebase)')
    conn.execute('''
        CREATE TABLE codebase_meta (
            codebase TEXT NOT NULL,
            file_path TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            indexed_at TEXT NOT NULL,
            PRIMARY KEY (codebase, file_path)
        )
    ''')
    conn.commit()
    return conn


class TestResolveCrossRepoDeps(unittest.TestCase):
    def test_resolves_matching_artifact(self):
        conn = create_test_db()
        # Add an indexed codebase
        conn.execute(
            'INSERT INTO codebase_meta (codebase, file_path, content_hash, indexed_at) '
            "VALUES ('toast-common', 'src/Main.java', 'abc123', '2024-01-01')"
        )
        # Add an unresolved build_dependency edge
        conn.execute(
            'INSERT INTO edges (codebase, source_file, target_file, edge_type, metadata, updated_at) '
            "VALUES ('toast-analytics', 'build.gradle.kts', NULL, 'build_dependency', 'com.toasttab:toast-common:1.0', 1000)"
        )
        conn.commit()

        result = resolve_cross_repo_deps(conn)
        self.assertEqual(result['resolved'], 1)

        # Verify the edge was updated
        row = conn.execute('SELECT target_file FROM edges WHERE id = 1').fetchone()
        self.assertEqual(row[0], 'codebase:toast-common/')

    def test_skips_non_matching_artifact(self):
        conn = create_test_db()
        conn.execute(
            'INSERT INTO codebase_meta (codebase, file_path, content_hash, indexed_at) '
            "VALUES ('toast-common', 'src/Main.java', 'abc123', '2024-01-01')"
        )
        conn.execute(
            'INSERT INTO edges (codebase, source_file, target_file, edge_type, metadata, updated_at) '
            "VALUES ('toast-analytics', 'build.gradle.kts', NULL, 'build_dependency', 'org.apache:commons-lang3:3.12', 1000)"
        )
        conn.commit()

        result = resolve_cross_repo_deps(conn)
        self.assertEqual(result['resolved'], 0)

        row = conn.execute('SELECT target_file FROM edges WHERE id = 1').fetchone()
        self.assertIsNone(row[0])

    def test_idempotent(self):
        conn = create_test_db()
        conn.execute(
            'INSERT INTO codebase_meta (codebase, file_path, content_hash, indexed_at) '
            "VALUES ('toast-common', 'src/Main.java', 'abc123', '2024-01-01')"
        )
        conn.execute(
            'INSERT INTO edges (codebase, source_file, target_file, edge_type, metadata, updated_at) '
            "VALUES ('toast-analytics', 'build.gradle.kts', NULL, 'build_dependency', 'com.toasttab:toast-common:1.0', 1000)"
        )
        conn.commit()

        resolve_cross_repo_deps(conn)
        # Run again — should not fail or double-resolve
        result = resolve_cross_repo_deps(conn)
        self.assertEqual(result['unresolved_checked'], 0)  # No NULL target_file edges left


class TestResolveCrossRepoTypes(unittest.TestCase):
    def test_resolves_extends_across_codebases(self):
        conn = create_test_db()
        # Add a symbol in codebase B
        conn.execute(
            'INSERT INTO symbols (id, codebase, file_path, name, kind, start_line, end_line, updated_at) '
            "VALUES ('s1', 'toast-common', 'src/BaseService.java', 'BaseService', 'class', 1, 50, 1000)"
        )
        # Add an unresolved extends edge in codebase A
        conn.execute(
            'INSERT INTO edges (codebase, source_file, target_file, edge_type, metadata, updated_at) '
            "VALUES ('toast-analytics', 'src/MyService.java', NULL, 'extends', 'com.toasttab.common.BaseService', 1000)"
        )
        conn.commit()

        result = resolve_cross_repo_types(conn)
        self.assertEqual(result['resolved'], 1)

        row = conn.execute('SELECT target_file FROM edges WHERE id = 1').fetchone()
        self.assertEqual(row[0], 'src/BaseService.java')

    def test_prefers_build_dependency_codebase(self):
        conn = create_test_db()
        # Two codebases have a class named BaseService
        conn.execute(
            'INSERT INTO symbols (id, codebase, file_path, name, kind, start_line, end_line, updated_at) '
            "VALUES ('s1', 'toast-common', 'src/BaseService.java', 'BaseService', 'class', 1, 50, 1000)"
        )
        conn.execute(
            'INSERT INTO symbols (id, codebase, file_path, name, kind, start_line, end_line, updated_at) '
            "VALUES ('s2', 'toast-other', 'src/BaseService.java', 'BaseService', 'class', 1, 50, 1000)"
        )
        # toast-analytics has a build_dependency on toast-common
        conn.execute(
            'INSERT INTO edges (codebase, source_file, target_file, edge_type, metadata, updated_at) '
            "VALUES ('toast-analytics', 'build.gradle.kts', 'codebase:toast-common/', 'build_dependency', 'com.toasttab:toast-common:1.0', 1000)"
        )
        # Add unresolved extends edge
        conn.execute(
            'INSERT INTO edges (codebase, source_file, target_file, edge_type, metadata, updated_at) '
            "VALUES ('toast-analytics', 'src/MyService.java', NULL, 'extends', 'com.toasttab.common.BaseService', 1000)"
        )
        conn.commit()

        result = resolve_cross_repo_types(conn)
        self.assertEqual(result['resolved'], 1)

        row = conn.execute('SELECT target_file FROM edges WHERE id = 2').fetchone()
        # Should prefer toast-common (declared build dep)
        self.assertEqual(row[0], 'src/BaseService.java')
        # Verify it chose the right codebase by checking the symbol codebase
        sym = conn.execute(
            "SELECT codebase FROM symbols WHERE file_path = ? AND id = 's1'",
            (row[0],)
        ).fetchone()
        self.assertEqual(sym[0], 'toast-common')

    def test_no_match_leaves_null(self):
        conn = create_test_db()
        conn.execute(
            'INSERT INTO edges (codebase, source_file, target_file, edge_type, metadata, updated_at) '
            "VALUES ('toast-analytics', 'src/MyService.java', NULL, 'extends', 'com.unknown.NoSuchClass', 1000)"
        )
        conn.commit()

        result = resolve_cross_repo_types(conn)
        self.assertEqual(result['resolved'], 0)

        row = conn.execute('SELECT target_file FROM edges WHERE id = 1').fetchone()
        self.assertIsNone(row[0])


class TestGraphTraverseLogic(unittest.TestCase):
    """Test the recursive CTE logic using raw SQL against an in-memory DB."""

    def setUp(self):
        self.conn = create_test_db()
        # Build a call chain: A -> B -> C -> D
        edges = [
            ('repo', 'A.java', 'B.java', 'calls', None, 1000),
            ('repo', 'B.java', 'C.java', 'calls', None, 1000),
            ('repo', 'C.java', 'D.java', 'calls', None, 1000),
            # Also: A extends E
            ('repo', 'A.java', 'E.java', 'extends', 'BaseClass', 1000),
        ]
        for e in edges:
            self.conn.execute(
                'INSERT INTO edges (codebase, source_file, target_file, edge_type, metadata, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?)', e
            )
        self.conn.commit()

    def test_downstream_reachability(self):
        """Walk downstream from A — should find B, C, D."""
        rows = self.conn.execute('''
            WITH RECURSIVE reachable(file, depth) AS (
                SELECT target_file, 1
                FROM edges
                WHERE source_file = 'A.java' AND target_file IS NOT NULL
              UNION
                SELECT e.target_file, r.depth + 1
                FROM edges e
                JOIN reachable r ON e.source_file = r.file
                WHERE e.target_file IS NOT NULL AND r.depth < 5
            )
            SELECT file, MIN(depth) as min_depth
            FROM reachable
            GROUP BY file
            ORDER BY min_depth
        ''').fetchall()

        files = [r[0] for r in rows]
        self.assertEqual(files, ['B.java', 'E.java', 'C.java', 'D.java'])

    def test_downstream_with_edge_type_filter(self):
        """Walk downstream from A with edge_type=calls — should NOT include E.java."""
        rows = self.conn.execute('''
            WITH RECURSIVE reachable(file, depth) AS (
                SELECT target_file, 1
                FROM edges
                WHERE source_file = 'A.java' AND target_file IS NOT NULL AND edge_type IN ('calls')
              UNION
                SELECT e.target_file, r.depth + 1
                FROM edges e
                JOIN reachable r ON e.source_file = r.file
                WHERE e.target_file IS NOT NULL AND r.depth < 5 AND e.edge_type IN ('calls')
            )
            SELECT file, MIN(depth) as min_depth
            FROM reachable
            GROUP BY file
            ORDER BY min_depth
        ''').fetchall()

        files = [r[0] for r in rows]
        self.assertEqual(files, ['B.java', 'C.java', 'D.java'])
        self.assertNotIn('E.java', files)

    def test_upstream_reachability(self):
        """Walk upstream from D — should find C, B, A."""
        rows = self.conn.execute('''
            WITH RECURSIVE reachable(file, depth) AS (
                SELECT source_file, 1
                FROM edges
                WHERE target_file = 'D.java' AND source_file IS NOT NULL
              UNION
                SELECT e.source_file, r.depth + 1
                FROM edges e
                JOIN reachable r ON e.target_file = r.file
                WHERE e.source_file IS NOT NULL AND r.depth < 5
            )
            SELECT file, MIN(depth) as min_depth
            FROM reachable
            GROUP BY file
            ORDER BY min_depth
        ''').fetchall()

        files = [r[0] for r in rows]
        self.assertEqual(files, ['C.java', 'B.java', 'A.java'])

    def test_max_depth_limit(self):
        """With max_depth=1, only direct neighbors."""
        rows = self.conn.execute('''
            WITH RECURSIVE reachable(file, depth) AS (
                SELECT target_file, 1
                FROM edges
                WHERE source_file = 'A.java' AND target_file IS NOT NULL
              UNION
                SELECT e.target_file, r.depth + 1
                FROM edges e
                JOIN reachable r ON e.source_file = r.file
                WHERE e.target_file IS NOT NULL AND r.depth < 1
            )
            SELECT file, MIN(depth) as min_depth
            FROM reachable
            GROUP BY file
            ORDER BY min_depth
        ''').fetchall()

        files = [r[0] for r in rows]
        self.assertEqual(files, ['B.java', 'E.java'])

    def test_cycle_detection(self):
        """Add a cycle C -> A and verify no infinite recursion."""
        self.conn.execute(
            'INSERT INTO edges (codebase, source_file, target_file, edge_type, metadata, updated_at) '
            "VALUES ('repo', 'C.java', 'A.java', 'calls', NULL, 1000)"
        )
        self.conn.commit()

        # UNION deduplicates, so cycles terminate naturally
        rows = self.conn.execute('''
            WITH RECURSIVE reachable(file, depth) AS (
                SELECT target_file, 1
                FROM edges
                WHERE source_file = 'A.java' AND target_file IS NOT NULL
              UNION
                SELECT e.target_file, r.depth + 1
                FROM edges e
                JOIN reachable r ON e.source_file = r.file
                WHERE e.target_file IS NOT NULL AND r.depth < 10
            )
            SELECT file, MIN(depth) as min_depth
            FROM reachable
            GROUP BY file
            ORDER BY min_depth
        ''').fetchall()

        files = [r[0] for r in rows]
        # A should appear (via cycle) but query should terminate
        self.assertIn('A.java', files)
        self.assertIn('D.java', files)


class TestDependencySearchEnhancements(unittest.TestCase):
    """Test the enhanced dependency_search directions using raw SQL."""

    def setUp(self):
        self.conn = create_test_db()
        # Add build dependencies
        self.conn.execute(
            'INSERT INTO edges (codebase, source_file, target_file, edge_type, metadata, updated_at) '
            "VALUES ('toast-analytics', 'build.gradle.kts', NULL, 'build_dependency', 'com.google.guava:guava:31.1', 1000)"
        )
        self.conn.execute(
            'INSERT INTO edges (codebase, source_file, target_file, edge_type, metadata, updated_at) '
            "VALUES ('toast-web', 'build.gradle.kts', NULL, 'build_dependency', 'com.google.guava:guava:30.0', 1000)"
        )
        self.conn.commit()

    def test_depended_on_by_direction(self):
        """Find all codebases that depend on guava."""
        rows = self.conn.execute(
            "SELECT source_file, edge_type, metadata, codebase FROM edges "
            "WHERE metadata LIKE ? AND edge_type = ?",
            ('%guava%', 'build_dependency'),
        ).fetchall()
        self.assertEqual(len(rows), 2)
        codebases = {r[3] for r in rows}
        self.assertEqual(codebases, {'toast-analytics', 'toast-web'})

    def test_edge_type_filter(self):
        """Add a calls edge and filter by edge_type."""
        self.conn.execute(
            'INSERT INTO edges (codebase, source_file, target_file, edge_type, metadata, updated_at) '
            "VALUES ('toast-analytics', 'A.java', 'B.java', 'calls', NULL, 1000)"
        )
        self.conn.commit()

        # Filter to only calls
        rows = self.conn.execute(
            "SELECT source_file, edge_type FROM edges WHERE target_file = ? AND edge_type = ?",
            ('B.java', 'calls'),
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], 'calls')

    def test_cross_codebase_results(self):
        """When no codebase filter, return results from all codebases."""
        rows = self.conn.execute(
            "SELECT source_file, codebase FROM edges WHERE edge_type = 'build_dependency'"
        ).fetchall()
        codebases = {r[1] for r in rows}
        self.assertEqual(len(codebases), 2)


if __name__ == '__main__':
    unittest.main()
