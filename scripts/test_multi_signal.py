#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""Tests for multi-signal retrieval: N-way RRF, temporal, entity signals."""

import sys
import unittest

sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'src'))


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


if __name__ == '__main__':
    unittest.main()
