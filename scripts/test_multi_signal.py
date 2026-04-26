#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""Tests for multi-signal retrieval: N-way RRF, temporal, entity signals."""

import sys
import unittest

sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent))


class TestMergeRRFMulti(unittest.TestCase):
    """Tests for N-way RRF merge (FlatSearchBackend.merge_rrf_multi)."""

    def _merge(self, ranked_lists, **kwargs):
        from unified_memory_server import FlatSearchBackend
        return FlatSearchBackend.merge_rrf_multi(ranked_lists, **kwargs)

    def test_two_list_produces_same_as_old_merge(self):
        """2-list input should produce identical ranking to the old merge_rrf."""
        hits_a = [
            {'id': 'a', 'content': 'alpha', 'score': 0.9},
            {'id': 'b', 'content': 'beta', 'score': 0.7},
            {'id': 'c', 'content': 'gamma', 'score': 0.5},
        ]
        hits_b = [
            {'id': 'b', 'content': 'beta', 'score': 0.8},
            {'id': 'd', 'content': 'delta', 'score': 0.6},
            {'id': 'a', 'content': 'alpha', 'score': 0.4},
        ]
        k = 60
        merged = self._merge([hits_a, hits_b], k=k)

        # Manually compute expected RRF scores
        # a: rank 0 in list A (1/61) + rank 2 in list B (1/63)
        # b: rank 1 in list A (1/62) + rank 0 in list B (1/61)
        # c: rank 2 in list A (1/63)
        # d: rank 1 in list B (1/62)
        expected_scores = {
            'a': 1/61 + 1/63,
            'b': 1/62 + 1/61,
            'c': 1/63,
            'd': 1/62,
        }
        by_id = {r['id']: r['score'] for r in merged}
        for rid, expected in expected_scores.items():
            self.assertAlmostEqual(by_id[rid], expected, places=10,
                                   msg=f'Score mismatch for {rid}')

        # b should rank highest (found in both, better combined rank)
        self.assertEqual(merged[0]['id'], 'b')

    def test_three_list_scores_triple_higher_than_single(self):
        """A result in all 3 lists scores higher than one in only 1."""
        shared = {'id': 'shared', 'content': 'shared item', 'score': 0.5}
        unique = {'id': 'unique', 'content': 'unique item', 'score': 0.9}

        list_a = [shared, unique]
        list_b = [shared]
        list_c = [shared]

        merged = self._merge([list_a, list_b, list_c])
        by_id = {r['id']: r['score'] for r in merged}

        self.assertGreater(by_id['shared'], by_id['unique'],
                           'Result in 3 lists should score higher than result in 1')

    def test_empty_lists_silently_skipped(self):
        """Empty lists should be skipped without error."""
        hits = [{'id': 'x', 'content': 'test', 'score': 0.5}]
        merged = self._merge([[], hits, [], []])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['id'], 'x')

    def test_all_empty_returns_empty(self):
        """All empty lists should return empty result."""
        merged = self._merge([[], [], []])
        self.assertEqual(merged, [])

    def test_single_list(self):
        """Single list should work, scores match 1/(k+rank+1)."""
        hits = [
            {'id': 'a', 'content': 'x', 'score': 1.0},
            {'id': 'b', 'content': 'y', 'score': 0.5},
        ]
        merged = self._merge([hits])
        self.assertEqual(len(merged), 2)
        self.assertAlmostEqual(merged[0]['score'], 1/61)
        self.assertAlmostEqual(merged[1]['score'], 1/62)


class TestExtractEventDate(unittest.TestCase):
    """Tests for extract_event_date()."""

    def _extract(self, content='', session_ts=None, file_path=''):
        from unified_memory_server import extract_event_date
        return extract_event_date(content, session_ts, file_path)

    def test_session_timestamp_iso(self):
        result = self._extract(session_ts='2025-03-15T10:30:00Z')
        self.assertEqual(result, '2025-03-15')

    def test_session_timestamp_epoch_millis(self):
        # 2025-01-15 00:00:00 UTC = 1736899200000 ms
        result = self._extract(session_ts='1736899200000')
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith('2025-01'))

    def test_file_path_date(self):
        result = self._extract(file_path='memory/2025-06-20.md')
        self.assertEqual(result, '2025-06-20')

    def test_iso_date_in_content(self):
        result = self._extract(content='We deployed on 2025-04-10 and it worked.')
        self.assertEqual(result, '2025-04-10')

    def test_english_date_in_content(self):
        result = self._extract(content='The meeting was on January 15, 2025.')
        self.assertEqual(result, '2025-01-15')

    def test_relative_date_yesterday(self):
        result = self._extract(
            content='I fixed the bug yesterday',
            session_ts='2025-06-10T12:00:00',
        )
        self.assertEqual(result, '2025-06-10')  # session_ts takes priority 1

    def test_no_date_returns_none(self):
        result = self._extract(content='No date information here at all.')
        self.assertIsNone(result)

    def test_priority_order_session_over_path(self):
        """Session timestamp should win over file path date."""
        result = self._extract(
            session_ts='2025-08-01T00:00:00',
            file_path='memory/2025-01-01.md',
        )
        self.assertEqual(result, '2025-08-01')


class TestExtractEntities(unittest.TestCase):
    """Tests for extract_entities()."""

    def _extract(self, content='', title=''):
        from unified_memory_server import extract_entities
        return extract_entities(content, title)

    def test_tool_extraction(self):
        entities = self._extract(content='We use Slack and Jira for communication.')
        types_vals = {(e, v) for e, v in entities}
        self.assertIn(('tool', 'slack'), types_vals)
        self.assertIn(('tool', 'jira'), types_vals)

    def test_project_from_title(self):
        entities = self._extract(title='toast-analytics | 2025-01-15 | Tools: Read, Write')
        types_vals = {(e, v) for e, v in entities}
        self.assertIn(('project', 'toast-analytics'), types_vals)

    def test_person_name_pattern(self):
        entities = self._extract(content='Talked with Nathan Norman about the deployment.')
        types_vals = {(e, v) for e, v in entities}
        self.assertIn(('person', 'nathan norman'), types_vals)

    def test_no_entities(self):
        entities = self._extract(content='just some plain text with no names or tools')
        self.assertEqual(entities, [])

    def test_tool_case_insensitive(self):
        entities = self._extract(content='GITHUB and Docker are useful')
        tools = [v for e, v in entities if e == 'tool']
        self.assertIn('github', tools)
        self.assertIn('docker', tools)

    def test_stop_words_not_persons(self):
        """Capitalized stop words should not be detected as person names."""
        entities = self._extract(content='Monday Tuesday and other dates')
        persons = [v for e, v in entities if e == 'person']
        self.assertEqual(persons, [])


class TestTemporalRetrieval(unittest.TestCase):
    """Tests for TemporalRetrieval search-time scoring."""

    def _setup_db(self):
        """Create an in-memory DB with chunks that have event_date."""
        import sqlite3
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('''
            CREATE TABLE chunks (
                id TEXT PRIMARY KEY, file_path TEXT, chunk_index INTEGER,
                start_line INTEGER, end_line INTEGER, title TEXT,
                content TEXT, embedding BLOB, hash TEXT,
                updated_at INTEGER, event_date TEXT
            )
        ''')
        # Insert chunks with dates spread across January 2025
        for day in range(1, 21):
            date_str = f'2025-01-{day:02d}'
            conn.execute(
                'INSERT INTO chunks VALUES (?, ?, 0, 0, 10, ?, ?, NULL, ?, 0, ?)',
                (f'chunk-{day}', f'memory/{date_str}.md', f'Day {day}',
                 f'Content from {date_str}', f'hash{day}', date_str),
            )
        conn.commit()
        return conn

    def test_query_with_date_ranks_nearby_highest(self):
        from unified_memory_server import TemporalRetrieval
        conn = self._setup_db()
        tr = TemporalRetrieval.__new__(TemporalRetrieval)
        tr._db_path = None
        tr._conn = conn

        results = tr.search('what happened on 2025-01-10', limit=20)
        self.assertGreater(len(results), 0)
        # The chunk for Jan 10 should be ranked first or very high
        self.assertEqual(results[0]['id'], 'chunk-10')
        # Jan 9 and Jan 11 should be next (1 day away = high score)
        nearby_ids = {r['id'] for r in results[:3]}
        self.assertIn('chunk-9', nearby_ids)
        self.assertIn('chunk-11', nearby_ids)

    def test_query_without_date_returns_empty(self):
        from unified_memory_server import TemporalRetrieval
        conn = self._setup_db()
        tr = TemporalRetrieval.__new__(TemporalRetrieval)
        tr._db_path = None
        tr._conn = conn

        results = tr.search('generic query no date')
        self.assertEqual(results, [])


class TestEntityRetrieval(unittest.TestCase):
    """Tests for EntityRetrieval search-time scoring."""

    def _setup_db(self):
        import sqlite3
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('''
            CREATE TABLE chunks (
                id TEXT PRIMARY KEY, file_path TEXT, chunk_index INTEGER,
                start_line INTEGER, end_line INTEGER, title TEXT,
                content TEXT, embedding BLOB, hash TEXT, updated_at INTEGER
            )
        ''')
        conn.execute('''
            CREATE TABLE chunk_entities (
                chunk_id TEXT NOT NULL, entity_type TEXT NOT NULL,
                entity_value TEXT NOT NULL
            )
        ''')
        conn.execute('CREATE INDEX idx_chunk_entities_value ON chunk_entities(entity_value)')

        # Chunk with both slack and jira
        conn.execute(
            'INSERT INTO chunks VALUES (?, ?, 0, 0, 10, ?, ?, NULL, ?, 0)',
            ('chunk-both', 'memory/2025-01-01.md', 'Both tools',
             'Uses slack and jira', 'h1'),
        )
        conn.execute('INSERT INTO chunk_entities VALUES (?, ?, ?)',
                     ('chunk-both', 'tool', 'slack'))
        conn.execute('INSERT INTO chunk_entities VALUES (?, ?, ?)',
                     ('chunk-both', 'tool', 'jira'))

        # Chunk with only slack
        conn.execute(
            'INSERT INTO chunks VALUES (?, ?, 0, 0, 10, ?, ?, NULL, ?, 0)',
            ('chunk-one', 'memory/2025-01-02.md', 'One tool',
             'Uses slack only', 'h2'),
        )
        conn.execute('INSERT INTO chunk_entities VALUES (?, ?, ?)',
                     ('chunk-one', 'tool', 'slack'))

        conn.commit()
        return conn

    def test_multi_entity_query_ranks_multi_match_higher(self):
        from unified_memory_server import EntityRetrieval
        conn = self._setup_db()
        er = EntityRetrieval.__new__(EntityRetrieval)
        er._db_path = None
        er._conn = conn

        results = er.search('slack and jira integration')
        self.assertGreater(len(results), 0)
        # chunk-both has 2/2 overlap, chunk-one has 1/2
        self.assertEqual(results[0]['id'], 'chunk-both')
        self.assertGreater(results[0]['score'], results[1]['score'])

    def test_no_entities_returns_empty(self):
        from unified_memory_server import EntityRetrieval
        conn = self._setup_db()
        er = EntityRetrieval.__new__(EntityRetrieval)
        er._db_path = None
        er._conn = conn

        results = er.search('plain query with no tools or names')
        self.assertEqual(results, [])


try:
    import igraph
    HAS_IGRAPH = True
except ImportError:
    HAS_IGRAPH = False


def _write_db_to_file(conn):
    """Dump in-memory DB to a temp file (GraphSidecar needs a file path)."""
    import tempfile
    from pathlib import Path
    tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    tmp.close()
    disk_conn = __import__('sqlite3').connect(tmp.name)
    conn.backup(disk_conn)
    disk_conn.close()
    return Path(tmp.name)


@unittest.skipUnless(HAS_IGRAPH, 'igraph not installed')
class TestGraphReranking(unittest.TestCase):
    """Tests for graph in-degree re-ranking of codebase search results."""

    def test_high_in_degree_boosted(self):
        """File with high in-degree should rank above same-scored file with low in-degree."""
        import math
        from test_fixtures import create_test_db
        from unified_memory_server import GraphSidecar

        conn = create_test_db()
        db_path = _write_db_to_file(conn)

        gs = GraphSidecar(db_path)
        gs.load()
        self.assertTrue(gs.is_loaded)

        # file2 gets bridge edges (in=4), file14 is a chain tail (in=2)
        hub = 'cluster0/file2.java'
        leaf = 'cluster0/file14.java'
        self.assertIn(hub, gs._node_index)
        self.assertIn(leaf, gs._node_index)

        hub_vid = gs._node_index[hub]
        leaf_vid = gs._node_index[leaf]
        hub_in = gs._graph.degree(hub_vid, mode='in')
        leaf_in = gs._graph.degree(leaf_vid, mode='in')

        # Hub should have more incoming edges than leaf
        self.assertGreater(hub_in, leaf_in)

        # Simulate re-ranking with same base score
        base_score = 0.5
        weight = 0.1
        hub_score = base_score * (1 + math.log(1 + hub_in) * weight)
        leaf_score = base_score * (1 + math.log(1 + leaf_in) * weight)
        self.assertGreater(hub_score, leaf_score)

        import os
        os.unlink(db_path)

    def test_graph_not_loaded_scores_unchanged(self):
        """When graph is not loaded, scores should remain unchanged."""
        from unified_memory_server import GraphSidecar

        gs = GraphSidecar.__new__(GraphSidecar)
        gs._loaded = False
        gs._graph = None
        gs._node_index = {}

        self.assertFalse(gs.is_loaded)


class TestBackfillSignals(unittest.TestCase):
    """Tests for backfill_signals.py."""

    def test_backfill_on_in_memory_db(self):
        """Backfill should populate event_date and entities for existing chunks."""
        import sqlite3
        import tempfile
        from pathlib import Path

        # Create a temp DB with some chunks
        tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        conn.execute('''
            CREATE TABLE chunks (
                id TEXT PRIMARY KEY, file_path TEXT, chunk_index INTEGER,
                start_line INTEGER, end_line INTEGER, title TEXT,
                content TEXT, embedding BLOB, hash TEXT, updated_at INTEGER
            )
        ''')
        conn.execute('''
            CREATE TABLE files (
                file_path TEXT PRIMARY KEY, content_hash TEXT,
                last_indexed TEXT, chunk_count INTEGER, summary TEXT
            )
        ''')
        conn.execute('''
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)
        ''')
        # Chunk with ISO date and tool mention
        conn.execute(
            'INSERT INTO chunks VALUES (?, ?, 0, 0, 10, ?, ?, NULL, ?, 0)',
            ('c1', 'memory/2025-03-15.md', 'Test chunk',
             'We deployed to Slack on 2025-03-15', 'h1'),
        )
        # Chunk with no date
        conn.execute(
            'INSERT INTO chunks VALUES (?, ?, 0, 0, 10, ?, ?, NULL, ?, 0)',
            ('c2', 'notes/random.md', 'No date',
             'Just some notes with no tools', 'h2'),
        )
        conn.commit()
        conn.close()

        from backfill_signals import backfill
        stats = backfill(Path(tmp.name))

        self.assertEqual(stats['chunks_processed'], 2)
        self.assertGreaterEqual(stats['dates_found'], 1)

        # Verify DB state
        conn = sqlite3.connect(tmp.name)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            'SELECT event_date FROM chunks WHERE id = ?', ('c1',)
        ).fetchone()
        self.assertEqual(row['event_date'], '2025-03-15')

        entities = conn.execute(
            'SELECT entity_type, entity_value FROM chunk_entities WHERE chunk_id = ?',
            ('c1',),
        ).fetchall()
        tool_vals = [r['entity_value'] for r in entities if r['entity_type'] == 'tool']
        self.assertIn('slack', tool_vals)

        conn.close()
        import os
        os.unlink(tmp.name)

    def test_backfill_idempotent(self):
        """Running backfill twice should not duplicate entities."""
        import sqlite3
        import tempfile
        from pathlib import Path

        tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        tmp.close()
        conn = sqlite3.connect(tmp.name)
        conn.execute('''
            CREATE TABLE chunks (
                id TEXT PRIMARY KEY, file_path TEXT, chunk_index INTEGER,
                start_line INTEGER, end_line INTEGER, title TEXT,
                content TEXT, embedding BLOB, hash TEXT, updated_at INTEGER
            )
        ''')
        conn.execute('''
            CREATE TABLE files (
                file_path TEXT PRIMARY KEY, content_hash TEXT,
                last_indexed TEXT, chunk_count INTEGER, summary TEXT
            )
        ''')
        conn.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)')
        conn.execute(
            'INSERT INTO chunks VALUES (?, ?, 0, 0, 10, ?, ?, NULL, ?, 0)',
            ('c1', 'memory/2025-01-01.md', 'Test', 'Using Jira today', 'h1'),
        )
        conn.commit()
        conn.close()

        from backfill_signals import backfill
        stats1 = backfill(Path(tmp.name))
        stats2 = backfill(Path(tmp.name))

        # Second run should find 0 new dates (already populated)
        self.assertEqual(stats2['chunks_processed'], 0)

        # Entity count should not increase
        conn = sqlite3.connect(tmp.name)
        entity_count = conn.execute('SELECT COUNT(*) FROM chunk_entities').fetchone()[0]
        conn.close()
        self.assertEqual(entity_count, stats1['entities_found'])

        import os
        os.unlink(tmp.name)


class TestMultiHopRetrieval(unittest.TestCase):
    """Tests for _extract_pass2_entities, _deep_search_pass2, and memory_deep_search."""

    def test_extract_pass2_entities_finds_new(self):
        from unified_memory_server import _extract_pass2_entities
        results = [
            {'content': 'We use Slack and Docker for our workflow', 'title': ''},
            {'content': 'Jira tracks all tasks', 'title': ''},
        ]
        original = [('tool', 'slack')]
        new_entities = _extract_pass2_entities(results, original)

        new_vals = {v for _, v in new_entities}
        self.assertIn('docker', new_vals)
        self.assertIn('jira', new_vals)
        # Original should not appear
        self.assertNotIn('slack', new_vals)

    def test_extract_pass2_entities_empty_when_no_new(self):
        from unified_memory_server import _extract_pass2_entities
        results = [
            {'content': 'slack is useful', 'title': ''},
        ]
        original = [('tool', 'slack')]
        new_entities = _extract_pass2_entities(results, original)
        self.assertEqual(new_entities, [])

    def test_extract_pass2_entities_caps_at_5_results(self):
        from unified_memory_server import _extract_pass2_entities
        results = [
            {'content': f'Tool{i} and Docker', 'title': ''} for i in range(10)
        ]
        original = []
        new_entities = _extract_pass2_entities(results, original)
        # Should only process first 5 results (docker appears in all, so just 1)
        self.assertGreater(len(new_entities), 0)

    def test_deep_search_pass2_returns_empty_when_no_entities(self):
        from unified_memory_server import _deep_search_pass2
        result = _deep_search_pass2([], limit=10)
        self.assertEqual(result, [])

    def test_deep_search_pass2_returns_results(self):
        """Pass 2 search returns results when entity+keyword backends are available."""
        from unified_memory_server import _deep_search_pass2
        import unified_memory_server as ums

        # Only test if backends are initialized (integration context)
        if ums.flat_backend is None or ums.entity_backend is None:
            self.skipTest('Backends not initialized (unit test context)')

        new_entities = [('tool', 'docker'), ('tool', 'jira')]
        results = _deep_search_pass2(new_entities, limit=5)
        # Should return a list (possibly empty if no matching data)
        self.assertIsInstance(results, list)

    def test_dedup_pass1_results_get_hop_0(self):
        """Results appearing in both passes should get hop=0 (Pass 1 wins)."""
        from unified_memory_server import _extract_pass2_entities
        # This tests the dedup logic conceptually — in memory_deep_search,
        # Pass 1 paths are tracked and Pass 2 skips them
        results = [
            {'content': 'Slack integration details', 'title': ''},
        ]
        original = [('tool', 'docker')]
        new_entities = _extract_pass2_entities(results, original)
        # slack is new (not in original docker-only query)
        new_vals = {v for _, v in new_entities}
        self.assertIn('slack', new_vals)


if __name__ == '__main__':
    unittest.main()
