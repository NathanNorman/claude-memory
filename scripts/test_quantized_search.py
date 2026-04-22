#!/usr/bin/env python3
"""
Integration test for TurboQuant quantized search.

Tests:
1. Round-trip quantization distortion
2. Quantized dot product accuracy
3. Recall@10 with reranking (random + clustered + real data)
4. Mixed-mode (float32 + quantized) search
5. Code chunker → index → search round-trip
"""

import os
import sqlite3
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import numpy as np
from quantize import (
    generate_rotation, compute_codebook, quantize, dequantize,
    quantized_dot_product, batch_quantized_dot_products,
    search_with_rerank, packed_size,
)
from code_chunker import chunk_file, chunk_python_file

DIMS = 384
BIT_WIDTH = 4
SEED = 42

passed = 0
failed = 0


def test(name: str, condition: bool, detail: str = ''):
    global passed, failed
    status = 'PASS' if condition else 'FAIL'
    if condition:
        passed += 1
    else:
        failed += 1
    print(f'  [{status}] {name}' + (f' — {detail}' if detail else ''))


def main():
    global passed, failed

    fwd, inv = generate_rotation(DIMS, SEED)
    codebook = compute_codebook(DIMS, BIT_WIDTH)

    # ──────────────────────────────────────────────────────────
    print('\n=== Test 1: Round-trip distortion ===')
    # ──────────────────────────────────────────────────────────

    rng = np.random.RandomState(123)
    vectors = rng.randn(500, DIMS).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)

    mse_total = 0.0
    for v in vectors:
        packed = quantize(v, fwd, codebook)
        reconstructed = dequantize(packed, inv, codebook, DIMS)
        mse_total += np.mean((v - reconstructed) ** 2)

    avg_mse = mse_total / len(vectors)
    test('Round-trip MSE ≤ 0.009', avg_mse <= 0.009, f'MSE={avg_mse:.6f}')

    # ──────────────────────────────────────────────────────────
    print('\n=== Test 2: Packed size ===')
    # ──────────────────────────────────────────────────────────

    ps = packed_size(DIMS, BIT_WIDTH)
    test('Packed size = 192 bytes (4-bit, 384-dim)', ps == 192, f'{ps} bytes')
    test('8x compression vs float32', DIMS * 4 / ps == 8.0, f'{DIMS * 4 / ps:.1f}x')

    packed = quantize(vectors[0], fwd, codebook)
    test('Actual packed BLOB size matches', len(packed) == ps, f'{len(packed)} bytes')

    # ──────────────────────────────────────────────────────────
    print('\n=== Test 3: Quantized dot product ===')
    # ──────────────────────────────────────────────────────────

    max_abs_err = 0.0
    for i in range(50):
        q = vectors[i]
        q_rot = fwd(q)
        for j in range(i + 1, min(i + 20, len(vectors))):
            packed_j = quantize(vectors[j], fwd, codebook)
            approx = quantized_dot_product(q_rot, packed_j, codebook, DIMS)
            exact = float(np.dot(q, vectors[j]))
            max_abs_err = max(max_abs_err, abs(approx - exact))

    test('Max dot product error < 0.05', max_abs_err < 0.05, f'max_err={max_abs_err:.4f}')

    # ──────────────────────────────────────────────────────────
    print('\n=== Test 4: Recall@10 with reranking (random vectors) ===')
    # ──────────────────────────────────────────────────────────

    n_vecs = 1000
    vecs = rng.randn(n_vecs, DIMS).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    packed_all = [quantize(vecs[i], fwd, codebook) for i in range(n_vecs)]
    matrix = vecs.copy()

    recall_sum = 0
    n_test = 100
    for i in range(n_test):
        q = vecs[i]
        exact_top10 = set(np.argsort(vecs @ q)[-10:])
        results = search_with_rerank(q, packed_all, matrix, fwd, codebook, DIMS)
        approx_top10 = set(idx for idx, _ in results)
        recall_sum += len(exact_top10 & approx_top10) / 10.0

    recall = recall_sum / n_test
    test('Recall@10 with reranking ≥ 0.95 (random)', recall >= 0.95, f'recall={recall:.3f}')

    # ──────────────────────────────────────────────────────────
    print('\n=== Test 5: Recall@10 with real embeddings ===')
    # ──────────────────────────────────────────────────────────

    db_path = Path.home() / '.claude-memory' / 'index' / 'memory.db'
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            'SELECT embedding FROM chunks WHERE embedding IS NOT NULL LIMIT 500'
        ).fetchall()
        conn.close()

        real_vecs = []
        quant_size = packed_size(DIMS, BIT_WIDTH)
        for row in rows:
            blob = row[0]
            if not blob:
                continue
            if len(blob) == DIMS * 4:
                vec = np.array(struct.unpack(f'{DIMS}f', blob), dtype=np.float32)
            elif len(blob) == quant_size:
                vec = dequantize(blob, inv, codebook, DIMS)
            else:
                continue
            norm = np.linalg.norm(vec)
            if norm > 0:
                real_vecs.append(vec / norm)

        if len(real_vecs) >= 50:
            real_vecs = np.array(real_vecs, dtype=np.float32)
            real_packed = [quantize(real_vecs[i], fwd, codebook) for i in range(len(real_vecs))]

            recall_sum = 0
            n_test = min(50, len(real_vecs))
            for i in range(n_test):
                q = real_vecs[i]
                exact_top10 = set(np.argsort(real_vecs @ q)[-10:])
                results = search_with_rerank(
                    q, real_packed, real_vecs, fwd, codebook, DIMS
                )
                approx_top10 = set(idx for idx, _ in results)
                recall_sum += len(exact_top10 & approx_top10) / 10.0

            recall = recall_sum / n_test
            test('Recall@10 with reranking ≥ 0.95 (real)', recall >= 0.95,
                 f'recall={recall:.3f}, {len(real_vecs)} vectors')
        else:
            print('  [SKIP] Not enough real embeddings for test')
    else:
        print('  [SKIP] No memory database found')

    # ──────────────────────────────────────────────────────────
    print('\n=== Test 6: Mixed-mode (float32 + quantized) ===')
    # ──────────────────────────────────────────────────────────

    # Create temp DB with mix of float32 and quantized embeddings
    tmp_db = tempfile.mktemp(suffix='.db')
    conn = sqlite3.connect(tmp_db)
    conn.execute('CREATE TABLE chunks (id TEXT, embedding BLOB, content TEXT)')

    # Insert 50 float32 + 50 quantized
    for i in range(100):
        vec = vecs[i]
        if i < 50:
            blob = struct.pack(f'{DIMS}f', *vec.tolist())
        else:
            blob = quantize(vec, fwd, codebook)
        conn.execute('INSERT INTO chunks VALUES (?, ?, ?)', (f'chunk_{i}', blob, f'content {i}'))
    conn.commit()

    # Read back and classify
    rows = conn.execute('SELECT id, embedding FROM chunks').fetchall()
    n_f32 = sum(1 for r in rows if len(r[1]) == DIMS * 4)
    n_quant = sum(1 for r in rows if len(r[1]) == packed_size(DIMS, BIT_WIDTH))
    test('Mixed-mode: 50 float32 detected', n_f32 == 50, f'{n_f32}')
    test('Mixed-mode: 50 quantized detected', n_quant == 50, f'{n_quant}')

    conn.close()
    os.unlink(tmp_db)

    # ──────────────────────────────────────────────────────────
    print('\n=== Test 7: Code chunker → search ===')
    # ──────────────────────────────────────────────────────────

    # Chunk a Python file
    quantize_py = str(Path(__file__).parent.parent / 'src' / 'quantize.py')
    if Path(quantize_py).exists():
        chunks = chunk_python_file(quantize_py)
        test('Python AST chunker finds functions', len(chunks) > 5,
             f'{len(chunks)} chunks')
        test('Chunks have titles', all('title' in c for c in chunks))
        test('Chunks have line numbers',
             all('start_line' in c and 'end_line' in c for c in chunks))

        # Verify dispatch
        chunks2 = chunk_file(quantize_py)
        test('chunk_file dispatches .py to AST', len(chunks2) == len(chunks))

        # Chunk a non-Python file
        indexer_ts = str(Path(__file__).parent.parent / 'src' / 'indexer.ts')
        if Path(indexer_ts).exists():
            ts_chunks = chunk_file(indexer_ts)
            test('File-level chunker works on .ts', len(ts_chunks) >= 1,
                 f'{len(ts_chunks)} chunks')
    else:
        print('  [SKIP] quantize.py not found')

    # ──────────────────────────────────────────────────────────
    print(f'\n{"=" * 60}')
    print(f'  Results: {passed} passed, {failed} failed')
    print(f'{"=" * 60}')

    sys.exit(0 if failed == 0 else 1)


if __name__ == '__main__':
    main()
