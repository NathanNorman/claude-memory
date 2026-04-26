#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""Backfill entity_relationships table from existing chunk_entities.

Reads chunk_entities grouped by chunk_id, generates pairwise co-occurrence
rows with canonical ordering (entity_a < entity_b), and batch-inserts them.

Idempotent: clears entity_relationships table before re-populating.

Usage:
    source ~/.claude-memory/graphiti-venv/bin/activate
    python3 scripts/backfill-entity-relationships.py [--db PATH]
"""

import argparse
import sqlite3
import sys
import time
from pathlib import Path

DEFAULT_DB = Path.home() / '.claude-memory' / 'index' / 'memory.db'


def backfill_entity_relationships(db_path: Path) -> dict:
    """Backfill entity_relationships from chunk_entities.

    Returns stats dict with chunks_processed, relationships_created.
    """
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.execute('PRAGMA busy_timeout = 5000')
    conn.execute('PRAGMA journal_mode = WAL')
    conn.row_factory = sqlite3.Row

    # Ensure table exists
    conn.execute('''
        CREATE TABLE IF NOT EXISTS entity_relationships (
            entity_a TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            entity_b TEXT NOT NULL,
            chunk_id TEXT NOT NULL,
            confidence REAL DEFAULT 1.0
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_entity_rel_a ON entity_relationships(entity_a)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_entity_rel_b ON entity_relationships(entity_b)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_entity_rel_chunk ON entity_relationships(chunk_id)')

    # Clear existing relationships for idempotent re-run
    conn.execute('DELETE FROM entity_relationships')
    conn.commit()

    # Get distinct chunk_ids that have entities
    chunk_ids = conn.execute(
        'SELECT DISTINCT chunk_id FROM chunk_entities'
    ).fetchall()

    total_chunks = len(chunk_ids)
    total_rels = 0
    batch_size = 500
    batch = []

    t0 = time.time()

    for i, row in enumerate(chunk_ids):
        cid = row['chunk_id']

        # Get entity values for this chunk
        entities = conn.execute(
            'SELECT DISTINCT entity_value FROM chunk_entities WHERE chunk_id = ?',
            (cid,),
        ).fetchall()

        values = sorted([e['entity_value'] for e in entities])

        if len(values) < 2:
            continue

        # Generate canonical pairs
        for a_idx in range(len(values)):
            for b_idx in range(a_idx + 1, len(values)):
                batch.append((values[a_idx], 'co_occurrence', values[b_idx], cid))

        # Flush batch
        if len(batch) >= batch_size:
            conn.executemany(
                'INSERT INTO entity_relationships '
                '(entity_a, relation_type, entity_b, chunk_id) '
                'VALUES (?, ?, ?, ?)',
                batch,
            )
            total_rels += len(batch)
            batch = []
            conn.commit()

        if (i + 1) % 10000 == 0:
            elapsed = time.time() - t0
            print(
                f'  Progress: {i + 1}/{total_chunks} chunks, '
                f'{total_rels} relationships ({elapsed:.1f}s)',
                file=sys.stderr,
            )

    # Flush remaining
    if batch:
        conn.executemany(
            'INSERT INTO entity_relationships '
            '(entity_a, relation_type, entity_b, chunk_id) '
            'VALUES (?, ?, ?, ?)',
            batch,
        )
        total_rels += len(batch)
        conn.commit()

    elapsed = time.time() - t0
    conn.execute('ANALYZE entity_relationships')
    conn.commit()
    conn.close()

    return {
        'chunks_processed': total_chunks,
        'relationships_created': total_rels,
        'elapsed_seconds': round(elapsed, 2),
    }


def main():
    parser = argparse.ArgumentParser(description='Backfill entity relationships')
    parser.add_argument('--db', type=Path, default=DEFAULT_DB, help='Database path')
    args = parser.parse_args()

    if not args.db.exists():
        print(f'Database not found: {args.db}', file=sys.stderr)
        sys.exit(1)

    print(f'Backfilling entity relationships from {args.db}...', file=sys.stderr)
    stats = backfill_entity_relationships(args.db)
    print(
        f'Done: {stats["chunks_processed"]} chunks, '
        f'{stats["relationships_created"]} relationships '
        f'in {stats["elapsed_seconds"]}s',
        file=sys.stderr,
    )


if __name__ == '__main__':
    main()
