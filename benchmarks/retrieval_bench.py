#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""Retrieval benchmark harness for multi-signal evaluation.

Indexes a synthetic corpus into an in-memory SQLite DB, runs queries
with specified signal combinations, and computes Recall@K metrics.

Usage:
    source ~/.claude-memory/graphiti-venv/bin/activate
    python3 benchmarks/retrieval_bench.py --signals keyword,vector --save baseline.json
    python3 benchmarks/retrieval_bench.py --signals keyword,vector,temporal --compare baseline.json
    python3 benchmarks/retrieval_bench.py --signals keyword,vector,temporal,entity --compare baseline.json
"""

import argparse
import hashlib
import json
import math
import sqlite3
import struct
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))

from test_fixtures import MockEmbeddingModel
from unified_memory_server import (
    FlatSearchBackend,
    TemporalRetrieval,
    EntityRetrieval,
    extract_entities,
    extract_event_date,
)

CORPUS_PATH = Path(__file__).parent / 'corpus.json'
VALID_SIGNALS = {'keyword', 'vector', 'temporal', 'entity', 'deep_search'}


def load_corpus() -> dict:
    with open(CORPUS_PATH) as f:
        return json.load(f)


def build_test_db(corpus: dict, model: MockEmbeddingModel) -> sqlite3.Connection:
    """Build an in-memory SQLite DB from corpus documents."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode = WAL')

    conn.execute('''
        CREATE TABLE chunks (
            id TEXT PRIMARY KEY, file_path TEXT NOT NULL,
            chunk_index INTEGER NOT NULL, start_line INTEGER DEFAULT 0,
            end_line INTEGER DEFAULT 0, title TEXT DEFAULT '',
            content TEXT DEFAULT '', embedding BLOB,
            hash TEXT DEFAULT '', updated_at INTEGER DEFAULT 0,
            event_date TEXT
        )
    ''')
    conn.execute('''
        CREATE VIRTUAL TABLE chunks_fts
        USING fts5(content, title, content='chunks', content_rowid='rowid')
    ''')
    conn.execute('''
        CREATE TABLE files (
            file_path TEXT PRIMARY KEY, content_hash TEXT,
            last_indexed TEXT, chunk_count INTEGER DEFAULT 0, summary TEXT
        )
    ''')
    conn.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)')
    conn.execute('''
        CREATE TABLE chunk_entities (
            chunk_id TEXT NOT NULL, entity_type TEXT NOT NULL,
            entity_value TEXT NOT NULL
        )
    ''')
    conn.execute('CREATE INDEX idx_chunk_entities_value ON chunk_entities(entity_value)')
    conn.execute('CREATE INDEX idx_chunk_entities_chunk ON chunk_entities(chunk_id)')
    conn.execute('CREATE INDEX idx_chunks_event_date ON chunks(event_date)')

    for doc in corpus['documents']:
        content = doc['content']
        title = doc.get('title', '')
        event_date = doc.get('event_date') or extract_event_date(
            content, session_ts=None, file_path=doc['file_path'],
        )

        # Generate embedding
        emb = model.encode(content, normalize_embeddings=True)[0]
        emb_blob = struct.pack(f'{len(emb)}f', *emb.tolist())

        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        conn.execute(
            'INSERT INTO chunks (id, file_path, chunk_index, start_line, end_line, '
            'title, content, embedding, hash, updated_at, event_date) '
            'VALUES (?, ?, 0, 0, 100, ?, ?, ?, ?, ?, ?)',
            (doc['id'], doc['file_path'], title, content,
             emb_blob, content_hash, int(time.time() * 1000), event_date),
        )

        row = conn.execute(
            'SELECT rowid FROM chunks WHERE id = ?', (doc['id'],)
        ).fetchone()
        if row:
            conn.execute(
                'INSERT INTO chunks_fts(rowid, content, title) VALUES (?, ?, ?)',
                (row['rowid'], content, title),
            )

        # Extract and store entities
        entities = extract_entities(content, title)
        for etype, evalue in entities:
            conn.execute(
                'INSERT INTO chunk_entities (chunk_id, entity_type, entity_value) '
                'VALUES (?, ?, ?)',
                (doc['id'], etype, evalue),
            )

    conn.commit()
    return conn


def search_with_signals(
    conn: sqlite3.Connection,
    model: MockEmbeddingModel,
    query: str,
    signals: set[str],
    limit: int = 10,
) -> list[dict]:
    """Run search with specified signal combination."""
    import numpy as np

    ranked_lists: list[list[dict]] = []

    # Keyword search (FTS5)
    if 'keyword' in signals:
        try:
            fts_query = ' OR '.join(
                f'"{w}"' for w in query.split() if len(w) > 2
            )
            if fts_query:
                rows = conn.execute(
                    'SELECT c.id, c.file_path, c.chunk_index, c.start_line, '
                    'c.end_line, c.title, c.content, '
                    'bm25(chunks_fts, 1.0, 0.5) as score '
                    'FROM chunks_fts fts '
                    'JOIN chunks c ON c.rowid = fts.rowid '
                    f'WHERE chunks_fts MATCH ? '
                    'ORDER BY score '
                    'LIMIT ?',
                    (fts_query, limit * 3),
                ).fetchall()
                keyword_hits = [
                    {
                        'id': r['id'], 'file_path': r['file_path'],
                        'chunk_index': r['chunk_index'],
                        'start_line': r['start_line'], 'end_line': r['end_line'],
                        'title': r['title'], 'content': r['content'],
                        'score': abs(r['score']),
                    }
                    for r in rows
                ]
                ranked_lists.append(keyword_hits)
        except Exception:
            pass

    # Vector search
    if 'vector' in signals:
        query_emb = model.encode(query, normalize_embeddings=True)[0]
        rows = conn.execute(
            'SELECT id, file_path, chunk_index, start_line, end_line, '
            'title, content, embedding FROM chunks WHERE embedding IS NOT NULL'
        ).fetchall()

        scored = []
        for r in rows:
            emb_blob = r['embedding']
            if not emb_blob:
                continue
            n_floats = len(emb_blob) // 4
            emb = np.array(struct.unpack(f'{n_floats}f', emb_blob), dtype=np.float32)
            cos_sim = float(np.dot(query_emb, emb))
            scored.append({
                'id': r['id'], 'file_path': r['file_path'],
                'chunk_index': r['chunk_index'],
                'start_line': r['start_line'], 'end_line': r['end_line'],
                'title': r['title'], 'content': r['content'],
                'score': max(0, cos_sim),
            })
        scored.sort(key=lambda x: x['score'], reverse=True)
        ranked_lists.append(scored[:limit * 3])

    # Temporal search
    if 'temporal' in signals:
        from unified_memory_server import parse_temporal_query
        center_date = parse_temporal_query(query)
        if center_date:
            rows = conn.execute(
                'SELECT id, file_path, chunk_index, start_line, end_line, '
                'title, content, event_date FROM chunks '
                'WHERE event_date IS NOT NULL '
                'ORDER BY ABS(julianday(event_date) - julianday(?)) '
                'LIMIT ?',
                (center_date, limit * 3),
            ).fetchall()
            temporal_hits = []
            for r in rows:
                try:
                    event_dt = datetime.strptime(r['event_date'], '%Y-%m-%d')
                    center_dt = datetime.strptime(center_date, '%Y-%m-%d')
                    days = abs((event_dt - center_dt).days)
                    score = math.exp(-(days ** 2) / 98.0)
                    if score >= 0.01:
                        temporal_hits.append({
                            'id': r['id'], 'file_path': r['file_path'],
                            'chunk_index': r['chunk_index'],
                            'start_line': r['start_line'],
                            'end_line': r['end_line'],
                            'title': r['title'], 'content': r['content'],
                            'score': score,
                        })
                except (ValueError, TypeError):
                    continue
            ranked_lists.append(temporal_hits)

    # Entity search
    if 'entity' in signals:
        query_entities = extract_entities(query, '')
        if query_entities:
            entity_values = [v for _, v in query_entities]
            placeholders = ','.join('?' * len(entity_values))
            rows = conn.execute(
                f'SELECT ce.chunk_id, COUNT(DISTINCT ce.entity_value) as overlap '
                f'FROM chunk_entities ce '
                f'WHERE ce.entity_value IN ({placeholders}) '
                f'GROUP BY ce.chunk_id '
                f'ORDER BY overlap DESC LIMIT ?',
                (*entity_values, limit * 3),
            ).fetchall()
            if rows:
                qe_count = len(query_entities)
                chunk_ids = [r['chunk_id'] for r in rows]
                overlap_map = {r['chunk_id']: r['overlap'] for r in rows}
                id_ph = ','.join('?' * len(chunk_ids))
                chunks = conn.execute(
                    f'SELECT id, file_path, chunk_index, start_line, end_line, '
                    f'title, content FROM chunks WHERE id IN ({id_ph})',
                    chunk_ids,
                ).fetchall()
                entity_hits = []
                for c in chunks:
                    overlap = overlap_map.get(c['id'], 0)
                    entity_hits.append({
                        'id': c['id'], 'file_path': c['file_path'],
                        'chunk_index': c['chunk_index'],
                        'start_line': c['start_line'],
                        'end_line': c['end_line'],
                        'title': c['title'], 'content': c['content'],
                        'score': overlap / qe_count,
                    })
                entity_hits.sort(key=lambda x: x['score'], reverse=True)
                ranked_lists.append(entity_hits)

    # Deep search (2-pass multi-hop): run standard signals first, then expand
    if 'deep_search' in signals:
        from unified_memory_server import _extract_pass2_entities, extract_entities as ee
        # Pass 1 merge
        if not ranked_lists:
            return []
        pass1 = FlatSearchBackend.merge_rrf_multi(ranked_lists)[:limit]

        # Extract new entities from top-5 pass 1 results
        original_entities = ee(query, '')
        pass1_for_extraction = [
            {'content': r.get('content', ''), 'title': r.get('title', '')}
            for r in pass1[:5]
        ]
        new_entities = _extract_pass2_entities(pass1_for_extraction, original_entities)

        if new_entities:
            # Pass 2: entity + keyword only
            entity_values = [v for _, v in new_entities]

            # Entity overlap for pass 2
            placeholders = ','.join('?' * len(entity_values))
            p2_entity_hits = []
            try:
                rows = conn.execute(
                    f'SELECT ce.chunk_id, COUNT(DISTINCT ce.entity_value) as overlap '
                    f'FROM chunk_entities ce '
                    f'WHERE ce.entity_value IN ({placeholders}) '
                    f'GROUP BY ce.chunk_id ORDER BY overlap DESC LIMIT ?',
                    (*entity_values, limit * 3),
                ).fetchall()
                if rows:
                    qe_count = len(new_entities)
                    chunk_ids = [r['chunk_id'] for r in rows]
                    overlap_map = {r['chunk_id']: r['overlap'] for r in rows}
                    id_ph = ','.join('?' * len(chunk_ids))
                    chunks = conn.execute(
                        f'SELECT id, file_path, chunk_index, start_line, end_line, '
                        f'title, content FROM chunks WHERE id IN ({id_ph})',
                        chunk_ids,
                    ).fetchall()
                    for c in chunks:
                        p2_entity_hits.append({
                            'id': c['id'], 'file_path': c['file_path'],
                            'chunk_index': c['chunk_index'],
                            'start_line': c['start_line'],
                            'end_line': c['end_line'],
                            'title': c['title'], 'content': c['content'],
                            'score': overlap_map.get(c['id'], 0) / qe_count,
                        })
            except Exception:
                pass

            # Keyword for pass 2
            kw_query = ' '.join(entity_values[:10])
            p2_kw = ' OR '.join(f'"{w}"' for w in kw_query.split() if len(w) > 2)
            p2_keyword_hits = []
            if p2_kw:
                try:
                    rows = conn.execute(
                        'SELECT c.id, c.file_path, c.chunk_index, c.start_line, '
                        'c.end_line, c.title, c.content, '
                        'bm25(chunks_fts, 1.0, 0.5) as score '
                        'FROM chunks_fts fts JOIN chunks c ON c.rowid = fts.rowid '
                        'WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?',
                        (p2_kw, limit * 3),
                    ).fetchall()
                    p2_keyword_hits = [{
                        'id': r['id'], 'file_path': r['file_path'],
                        'chunk_index': r['chunk_index'],
                        'start_line': r['start_line'], 'end_line': r['end_line'],
                        'title': r['title'], 'content': r['content'],
                        'score': abs(r['score']),
                    } for r in rows]
                except Exception:
                    pass

            pass2 = FlatSearchBackend.merge_rrf_multi([p2_entity_hits, p2_keyword_hits])

            # Merge pass 1 + pass 2 via RRF, dedup by ID
            combined = FlatSearchBackend.merge_rrf_multi([pass1, pass2])
            return combined[:limit]

        return pass1[:limit]

    # RRF merge
    if not ranked_lists:
        return []
    merged = FlatSearchBackend.merge_rrf_multi(ranked_lists)
    return merged[:limit]


def compute_metrics(
    corpus: dict,
    conn: sqlite3.Connection,
    model: MockEmbeddingModel,
    signals: set[str],
) -> dict:
    """Compute Recall@5 and Recall@10 per category."""
    categories: dict[str, list] = {}
    for q in corpus['queries']:
        cat = q['category']
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(q)

    results = {}
    for cat, queries in sorted(categories.items()):
        r5_scores = []
        r10_scores = []

        for q in queries:
            hits = search_with_signals(conn, model, q['query'], signals, limit=10)
            hit_ids = [h['id'] for h in hits]
            relevant = set(q['relevant_ids'])

            r5 = len(relevant & set(hit_ids[:5])) / len(relevant) if relevant else 0
            r10 = len(relevant & set(hit_ids[:10])) / len(relevant) if relevant else 0
            r5_scores.append(r5)
            r10_scores.append(r10)

        results[cat] = {
            'R@5': round(sum(r5_scores) / len(r5_scores), 3) if r5_scores else 0,
            'R@10': round(sum(r10_scores) / len(r10_scores), 3) if r10_scores else 0,
            'n_queries': len(queries),
        }

    # Compute overall
    all_r5 = []
    all_r10 = []
    for cat_data in results.values():
        all_r5.extend([cat_data['R@5']] * cat_data['n_queries'])
        all_r10.extend([cat_data['R@10']] * cat_data['n_queries'])

    results['overall'] = {
        'R@5': round(sum(all_r5) / len(all_r5), 3) if all_r5 else 0,
        'R@10': round(sum(all_r10) / len(all_r10), 3) if all_r10 else 0,
        'n_queries': sum(d['n_queries'] for d in results.values() if d != results.get('overall')),
    }

    return results


def print_results(results: dict, signals: set[str], baseline: dict | None = None):
    """Print results table with optional baseline comparison."""
    sig_str = '+'.join(sorted(signals))
    print(f'\n{"="*60}')
    print(f'  Retrieval Benchmark: {sig_str}')
    print(f'{"="*60}')
    print(f'  {"Category":<15} {"R@5":>8} {"R@10":>8}', end='')
    if baseline:
        print(f' {"dR@5":>8} {"dR@10":>8}', end='')
    print()
    print(f'  {"-"*15} {"-"*8} {"-"*8}', end='')
    if baseline:
        print(f' {"-"*8} {"-"*8}', end='')
    print()

    for cat in sorted(results.keys()):
        if cat == 'overall':
            continue
        r = results[cat]
        print(f'  {cat:<15} {r["R@5"]:>8.3f} {r["R@10"]:>8.3f}', end='')
        if baseline and cat in baseline:
            dr5 = r['R@5'] - baseline[cat]['R@5']
            dr10 = r['R@10'] - baseline[cat]['R@10']
            print(f' {dr5:>+8.3f} {dr10:>+8.3f}', end='')
        print()

    # Overall
    print(f'  {"-"*15} {"-"*8} {"-"*8}', end='')
    if baseline:
        print(f' {"-"*8} {"-"*8}', end='')
    print()
    o = results['overall']
    print(f'  {"OVERALL":<15} {o["R@5"]:>8.3f} {o["R@10"]:>8.3f}', end='')
    if baseline and 'overall' in baseline:
        dr5 = o['R@5'] - baseline['overall']['R@5']
        dr10 = o['R@10'] - baseline['overall']['R@10']
        print(f' {dr5:>+8.3f} {dr10:>+8.3f}', end='')
    print(f'\n')


def main():
    parser = argparse.ArgumentParser(description='Retrieval benchmark harness')
    parser.add_argument(
        '--signals', required=True,
        help='Comma-separated signals: keyword,vector,temporal,entity',
    )
    parser.add_argument('--save', type=Path, help='Save results to JSON file')
    parser.add_argument('--compare', type=Path, help='Compare against baseline JSON')
    parser.add_argument('--corpus', type=Path, default=CORPUS_PATH, help='Corpus JSON path')
    args = parser.parse_args()

    signals = set(args.signals.split(','))
    invalid = signals - VALID_SIGNALS
    if invalid:
        print(f'Invalid signals: {invalid}. Valid: {VALID_SIGNALS}', file=sys.stderr)
        sys.exit(1)

    corpus = load_corpus()
    model = MockEmbeddingModel()

    print(f'Building test DB with {len(corpus["documents"])} documents...', file=sys.stderr)
    t0 = time.time()
    conn = build_test_db(corpus, model)
    print(f'DB built in {time.time() - t0:.2f}s', file=sys.stderr)

    print(f'Running {len(corpus["queries"])} queries with signals: {signals}...', file=sys.stderr)
    t0 = time.time()
    results = compute_metrics(corpus, conn, model, signals)
    print(f'Benchmark complete in {time.time() - t0:.2f}s', file=sys.stderr)

    baseline = None
    if args.compare and args.compare.exists():
        with open(args.compare) as f:
            baseline = json.load(f)

    print_results(results, signals, baseline)

    if args.save:
        with open(args.save, 'w') as f:
            json.dump(results, f, indent=2)
        print(f'Results saved to {args.save}', file=sys.stderr)

    conn.close()


if __name__ == '__main__':
    main()
