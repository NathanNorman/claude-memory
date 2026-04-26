#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""Backfill event_date and chunk_entities for existing chunks.

Processes all chunks in the production DB, extracts temporal dates and
entities, and stores results. Idempotent: skips chunks already populated.

Usage:
    source ~/.claude-memory/graphiti-venv/bin/activate
    python3 scripts/backfill_signals.py [--db PATH] [--dry-run]
"""

import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

from unified_memory_server import extract_entities, extract_event_date


DEFAULT_DB = Path.home() / '.claude-memory' / 'index' / 'memory.db'


def backfill(db_path: Path, dry_run: bool = False) -> dict:
    """Backfill event_date and entities for all chunks.

    Returns dict with processing stats.
    """
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout = 10000')

    # Ensure columns/tables exist
    try:
        conn.execute('ALTER TABLE chunks ADD COLUMN event_date TEXT')
    except sqlite3.OperationalError:
        pass
    conn.execute('CREATE INDEX IF NOT EXISTS idx_chunks_event_date ON chunks(event_date)')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS chunk_entities (
            chunk_id TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_value TEXT NOT NULL
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_chunk_entities_value ON chunk_entities(entity_value)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_chunk_entities_chunk ON chunk_entities(chunk_id)')
    conn.commit()

    # Count totals
    total = conn.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
    already_dated = conn.execute(
        'SELECT COUNT(*) FROM chunks WHERE event_date IS NOT NULL'
    ).fetchone()[0]

    # Get chunks needing date backfill
    needs_date = conn.execute(
        'SELECT id, file_path, content, title FROM chunks WHERE event_date IS NULL'
    ).fetchall()

    # Get chunk IDs that already have entities
    existing_entity_ids = {
        row[0] for row in conn.execute(
            'SELECT DISTINCT chunk_id FROM chunk_entities'
        ).fetchall()
    }

    stats = {
        'total_chunks': total,
        'already_dated': already_dated,
        'dates_found': 0,
        'entities_found': 0,
        'chunks_processed': 0,
    }

    t0 = time.time()

    for i, row in enumerate(needs_date):
        chunk_id = row['id']
        content = row['content'] or ''
        title = row['title'] or ''
        file_path = row['file_path'] or ''

        # Extract event_date
        event_date = extract_event_date(content, session_ts=None, file_path=file_path)
        if event_date and not dry_run:
            conn.execute(
                'UPDATE chunks SET event_date = ? WHERE id = ?',
                (event_date, chunk_id),
            )
            stats['dates_found'] += 1
        elif event_date:
            stats['dates_found'] += 1

        # Extract entities (skip if already populated)
        if chunk_id not in existing_entity_ids:
            entities = extract_entities(content, title)
            for etype, evalue in entities:
                if not dry_run:
                    conn.execute(
                        'INSERT INTO chunk_entities (chunk_id, entity_type, entity_value) '
                        'VALUES (?, ?, ?)',
                        (chunk_id, etype, evalue),
                    )
                stats['entities_found'] += 1

        stats['chunks_processed'] += 1

        if (i + 1) % 500 == 0:
            if not dry_run:
                conn.commit()
            elapsed = time.time() - t0
            print(
                f'  [{i + 1}/{len(needs_date)}] '
                f'{stats["dates_found"]} dates, {stats["entities_found"]} entities '
                f'({elapsed:.1f}s)',
                file=sys.stderr,
            )

    if not dry_run:
        conn.commit()

    stats['elapsed_seconds'] = round(time.time() - t0, 2)
    conn.close()
    return stats


def main():
    parser = argparse.ArgumentParser(description='Backfill temporal and entity signals')
    parser.add_argument('--db', type=Path, default=DEFAULT_DB, help='Database path')
    parser.add_argument('--dry-run', action='store_true', help='Preview without writing')
    args = parser.parse_args()

    if not args.db.exists():
        print(f'Database not found: {args.db}', file=sys.stderr)
        sys.exit(1)

    print(f'Backfilling signals in {args.db}', file=sys.stderr)
    if args.dry_run:
        print('  (DRY RUN — no changes will be written)', file=sys.stderr)

    stats = backfill(args.db, dry_run=args.dry_run)

    print(f'\nBackfill complete:', file=sys.stderr)
    print(f'  Total chunks: {stats["total_chunks"]}', file=sys.stderr)
    print(f'  Already dated: {stats["already_dated"]}', file=sys.stderr)
    print(f'  Chunks processed: {stats["chunks_processed"]}', file=sys.stderr)
    print(f'  Dates found: {stats["dates_found"]}', file=sys.stderr)
    print(f'  Entities found: {stats["entities_found"]}', file=sys.stderr)
    print(f'  Elapsed: {stats["elapsed_seconds"]}s', file=sys.stderr)


if __name__ == '__main__':
    main()
