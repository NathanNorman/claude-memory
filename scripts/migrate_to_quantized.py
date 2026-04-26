#!/usr/bin/env python3
"""
Migrate unified-memory embeddings from float32 to TurboQuant 4-bit quantized.

Reads all float32 BLOBs from chunks.embedding, generates a rotation seed
and codebook, quantizes all vectors, writes packed BLOBs back, and stores
quantization parameters in quantization_meta.

Usage:
    python3 scripts/migrate_to_quantized.py [--bit-width 4] [--seed 42] [--dry-run]
"""

import argparse
import json
import math
import os
import shutil
import sqlite3
import struct
import sys
import time
from datetime import datetime
from pathlib import Path

# Add src/ to path for quantize module
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import numpy as np
from quantize import (
    generate_rotation, compute_codebook, quantize, dequantize,
    batch_quantized_dot_products, packed_size,
)

HOME = Path.home()
DB_PATH = HOME / '.claude-memory' / 'index' / 'memory.db'
BACKUP_DIR = HOME / '.claude-memory' / 'backups'


def backup_db(db_path: Path) -> Path:
    """Create a backup of the database before migration."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = BACKUP_DIR / f'memory-pre-quantize-{ts}.db'
    # Use sqlite3 .backup for WAL-safe copy
    conn = sqlite3.connect(str(db_path))
    backup_conn = sqlite3.connect(str(backup_path))
    conn.backup(backup_conn)
    backup_conn.close()
    conn.close()
    print(f'  Backup: {backup_path} ({backup_path.stat().st_size / 1e6:.1f} MB)')
    return backup_path


def load_float32_embeddings(conn: sqlite3.Connection, dims: int) -> tuple:
    """Load all float32 embeddings from chunks table."""
    float32_size = dims * 4
    rows = conn.execute(
        'SELECT rowid, embedding FROM chunks WHERE embedding IS NOT NULL'
    ).fetchall()

    rowids = []
    vectors = []
    skipped = 0
    for row in rows:
        blob = row[1]
        if not blob or len(blob) != float32_size:
            skipped += 1
            continue
        vec = np.array(struct.unpack(f'{dims}f', blob), dtype=np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        rowids.append(row[0])
        vectors.append(vec)

    return rowids, np.array(vectors, dtype=np.float32), skipped


def store_quantization_params(
    conn: sqlite3.Connection,
    model_name: str,
    dims: int,
    bit_width: int,
    seed: int,
    codebook: np.ndarray,
) -> None:
    """Store rotation seed and codebook in quantization_meta table."""
    # Ensure table exists
    conn.execute('''
        CREATE TABLE IF NOT EXISTS quantization_meta (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_name TEXT NOT NULL,
            dims INTEGER NOT NULL,
            bit_width INTEGER NOT NULL,
            rotation_seed INTEGER NOT NULL,
            codebook BLOB NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(model_name, dims, bit_width)
        )
    ''')
    codebook_blob = struct.pack(f'{len(codebook)}f', *codebook.tolist())
    conn.execute(
        'INSERT OR REPLACE INTO quantization_meta '
        '(model_name, dims, bit_width, rotation_seed, codebook) '
        'VALUES (?, ?, ?, ?, ?)',
        (model_name, dims, bit_width, seed, codebook_blob),
    )


def verify_migration(
    conn: sqlite3.Connection,
    original_matrix: np.ndarray,
    rowids: list,
    rotate_fn,
    inv_rotate_fn,
    codebook: np.ndarray,
    dims: int,
    bit_width: int,
) -> bool:
    """Verify migration by comparing top-10 search results before/after."""
    quant_size = packed_size(dims, bit_width)

    # Load quantized embeddings
    packed_list = []
    deq_vectors = []
    for rowid in rowids:
        row = conn.execute(
            'SELECT embedding FROM chunks WHERE rowid = ?', (rowid,)
        ).fetchone()
        blob = row[0]
        if len(blob) != quant_size:
            print(f'  FAIL: rowid {rowid} has unexpected BLOB size {len(blob)} (expected {quant_size})')
            return False
        packed_list.append(blob)
        deq = dequantize(blob, inv_rotate_fn, codebook, dims)
        norm = np.linalg.norm(deq)
        if norm > 0:
            deq = deq / norm
        deq_vectors.append(deq)

    deq_matrix = np.array(deq_vectors, dtype=np.float32)

    # Compare top-10 results for 20 test queries
    n_test = min(20, len(rowids))
    recall_sum = 0
    for i in range(n_test):
        q = original_matrix[i]
        exact_sims = original_matrix @ q
        exact_top10 = set(np.argsort(exact_sims)[-10:])

        # Quantized search with reranking
        q_rotated = rotate_fn(q)
        approx_sims = batch_quantized_dot_products(q_rotated, packed_list, codebook, dims)
        top30 = np.argsort(approx_sims)[-30:]
        rerank_sims = deq_matrix[top30] @ q
        top10_within = top30[np.argsort(rerank_sims)[-10:]]
        reranked_top10 = set(top10_within)

        recall = len(exact_top10 & reranked_top10) / 10.0
        recall_sum += recall

    avg_recall = recall_sum / n_test
    print(f'  Verification recall@10 (with reranking): {avg_recall:.3f}')
    return avg_recall >= 0.90


def write_sidecar_files(
    sidecar_dir: Path,
    rowids: list,
    packed_vectors: list[bytes],
    vectors: np.ndarray,
    codebook: np.ndarray,
    dims: int,
    bit_width: int,
    seed: int,
    model_name: str,
) -> dict:
    """Write TurboQuantBackend sidecar files.

    Creates:
    - packed_vectors.bin: concatenated packed vectors
    - rerank_matrix.f32: float32 matrix for exact reranking
    - quantization.json: metadata (codebook, rowid_map, rotation_seed, dims)
    """
    sidecar_dir.mkdir(parents=True, exist_ok=True)

    # packed_vectors.bin: concatenated packed bytes
    packed_path = sidecar_dir / 'packed_vectors.bin'
    with open(packed_path, 'wb') as f:
        for packed in packed_vectors:
            f.write(packed)
    packed_mb = packed_path.stat().st_size / 1e6

    # rerank_matrix.f32: flat binary float32 matrix
    rerank_path = sidecar_dir / 'rerank_matrix.f32'
    vectors.tofile(str(rerank_path))
    rerank_mb = rerank_path.stat().st_size / 1e6

    # quantization.json: metadata
    meta = {
        'dims': dims,
        'bit_width': bit_width,
        'rotation_seed': seed,
        'model_name': model_name,
        'codebook': codebook.tolist(),
        'rowid_map': rowids,
        'vector_count': len(rowids),
        'packed_vector_size': len(packed_vectors[0]) if packed_vectors else 0,
    }
    meta_path = sidecar_dir / 'quantization.json'
    with open(meta_path, 'w') as f:
        json.dump(meta, f)
    meta_kb = meta_path.stat().st_size / 1024

    return {
        'packed_mb': round(packed_mb, 2),
        'rerank_mb': round(rerank_mb, 2),
        'meta_kb': round(meta_kb, 1),
    }


def main():
    parser = argparse.ArgumentParser(description='Migrate embeddings to quantized format')
    parser.add_argument('--bit-width', type=int, default=4, help='Quantization bit width (default: 4)')
    parser.add_argument('--seed', type=int, default=42, help='Rotation seed (default: 42)')
    parser.add_argument('--model', type=str, default='all-MiniLM-L6-v2', help='Model name')
    parser.add_argument('--dims', type=int, default=384, help='Embedding dimensions')
    parser.add_argument('--dry-run', action='store_true', help='Show what would happen without changing DB')
    parser.add_argument('--sidecar', action='store_true', help='Also write sidecar files for TurboQuantBackend')
    parser.add_argument('--sidecar-only', action='store_true', help='Only write sidecar files (skip DB migration)')
    parser.add_argument('--db', type=str, default=str(DB_PATH), help='Database path')
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f'ERROR: Database not found: {db_path}')
        sys.exit(1)

    print('=' * 60)
    print('  MIGRATE TO QUANTIZED EMBEDDINGS')
    print('=' * 60)
    print()
    print(f'  Database:  {db_path}')
    print(f'  Model:     {args.model}')
    print(f'  Dims:      {args.dims}')
    print(f'  Bit width: {args.bit_width}')
    print(f'  Seed:      {args.seed}')
    print(f'  Dry run:   {args.dry_run}')
    print()

    # Step 1: Backup
    print('Step 1: Backup database')
    if not args.dry_run:
        backup_path = backup_db(db_path)
    else:
        print('  (skipped in dry-run mode)')
    print()

    # Step 2: Load float32 embeddings
    print('Step 2: Load float32 embeddings')
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.execute('PRAGMA busy_timeout = 5000')
    conn.execute('PRAGMA journal_mode = WAL')

    rowids, vectors, skipped = load_float32_embeddings(conn, args.dims)
    print(f'  Loaded: {len(rowids)} vectors')
    if skipped:
        print(f'  Skipped: {skipped} (wrong size or NULL)')
    if not rowids:
        print('  ERROR: No float32 embeddings found. Nothing to migrate.')
        conn.close()
        sys.exit(1)
    print()

    # Step 3: Generate quantization params
    print('Step 3: Generate rotation + codebook')
    fwd, inv = generate_rotation(args.dims, args.seed)
    codebook = compute_codebook(args.dims, args.bit_width)
    print(f'  Codebook: {len(codebook)} centroids')
    print(f'  Packed vector size: {packed_size(args.dims, args.bit_width)} bytes '
          f'(vs {args.dims * 4} bytes float32, '
          f'{args.dims * 4 / packed_size(args.dims, args.bit_width):.1f}x compression)')
    print()

    # Step 4: Quantize all vectors
    print('Step 4: Quantize all vectors')
    start = time.time()
    packed_vectors = []
    batch_size = 1000
    for i in range(0, len(vectors), batch_size):
        batch = vectors[i:i + batch_size]
        for vec in batch:
            packed_vectors.append(quantize(vec, fwd, codebook))
        done = min(i + batch_size, len(vectors))
        elapsed = time.time() - start
        rate = done / elapsed if elapsed > 0 else 0
        print(f'  Quantized {done}/{len(vectors)} ({rate:.0f} vec/s)', end='\r')
    print(f'  Quantized {len(vectors)}/{len(vectors)} in {time.time() - start:.1f}s')
    print()

    if args.sidecar_only:
        print('SIDECAR-ONLY mode — skipping DB migration')
        sidecar_dir = db_path.parent
        print(f'Step 5: Writing sidecar files to {sidecar_dir}')
        stats = write_sidecar_files(
            sidecar_dir, rowids, packed_vectors, vectors,
            codebook, args.dims, args.bit_width, args.seed, args.model,
        )
        print(f'  packed_vectors.bin: {stats["packed_mb"]} MB')
        print(f'  rerank_matrix.f32:  {stats["rerank_mb"]} MB')
        print(f'  quantization.json:  {stats["meta_kb"]} KB')
        conn.close()
        print()
        print('  Sidecar files written. Restart MCP server to use TurboQuantBackend.')
        return

    if args.dry_run:
        print('DRY RUN — would write quantized embeddings to DB. Exiting.')
        conn.close()
        return

    # Step 5: Write quantized embeddings to DB
    print('Step 5: Write quantized embeddings + meta')
    store_quantization_params(conn, args.model, args.dims, args.bit_width, args.seed, codebook)

    for rowid, packed in zip(rowids, packed_vectors):
        conn.execute(
            'UPDATE chunks SET embedding = ? WHERE rowid = ?',
            (packed, rowid),
        )
    conn.commit()
    print(f'  Updated {len(rowids)} rows')
    print()

    # Step 6: Verify
    print('Step 6: Verify migration')
    ok = verify_migration(
        conn, vectors, rowids, fwd, inv, codebook, args.dims, args.bit_width,
    )

    conn.close()
    print()

    if ok:
        # Write sidecar files if requested
        if args.sidecar:
            sidecar_dir = db_path.parent
            print(f'Step 7: Writing sidecar files to {sidecar_dir}')
            stats = write_sidecar_files(
                sidecar_dir, rowids, packed_vectors, vectors,
                codebook, args.dims, args.bit_width, args.seed, args.model,
            )
            print(f'  packed_vectors.bin: {stats["packed_mb"]} MB')
            print(f'  rerank_matrix.f32:  {stats["rerank_mb"]} MB')
            print(f'  quantization.json:  {stats["meta_kb"]} KB')
            print()

        print('=' * 60)
        print('  MIGRATION COMPLETE')
        print('=' * 60)
        print()
        print(f'  {len(rowids)} embeddings converted to {args.bit_width}-bit quantized')
        print(f'  Storage: {len(rowids) * packed_size(args.dims, args.bit_width) / 1024:.0f} KB '
              f'(was {len(rowids) * args.dims * 4 / 1024:.0f} KB)')
        print()
        print('  MCP servers will pick up quantized embeddings on next query reload.')
        if args.sidecar:
            print('  TurboQuantBackend sidecar files also written.')
        print(f'  To rollback: bash scripts/restore_pre_turboquant.sh')
    else:
        print('  VERIFICATION FAILED — consider restoring from backup')
        print(f'  Backup: {backup_path}')
        sys.exit(1)


if __name__ == '__main__':
    main()
