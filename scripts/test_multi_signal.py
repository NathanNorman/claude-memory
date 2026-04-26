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


if __name__ == '__main__':
    unittest.main()
