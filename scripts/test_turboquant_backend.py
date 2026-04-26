#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""Tests for TurboQuantBackend sidecar integration."""

import json
import sqlite3
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

import numpy as np
from quantize import (
    generate_rotation, compute_codebook, quantize, packed_size,
    quantize_binary,
)


def create_test_sidecar(tmpdir: Path, dims: int = 384, n_vectors: int = 50, seed: int = 42):
    """Create a test DB and sidecar files with synthetic embeddings."""
    bit_width = 4

    # Generate random normalized vectors
    rng = np.random.RandomState(seed)
    vectors = rng.randn(n_vectors, dims).astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vectors = vectors / norms

    # Quantization params
    fwd, inv = generate_rotation(dims, seed)
    codebook = compute_codebook(dims, bit_width)

    # Quantize vectors
    packed_vectors = [quantize(v, fwd, codebook) for v in vectors]
    vec_size = packed_size(dims, bit_width)

    # Write sidecar files
    packed_path = tmpdir / 'packed_vectors.bin'
    with open(packed_path, 'wb') as f:
        for packed in packed_vectors:
            f.write(packed)

    rerank_path = tmpdir / 'rerank_matrix.f32'
    vectors.tofile(str(rerank_path))

    rowids = list(range(1, n_vectors + 1))
    meta = {
        'dims': dims,
        'bit_width': bit_width,
        'rotation_seed': seed,
        'model_name': 'test-model',
        'codebook': codebook.tolist(),
        'rowid_map': rowids,
        'vector_count': n_vectors,
        'packed_vector_size': vec_size,
    }
    meta_path = tmpdir / 'quantization.json'
    with open(meta_path, 'w') as f:
        json.dump(meta, f)

    # Create a test DB with matching chunks
    db_path = tmpdir / 'memory.db'
    conn = sqlite3.connect(str(db_path))
    conn.execute('''
        CREATE TABLE chunks (
            id TEXT PRIMARY KEY, file_path TEXT, chunk_index INTEGER,
            start_line INTEGER, end_line INTEGER, title TEXT,
            content TEXT, embedding BLOB, hash TEXT, updated_at INTEGER
        )
    ''')
    for i in range(n_vectors):
        emb_blob = struct.pack(f'{dims}f', *vectors[i].tolist())
        conn.execute(
            'INSERT INTO chunks VALUES (?, ?, 0, 0, 10, ?, ?, ?, ?, 0)',
            (f'chunk-{i}', f'memory/test-{i}.md', f'Test {i}',
             f'Test content {i} with tools like Slack and Docker',
             emb_blob, f'hash{i}'),
        )
    conn.commit()
    conn.close()

    return db_path, vectors, rowids


class TestTurboQuantBackendLoad(unittest.TestCase):
    """Tests for TurboQuantBackend sidecar file loading."""

    def test_loads_sidecar_files(self):
        from unified_memory_server import TurboQuantBackend
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            db_path, vectors, rowids = create_test_sidecar(tmpdir)

            backend = TurboQuantBackend(tmpdir, db_path)
            self.assertTrue(backend.load())
            self.assertTrue(backend.is_loaded)

            stats = backend.get_stats()
            self.assertEqual(stats['status'], 'ok')
            self.assertEqual(stats['vectors'], 50)
            self.assertEqual(stats['dims'], 384)
            self.assertEqual(stats['bit_width'], 4)

    def test_fallback_when_files_missing(self):
        from unified_memory_server import TurboQuantBackend
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            db_path = tmpdir / 'memory.db'
            conn = sqlite3.connect(str(db_path))
            conn.execute('CREATE TABLE chunks (id TEXT)')
            conn.close()

            backend = TurboQuantBackend(tmpdir, db_path)
            self.assertFalse(backend.load())
            self.assertFalse(backend.is_loaded)

    def test_search_returns_empty_when_not_loaded(self):
        from unified_memory_server import TurboQuantBackend
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            db_path = tmpdir / 'memory.db'
            conn = sqlite3.connect(str(db_path))
            conn.execute('CREATE TABLE chunks (id TEXT)')
            conn.close()

            backend = TurboQuantBackend(tmpdir, db_path)
            results = backend.search('test query', 10)
            self.assertEqual(results, [])

    def test_vector_count_mismatch_rejects(self):
        """Sidecar files with mismatched vector count should fail to load."""
        from unified_memory_server import TurboQuantBackend
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            db_path, vectors, rowids = create_test_sidecar(tmpdir, n_vectors=50)

            # Corrupt: remove half the packed vectors
            packed_path = tmpdir / 'packed_vectors.bin'
            data = packed_path.read_bytes()
            with open(packed_path, 'wb') as f:
                f.write(data[:len(data) // 2])

            backend = TurboQuantBackend(tmpdir, db_path)
            self.assertFalse(backend.load())

    def test_get_stats_when_unavailable(self):
        from unified_memory_server import TurboQuantBackend
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            backend = TurboQuantBackend(tmpdir, tmpdir / 'nope.db')
            stats = backend.get_stats()
            self.assertEqual(stats['status'], 'unavailable')
            self.assertEqual(stats['vectors'], 0)

    def test_close_cleans_up(self):
        from unified_memory_server import TurboQuantBackend
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            db_path, vectors, rowids = create_test_sidecar(tmpdir)

            backend = TurboQuantBackend(tmpdir, db_path)
            backend.load()
            self.assertTrue(backend.is_loaded)

            backend.close()
            self.assertFalse(backend.is_loaded)
            self.assertIsNone(backend._packed_list)
            self.assertIsNone(backend._rerank_mmap)


class TestSidecarFileWriter(unittest.TestCase):
    """Tests for write_sidecar_files in migrate_to_quantized.py."""

    def test_writes_all_files(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from migrate_to_quantized import write_sidecar_files

        dims = 384
        n_vectors = 20
        bit_width = 4

        rng = np.random.RandomState(42)
        vectors = rng.randn(n_vectors, dims).astype(np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        vectors = vectors / norms

        fwd, inv = generate_rotation(dims, 42)
        codebook = compute_codebook(dims, bit_width)
        packed = [quantize(v, fwd, codebook) for v in vectors]
        rowids = list(range(1, n_vectors + 1))

        with tempfile.TemporaryDirectory() as tmpdir:
            sidecar_dir = Path(tmpdir)
            stats = write_sidecar_files(
                sidecar_dir, rowids, packed, vectors,
                codebook, dims, bit_width, 42, 'test-model',
            )

            self.assertTrue((sidecar_dir / 'packed_vectors.bin').exists())
            self.assertTrue((sidecar_dir / 'rerank_matrix.f32').exists())
            self.assertTrue((sidecar_dir / 'quantization.json').exists())

            # Verify metadata
            with open(sidecar_dir / 'quantization.json') as f:
                meta = json.load(f)
            self.assertEqual(meta['dims'], dims)
            self.assertEqual(meta['bit_width'], bit_width)
            self.assertEqual(meta['vector_count'], n_vectors)
            self.assertEqual(len(meta['rowid_map']), n_vectors)
            self.assertEqual(len(meta['codebook']), 1 << bit_width)

            # Verify packed size
            packed_data = (sidecar_dir / 'packed_vectors.bin').read_bytes()
            expected_size = n_vectors * packed_size(dims, bit_width)
            self.assertEqual(len(packed_data), expected_size)

            # Verify rerank matrix size
            rerank_size = (sidecar_dir / 'rerank_matrix.f32').stat().st_size
            self.assertEqual(rerank_size, n_vectors * dims * 4)


if __name__ == '__main__':
    unittest.main()
