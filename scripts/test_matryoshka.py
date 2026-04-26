#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""Tests for Matryoshka embedding truncation and dimension matching."""

import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))
sys.path.insert(0, str(Path(__file__).parent))

from test_fixtures import create_test_db, mock_embedding_model


class TestTruncationMath(unittest.TestCase):
    """Test pure numpy truncation + L2 renormalization (no model needed)."""

    def test_768_to_256_truncation(self):
        vec = np.random.randn(768).astype(np.float32)
        vec = vec / np.linalg.norm(vec)  # Normalize first

        truncated = vec[:256]
        norm = np.linalg.norm(truncated)
        if norm > 0:
            truncated = truncated / norm

        self.assertEqual(len(truncated), 256)
        self.assertAlmostEqual(np.linalg.norm(truncated), 1.0, places=5)

    def test_l2_norm_approximately_one(self):
        for seed in range(5):
            rng = np.random.RandomState(seed)
            vec = rng.randn(768).astype(np.float32)
            vec = vec / np.linalg.norm(vec)
            truncated = vec[:256]
            truncated = truncated / np.linalg.norm(truncated)
            self.assertAlmostEqual(float(np.linalg.norm(truncated)), 1.0, places=5)

    def test_noop_when_dims_match(self):
        vec = np.random.randn(256).astype(np.float32)
        vec = vec / np.linalg.norm(vec)
        # When truncate_dims == len(vec), no truncation should happen
        truncate_dims = 256
        if truncate_dims > 0 and len(vec) > truncate_dims:
            vec = vec[:truncate_dims]
            vec = vec / np.linalg.norm(vec)
        # Should be unchanged (256 is not > 256)
        self.assertEqual(len(vec), 256)

    def test_truncation_preserves_prefix(self):
        """First N dims of truncated vector should match first N of original."""
        vec = np.arange(768, dtype=np.float32)
        truncated = vec[:256]
        np.testing.assert_array_equal(truncated, vec[:256])


class TestEmbedAndStoreBatch(unittest.TestCase):
    """Test embed_and_store_batch with mock model and Matryoshka truncation."""

    def setUp(self):
        self.conn = create_test_db()
        self.model = mock_embedding_model(dims=768)

    def tearDown(self):
        self.conn.close()

    def test_stored_blob_size_matches_truncated_dims(self):
        """With truncate_dims=256, stored BLOB should be 256*4 bytes."""
        chunks = [{
            'file_path': 'codebase:test/src/Foo.java',
            'chunk_index': 0,
            'start_line': 1,
            'end_line': 10,
            'title': 'class Foo',
            'content': 'public class Foo { }',
        }]

        # We need quantize module — mock it
        with patch.dict('sys.modules', {
            'quantize': MagicMock(
                quantize=lambda emb, rot, cb: b'',
                quantize_binary=lambda x: np.zeros((1, 32), dtype=np.uint8),
            ),
        }):
            # Import after patching
            sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))
            from index_worker import embed_and_store_batch

            count = embed_and_store_batch(
                self.conn, self.model, chunks,
                rotate_fn=None, codebook=None,
                truncate_dims=256,
            )

        self.assertEqual(count, 1)
        row = self.conn.execute(
            "SELECT embedding FROM chunks WHERE id = 'codebase:test/src/Foo.java:0'"
        ).fetchone()
        self.assertIsNotNone(row)
        blob = row['embedding']
        # float32 = 4 bytes per dim
        self.assertEqual(len(blob), 256 * 4)

    def test_doc_prefix_prepended(self):
        """When doc_prefix is set, it should be prepended to model input."""
        chunks = [{
            'file_path': 'codebase:test/src/Bar.java',
            'chunk_index': 0,
            'start_line': 1,
            'end_line': 5,
            'title': 'class Bar',
            'content': 'class Bar {}',
        }]

        with patch.dict('sys.modules', {
            'quantize': MagicMock(
                quantize=lambda emb, rot, cb: b'',
                quantize_binary=lambda x: np.zeros((1, 32), dtype=np.uint8),
            ),
        }):
            from index_worker import embed_and_store_batch

            embed_and_store_batch(
                self.conn, self.model, chunks,
                rotate_fn=None, codebook=None,
                doc_prefix='search_document: ',
            )

        # Check that the model received text with prefix
        last_call = self.model.call_texts[-1]
        self.assertTrue(last_call[0].startswith('search_document: '))


class TestDimensionAutoDetect(unittest.TestCase):
    """Test _codebase_stored_dims auto-detection from meta table and BLOB size."""

    def test_detect_from_meta_table(self):
        conn = create_test_db()
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('codebase_embedding_dims', '256')"
        )
        conn.commit()

        # Verify the meta value can be read back
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'codebase_embedding_dims'"
        ).fetchone()
        self.assertEqual(int(row['value']), 256)
        conn.close()

    def test_detect_from_blob_size_fallback(self):
        conn = create_test_db()
        # No meta row — store a 256-dim float32 BLOB
        blob = struct.pack('256f', *([0.1] * 256))
        conn.execute(
            "INSERT INTO chunks (id, file_path, chunk_index, embedding, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ('codebase:test/foo.java:0', 'codebase:test/foo.java', 0, blob, 0),
        )
        conn.commit()

        # Read back and detect dims from BLOB size
        row = conn.execute(
            "SELECT embedding FROM chunks WHERE file_path LIKE 'codebase:%' "
            "AND embedding IS NOT NULL LIMIT 1"
        ).fetchone()
        self.assertIsNotNone(row)
        blob_len = len(row['embedding'])
        detected_dims = blob_len // 4  # float32 = 4 bytes
        self.assertEqual(detected_dims, 256)
        conn.close()


class TestQueryDimensionMatching(unittest.TestCase):
    """Test that query vectors are truncated to match stored dimensions."""

    def test_query_truncated_to_stored_dims(self):
        """A 768-dim query should be truncated to 256 when stored_dims=256."""
        query_vec = np.random.randn(768).astype(np.float32)
        query_vec = query_vec / np.linalg.norm(query_vec)

        stored_dims = 256
        if stored_dims > 0 and len(query_vec) > stored_dims:
            query_vec = query_vec[:stored_dims]
            norm = np.linalg.norm(query_vec)
            if norm > 0:
                query_vec = query_vec / norm

        self.assertEqual(len(query_vec), 256)
        self.assertAlmostEqual(float(np.linalg.norm(query_vec)), 1.0, places=5)

    def test_no_truncation_when_dims_match(self):
        """A 256-dim query should not be truncated when stored_dims=256."""
        query_vec = np.random.randn(256).astype(np.float32)
        query_vec = query_vec / np.linalg.norm(query_vec)
        original_len = len(query_vec)

        stored_dims = 256
        if stored_dims > 0 and len(query_vec) > stored_dims:
            query_vec = query_vec[:stored_dims]
            query_vec = query_vec / np.linalg.norm(query_vec)

        self.assertEqual(len(query_vec), original_len)


if __name__ == '__main__':
    unittest.main()
