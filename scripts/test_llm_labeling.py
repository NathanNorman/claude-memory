#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""Tests for LLM semantic labeling of high-value code symbols."""

import json
import sqlite3
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))
sys.path.insert(0, str(Path(__file__).parent))

from test_fixtures import create_test_db


def _populate_high_value_graph(conn: sqlite3.Connection) -> None:
    """Add symbols with varying incoming edge counts for high-value node testing."""
    codebase = 'test-repo'
    now_ts = int(time.time() * 1000)

    # Create a hub symbol with many incoming edges (>= 5)
    hub_file = 'src/core/Service.java'
    conn.execute(
        'INSERT OR REPLACE INTO symbols (id, codebase, file_path, name, kind, start_line, end_line, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (f'{codebase}:{hub_file}:Service', codebase, hub_file, 'Service', 'class', 1, 100, now_ts),
    )
    # 7 incoming edges to hub
    for i in range(7):
        caller = f'src/callers/Caller{i}.java'
        conn.execute(
            'INSERT INTO edges (codebase, source_file, target_file, edge_type, updated_at) '
            'VALUES (?, ?, ?, ?, ?)',
            (codebase, caller, hub_file, 'calls', now_ts),
        )

    # A low-value symbol with few edges
    low_file = 'src/util/Helper.java'
    conn.execute(
        'INSERT OR REPLACE INTO symbols (id, codebase, file_path, name, kind, start_line, end_line, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (f'{codebase}:{low_file}:Helper', codebase, low_file, 'Helper', 'class', 1, 20, now_ts),
    )
    conn.execute(
        'INSERT INTO edges (codebase, source_file, target_file, edge_type, updated_at) '
        'VALUES (?, ?, ?, ?, ?)',
        (codebase, 'src/other.java', low_file, 'calls', now_ts),
    )

    # An entry point file (main)
    main_file = 'src/main/Application.java'
    conn.execute(
        'INSERT OR REPLACE INTO symbols (id, codebase, file_path, name, kind, start_line, end_line, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (f'{codebase}:{main_file}:Application', codebase, main_file, 'Application', 'class', 1, 50, now_ts),
    )

    # Codebase meta for content hash lookups
    for fp in [hub_file, low_file, main_file]:
        conn.execute(
            'INSERT OR REPLACE INTO codebase_meta (codebase, file_path, content_hash, indexed_at) '
            'VALUES (?, ?, ?, ?)',
            (codebase, fp, f'hash_{fp}', '2025-01-01T00:00:00'),
        )

    conn.commit()


class TestIdentifyHighValueNodes(unittest.TestCase):
    """Test identify_high_value_nodes() — selection criteria."""

    def setUp(self):
        self.conn = create_test_db()
        _populate_high_value_graph(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_nodes_with_many_edges_selected(self):
        sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
        from importlib import import_module
        codebase_index = import_module('codebase-index')
        identify_high_value_nodes = codebase_index.identify_high_value_nodes

        candidates = identify_high_value_nodes(self.conn, 'test-repo', min_incoming_edges=5)
        high_value_files = [c['file_path'] for c in candidates]
        self.assertIn('src/core/Service.java', high_value_files)

    def test_entry_points_selected(self):
        sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
        from importlib import import_module
        codebase_index = import_module('codebase-index')
        identify_high_value_nodes = codebase_index.identify_high_value_nodes

        candidates = identify_high_value_nodes(self.conn, 'test-repo', min_incoming_edges=5)
        files = [c['file_path'] for c in candidates]
        self.assertIn('src/main/Application.java', files)

    def test_no_duplicates(self):
        sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
        from importlib import import_module
        codebase_index = import_module('codebase-index')
        identify_high_value_nodes = codebase_index.identify_high_value_nodes

        candidates = identify_high_value_nodes(self.conn, 'test-repo', min_incoming_edges=5)
        ids = [c['id'] for c in candidates]
        self.assertEqual(len(ids), len(set(ids)))


class TestLabelCaching(unittest.TestCase):
    """Test label_nodes_batch() — caching, skipping, and re-labeling."""

    def setUp(self):
        self.conn = create_test_db()
        _populate_high_value_graph(self.conn)

    def tearDown(self):
        self.conn.close()

    def _mock_openai_client(self, label_text='Handles core business logic'):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = label_text
        mock_client.chat.completions.create.return_value = mock_response
        return mock_client

    def test_same_hash_skipped(self):
        """Already-labeled with same content hash should be skipped."""
        sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
        from importlib import import_module
        codebase_index = import_module('codebase-index')
        label_nodes_batch = codebase_index.label_nodes_batch

        # Pre-label the symbol with matching content hash
        meta_json = json.dumps({'label': 'Old label', 'content_hash': 'hash_src/core/Service.java'})
        self.conn.execute(
            "UPDATE symbols SET metadata = ? WHERE id = ?",
            (meta_json, 'test-repo:src/core/Service.java:Service'),
        )
        self.conn.commit()

        candidates = [{
            'id': 'test-repo:src/core/Service.java:Service',
            'file_path': 'src/core/Service.java',
            'name': 'Service',
            'kind': 'class',
            'start_line': 1,
            'end_line': 100,
            'metadata': meta_json,
        }]

        mock_client = self._mock_openai_client()
        with patch('openai.OpenAI', return_value=mock_client):
            result = label_nodes_batch(self.conn, 'test-repo', candidates, 'fake-key', delay_ms=0)

        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['labeled'], 0)

    def test_different_hash_relabeled(self):
        """Changed content hash should trigger re-labeling."""
        sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
        from importlib import import_module
        codebase_index = import_module('codebase-index')
        label_nodes_batch = codebase_index.label_nodes_batch

        # Pre-label with a DIFFERENT content hash
        meta_json = json.dumps({'label': 'Old label', 'content_hash': 'stale_hash'})
        self.conn.execute(
            "UPDATE symbols SET metadata = ? WHERE id = ?",
            (meta_json, 'test-repo:src/core/Service.java:Service'),
        )
        self.conn.commit()

        candidates = [{
            'id': 'test-repo:src/core/Service.java:Service',
            'file_path': 'src/core/Service.java',
            'name': 'Service',
            'kind': 'class',
            'start_line': 1,
            'end_line': 100,
            'metadata': meta_json,
        }]

        mock_client = self._mock_openai_client('Updated label')
        with patch('openai.OpenAI', return_value=mock_client):
            result = label_nodes_batch(self.conn, 'test-repo', candidates, 'fake-key', delay_ms=0)

        self.assertEqual(result['labeled'], 1)
        self.assertEqual(result['skipped'], 0)

    def test_no_metadata_gets_labeled(self):
        """Symbol with no metadata should be labeled."""
        sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
        from importlib import import_module
        codebase_index = import_module('codebase-index')
        label_nodes_batch = codebase_index.label_nodes_batch

        candidates = [{
            'id': 'test-repo:src/core/Service.java:Service',
            'file_path': 'src/core/Service.java',
            'name': 'Service',
            'kind': 'class',
            'start_line': 1,
            'end_line': 100,
            'metadata': None,
        }]

        mock_client = self._mock_openai_client('New label')
        with patch('openai.OpenAI', return_value=mock_client):
            result = label_nodes_batch(self.conn, 'test-repo', candidates, 'fake-key', delay_ms=0)

        self.assertEqual(result['labeled'], 1)


class TestLabelSurfacing(unittest.TestCase):
    """Test that labels are accessible via SQL queries on symbols table."""

    def test_label_present_in_metadata(self):
        conn = create_test_db()
        meta_json = json.dumps({'label': 'Core business logic handler', 'content_hash': 'abc123'})
        now_ts = int(time.time() * 1000)
        conn.execute(
            'INSERT OR REPLACE INTO symbols (id, codebase, file_path, name, kind, start_line, end_line, metadata, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            ('test:foo:Bar', 'test', 'foo.java', 'Bar', 'class', 1, 10, meta_json, now_ts),
        )
        conn.commit()

        row = conn.execute("SELECT metadata FROM symbols WHERE id = 'test:foo:Bar'").fetchone()
        meta = json.loads(row['metadata'])
        self.assertEqual(meta['label'], 'Core business logic handler')
        conn.close()

    def test_label_absent_when_no_metadata(self):
        conn = create_test_db()
        now_ts = int(time.time() * 1000)
        conn.execute(
            'INSERT OR REPLACE INTO symbols (id, codebase, file_path, name, kind, start_line, end_line, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            ('test:foo:Baz', 'test', 'foo.java', 'Baz', 'class', 1, 10, now_ts),
        )
        conn.commit()

        row = conn.execute("SELECT metadata FROM symbols WHERE id = 'test:foo:Baz'").fetchone()
        self.assertIsNone(row['metadata'])
        conn.close()


if __name__ == '__main__':
    unittest.main()
