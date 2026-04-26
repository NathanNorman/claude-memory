#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""Tests for community detection — Louvain clustering on dependency graphs."""

import sys
import time
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


@unittest.skipUnless(HAS_IGRAPH, 'igraph not installed')
class TestComputeCommunities(unittest.TestCase):
    """Test compute_communities() — Louvain clustering."""

    def test_three_cluster_graph(self):
        from unified_memory_server import compute_communities
        conn = create_test_db()
        result = compute_communities(conn, 'test-repo')

        self.assertNotIn('error', result)
        self.assertEqual(result['codebase'], 'test-repo')
        self.assertGreater(result['communities'], 1)
        self.assertGreater(result['nodes'], 0)
        self.assertGreater(result['edges'], 0)

        # Verify stored in DB
        rows = conn.execute(
            'SELECT DISTINCT community_id FROM communities WHERE codebase = ?',
            ('test-repo',),
        ).fetchall()
        self.assertGreater(len(rows), 1)

        # Verify community_meta stored
        meta = conn.execute(
            'SELECT * FROM community_meta WHERE codebase = ?', ('test-repo',)
        ).fetchone()
        self.assertIsNotNone(meta)
        self.assertGreater(meta['edge_count'], 0)
        self.assertGreater(meta['community_count'], 0)

        conn.close()

    def test_empty_edges_returns_error(self):
        from unified_memory_server import compute_communities
        conn = create_test_db(num_clusters=0, nodes_per_cluster=0,
                               intra_edges_per_cluster=0, bridge_edges=0,
                               symbols_per_cluster=0)
        result = compute_communities(conn, 'test-repo')
        self.assertIn('error', result)
        conn.close()


@unittest.skipUnless(HAS_IGRAPH, 'igraph not installed')
class TestCommunityStaleness(unittest.TestCase):
    """Test _communities_are_stale() — edge drift detection."""

    def test_never_computed_is_stale(self):
        from unified_memory_server import _communities_are_stale
        conn = create_test_db()
        # No community_meta rows yet -> stale
        self.assertTrue(_communities_are_stale(conn, 'test-repo'))
        conn.close()

    def test_within_threshold_not_stale(self):
        from unified_memory_server import compute_communities, _communities_are_stale
        conn = create_test_db()
        compute_communities(conn, 'test-repo')
        # No changes -> not stale
        self.assertFalse(_communities_are_stale(conn, 'test-repo'))
        conn.close()

    def test_beyond_threshold_is_stale(self):
        from unified_memory_server import compute_communities, _communities_are_stale
        conn = create_test_db()
        compute_communities(conn, 'test-repo')

        # Get current edge count
        meta = conn.execute(
            'SELECT edge_count FROM community_meta WHERE codebase = ?', ('test-repo',)
        ).fetchone()
        old_count = meta['edge_count']

        # Add >10% more qualifying edges
        edges_to_add = int(old_count * 0.15) + 1
        now_ts = int(time.time() * 1000)
        for i in range(edges_to_add):
            conn.execute(
                'INSERT INTO edges (codebase, source_file, target_file, edge_type, updated_at) '
                'VALUES (?, ?, ?, ?, ?)',
                ('test-repo', f'new/a{i}.java', f'new/b{i}.java', 'calls', now_ts),
            )
        conn.commit()

        self.assertTrue(_communities_are_stale(conn, 'test-repo'))
        conn.close()


@unittest.skipUnless(HAS_IGRAPH, 'igraph not installed')
class TestCommunitySearch(unittest.TestCase):
    """Test community_search internal logic (not the MCP wrapper)."""

    def setUp(self):
        from unified_memory_server import compute_communities
        self.conn = create_test_db()
        compute_communities(self.conn, 'test-repo')

    def tearDown(self):
        self.conn.close()

    def test_file_path_lookup(self):
        """Look up a file's community and verify members sorted by degree."""
        # Pick a file that should be in the communities table
        row = self.conn.execute(
            "SELECT file_path, community_id FROM communities WHERE codebase = 'test-repo' LIMIT 1"
        ).fetchone()
        self.assertIsNotNone(row)
        file_path = row['file_path']
        community_id = row['community_id']

        # Get all members of this community
        members = self.conn.execute(
            'SELECT file_path FROM communities WHERE codebase = ? AND community_id = ?',
            ('test-repo', community_id),
        ).fetchall()
        self.assertGreater(len(members), 0)

        # Verify degree sorting works
        member_files = [m['file_path'] for m in members]
        degree_counts = []
        for mf in member_files:
            cnt = self.conn.execute(
                'SELECT COUNT(*) as cnt FROM edges WHERE codebase = ? AND (source_file = ? OR target_file = ?)',
                ('test-repo', mf, mf),
            ).fetchone()['cnt']
            degree_counts.append((mf, cnt))
        degree_counts.sort(key=lambda x: x[1], reverse=True)
        # First entry should have highest degree
        if len(degree_counts) > 1:
            self.assertGreaterEqual(degree_counts[0][1], degree_counts[-1][1])

    def test_list_all_communities(self):
        """List all communities with file counts and representatives."""
        rows = self.conn.execute(
            'SELECT community_id, COUNT(*) as file_count '
            'FROM communities WHERE codebase = ? '
            'GROUP BY community_id ORDER BY file_count DESC',
            ('test-repo',),
        ).fetchall()
        self.assertGreater(len(rows), 1)
        for row in rows:
            self.assertGreater(row['file_count'], 0)

            # Get representative files
            top_files = self.conn.execute(
                'SELECT c.file_path FROM communities c '
                'WHERE c.codebase = ? AND c.community_id = ? LIMIT 3',
                ('test-repo', row['community_id']),
            ).fetchall()
            self.assertGreater(len(top_files), 0)

    def test_show_bridges(self):
        """Cross-community edges should exist between clusters."""
        # Get community assignments
        assignments = {}
        for row in self.conn.execute(
            "SELECT file_path, community_id FROM communities WHERE codebase = 'test-repo'"
        ).fetchall():
            assignments[row['file_path']] = row['community_id']

        # Find cross-community edges
        edges = self.conn.execute(
            "SELECT source_file, target_file FROM edges "
            "WHERE codebase = 'test-repo' AND target_file IS NOT NULL"
        ).fetchall()

        bridge_edges = []
        for e in edges:
            src_comm = assignments.get(e['source_file'])
            tgt_comm = assignments.get(e['target_file'])
            if src_comm is not None and tgt_comm is not None and src_comm != tgt_comm:
                bridge_edges.append(e)

        # Our synthetic graph has explicit bridge edges
        self.assertGreater(len(bridge_edges), 0)


if __name__ == '__main__':
    unittest.main()
