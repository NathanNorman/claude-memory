#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""Tests for GraphSidecar — igraph in-process graph queries."""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))
sys.path.insert(0, str(Path(__file__).parent))

from test_fixtures import create_test_db

try:
    import igraph
    HAS_IGRAPH = True
except ImportError:
    HAS_IGRAPH = False


def _write_db_to_file(conn: sqlite3.Connection) -> Path:
    """Dump in-memory DB to a temp file (GraphSidecar needs a file path)."""
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    disk_conn = sqlite3.connect(tmp.name)
    conn.backup(disk_conn)
    disk_conn.close()
    return Path(tmp.name)


@unittest.skipUnless(HAS_IGRAPH, 'igraph not installed')
class TestGraphSidecarLoad(unittest.TestCase):
    """Test loading edges from SQLite into igraph."""

    def setUp(self):
        self.conn = create_test_db()
        self.db_path = _write_db_to_file(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db_path)

    def test_load_populated_db(self):
        from unified_memory_server import GraphSidecar
        gs = GraphSidecar(self.db_path)
        result = gs.load()
        self.assertTrue(result)
        self.assertTrue(gs.is_loaded)
        stats = gs.get_stats()
        self.assertEqual(stats['status'], 'loaded')
        self.assertGreater(stats['nodes'], 0)
        self.assertGreater(stats['edges'], 0)

    def test_load_empty_db(self):
        from unified_memory_server import GraphSidecar
        # Create empty DB with schema but no edges
        empty_conn = create_test_db(num_clusters=0, nodes_per_cluster=0,
                                     intra_edges_per_cluster=0, bridge_edges=0,
                                     symbols_per_cluster=0)
        empty_path = _write_db_to_file(empty_conn)
        empty_conn.close()
        try:
            gs = GraphSidecar(empty_path)
            result = gs.load()
            self.assertFalse(result)
            self.assertFalse(gs.is_loaded)
        finally:
            os.unlink(empty_path)

    def test_load_codebase_scoped(self):
        from unified_memory_server import GraphSidecar
        gs = GraphSidecar(self.db_path)
        result = gs.load(codebase='test-repo')
        self.assertTrue(result)
        stats = gs.get_stats()
        self.assertEqual(stats['codebase'], 'test-repo')

    def test_load_nonexistent_codebase(self):
        from unified_memory_server import GraphSidecar
        gs = GraphSidecar(self.db_path)
        result = gs.load(codebase='does-not-exist')
        self.assertFalse(result)


@unittest.skipUnless(HAS_IGRAPH, 'igraph not installed')
class TestGraphSidecarTraverse(unittest.TestCase):
    """Test BFS traversal with various options."""

    def setUp(self):
        self.conn = create_test_db()
        self.db_path = _write_db_to_file(self.conn)
        from unified_memory_server import GraphSidecar
        self.gs = GraphSidecar(self.db_path)
        self.gs.load()

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db_path)

    def test_downstream_bfs(self):
        # Hub node should have downstream connections
        results = self.gs.traverse('cluster0/file0.java', direction='downstream')
        self.assertGreater(len(results), 0)
        for r in results:
            self.assertIn('file', r)
            self.assertIn('depth', r)
            self.assertGreater(r['depth'], 0)

    def test_upstream_bfs(self):
        # A spoke node should have the hub as upstream
        results = self.gs.traverse('cluster0/file1.java', direction='upstream')
        # Should find at least the hub node upstream
        files = [r['file'] for r in results]
        self.assertIn('cluster0/file0.java', files)

    def test_edge_type_filtering(self):
        # Filter to only 'calls' edges
        results_calls = self.gs.traverse(
            'cluster0/file0.java', direction='downstream', edge_types=['calls'],
        )
        results_all = self.gs.traverse(
            'cluster0/file0.java', direction='downstream',
        )
        # Filtered should be <= unfiltered
        self.assertLessEqual(len(results_calls), len(results_all))

    def test_max_depth_cap(self):
        results = self.gs.traverse('cluster0/file0.java', max_depth=1)
        for r in results:
            self.assertLessEqual(r['depth'], 1)

    def test_max_results_cap(self):
        results = self.gs.traverse('cluster0/file0.java', max_results=3)
        self.assertLessEqual(len(results), 3)

    def test_include_paths(self):
        results = self.gs.traverse(
            'cluster0/file0.java', direction='downstream', include_paths=True,
        )
        if results:
            # At least some results should have path info
            paths_found = [r for r in results if 'path' in r]
            self.assertGreater(len(paths_found), 0)

    def test_nonexistent_start_file(self):
        results = self.gs.traverse('nonexistent/file.java')
        self.assertEqual(results, [])


@unittest.skipUnless(HAS_IGRAPH, 'igraph not installed')
class TestGraphSidecarStaleness(unittest.TestCase):
    """Test staleness detection based on edge count drift."""

    def setUp(self):
        self.conn = create_test_db()
        self.db_path = _write_db_to_file(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db_path)

    def test_no_drift_not_stale(self):
        from unified_memory_server import GraphSidecar
        gs = GraphSidecar(self.db_path)
        gs.load()
        self.assertFalse(gs.is_stale())

    def test_significant_drift_is_stale(self):
        from unified_memory_server import GraphSidecar
        gs = GraphSidecar(self.db_path)
        gs.load()
        # Add >10% more edges to the DB
        edge_count = gs._edge_count_at_load
        edges_to_add = int(edge_count * 0.15) + 1
        disk_conn = sqlite3.connect(str(self.db_path))
        for i in range(edges_to_add):
            disk_conn.execute(
                'INSERT INTO edges (codebase, source_file, target_file, edge_type, updated_at) '
                'VALUES (?, ?, ?, ?, ?)',
                ('test-repo', f'new/src{i}.java', f'new/tgt{i}.java', 'calls', 0),
            )
        disk_conn.commit()
        disk_conn.close()
        self.assertTrue(gs.is_stale())

    def test_not_loaded_not_stale(self):
        from unified_memory_server import GraphSidecar
        gs = GraphSidecar(self.db_path)
        # Never loaded -> is_stale returns False
        self.assertFalse(gs.is_stale())


@unittest.skipUnless(HAS_IGRAPH, 'igraph not installed')
class TestGraphSidecarMemoryCap(unittest.TestCase):
    """Test memory cap (MAX_EDGES)."""

    def setUp(self):
        self.conn = create_test_db()
        self.db_path = _write_db_to_file(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db_path)

    def test_max_edges_limit(self):
        from unified_memory_server import GraphSidecar
        gs = GraphSidecar(self.db_path)
        gs.MAX_EDGES = 50
        gs.load()
        if gs.is_loaded:
            stats = gs.get_stats()
            self.assertLessEqual(stats['edges'], 50)


@unittest.skipUnless(HAS_IGRAPH, 'igraph not installed')
class TestGraphSidecarRebuild(unittest.TestCase):
    """Test atomic rebuild behavior."""

    def setUp(self):
        self.conn = create_test_db()
        self.db_path = _write_db_to_file(self.conn)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db_path)

    def test_successful_rebuild(self):
        from unified_memory_server import GraphSidecar
        gs = GraphSidecar(self.db_path)
        gs.load()
        old_edges = gs.get_stats()['edges']

        # Add edges to DB
        disk_conn = sqlite3.connect(str(self.db_path))
        for i in range(5):
            disk_conn.execute(
                'INSERT INTO edges (codebase, source_file, target_file, edge_type, updated_at) '
                'VALUES (?, ?, ?, ?, ?)',
                ('test-repo', f'rebuild/src{i}.java', f'rebuild/tgt{i}.java', 'calls', 0),
            )
        disk_conn.commit()
        disk_conn.close()

        result = gs.rebuild()
        self.assertTrue(result)
        new_edges = gs.get_stats()['edges']
        self.assertGreater(new_edges, old_edges)

    def test_failed_rebuild_preserves_old_graph(self):
        from unified_memory_server import GraphSidecar
        gs = GraphSidecar(self.db_path)
        gs.load()
        old_stats = gs.get_stats()

        # Rebuild with a codebase that has no edges -> load fails
        result = gs.rebuild(codebase='nonexistent-codebase')
        self.assertFalse(result)
        # Old graph should be preserved
        self.assertTrue(gs.is_loaded)
        self.assertEqual(gs.get_stats()['nodes'], old_stats['nodes'])


if __name__ == '__main__':
    unittest.main()
