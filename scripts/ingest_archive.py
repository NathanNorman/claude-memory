#!/usr/bin/env python3
"""
Ingest conversation archive into unified-memory.

Reads JSONL conversation files from an extracted archive directory,
parses them into exchange-aware chunks, embeds with bge-base-en-v1.5,
and stores as 4-bit quantized embeddings in the search index.

Skips agent/subagent files. Skips already-indexed files by content hash.

Usage:
    python3 scripts/ingest_archive.py /tmp/claude-conv-archive/claude_conversations --progress
    python3 scripts/ingest_archive.py /tmp/claude-conv-archive/claude_conversations --progress --background
"""

import argparse
import hashlib
import os
import re
import signal
import sqlite3
import struct
import sys
import time
from datetime import datetime
from pathlib import Path

# Add src/ and scripts/ to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
from conversation_parser import parse_conversation_jsonl

HOME = Path.home()
MEMORY_DIR = HOME / '.claude-memory'
DB_PATH = MEMORY_DIR / 'index' / 'memory.db'
LOCK_PATH = MEMORY_DIR / 'index' / 'reindex.lock'
PID_PATH = MEMORY_DIR / 'ingest-archive.pid'
LOG_PATH = MEMORY_DIR / 'ingest-archive.log'
STALE_LOCK_SECONDS = 300

# Exchange chunking params (match Node.js chunker.ts)
MAX_CHUNK_CHARS = 1600  # ~400 tokens

_shutdown = False


def _signal_handler(sig, frame):
    global _shutdown
    _shutdown = True
    print('\n[ingest-archive] Caught signal, finishing current batch...', file=sys.stderr)


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ──────────────────────────────────────────────────────────────
# Database & lock helpers (from bulk_index.py)
# ──────────────────────────────────────────────────────────────


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.execute('PRAGMA busy_timeout = 5000')
    conn.execute('PRAGMA journal_mode = WAL')
    conn.row_factory = sqlite3.Row
    return conn


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def acquire_lock() -> bool:
    try:
        if LOCK_PATH.exists():
            age = time.time() - LOCK_PATH.stat().st_mtime
            if age > STALE_LOCK_SECONDS:
                LOCK_PATH.unlink()
            else:
                return False
        LOCK_PATH.write_text(str(os.getpid()))
        return True
    except Exception:
        return False


def release_lock():
    try:
        if LOCK_PATH.exists():
            owner = LOCK_PATH.read_text().strip()
            if owner == str(os.getpid()):
                LOCK_PATH.unlink()
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────
# File naming → index path mapping
# ──────────────────────────────────────────────────────────────


def archive_filename_to_index_path(filename: str) -> str:
    """Convert archive filename to the conversations/ path used in the index.

    Archive format: project_uuid.jsonl or project_uuid_date.jsonl
    Index format:   conversations/project/uuid.jsonl

    Examples:
        toast-analytics_6bb5cddd-...-dbbe500.jsonl
            → conversations/toast-analytics/6bb5cddd-...-dbbe500.jsonl
        Users-nathan-norman_22d5a43c-...79077d.jsonl
            → conversations/Users-nathan-norman/22d5a43c-...79077d.jsonl
        -Users-nathan-norman-financial-clarity_cdf2cac9-..._2025-09-18.jsonl
            → conversations/-Users-nathan-norman-financial-clarity/cdf2cac9-....jsonl
    """
    stem = filename.replace('.jsonl', '')

    # Find UUID pattern in the filename
    uuid_pattern = r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'
    match = re.search(uuid_pattern, stem)
    if not match:
        return f'conversations/unknown/{filename}'

    uuid = match.group(1)
    # Everything before _uuid is the project name
    uuid_start = stem.index(uuid)
    project = stem[:uuid_start].rstrip('_')
    if not project:
        project = 'unknown'

    return f'conversations/{project}/{uuid}.jsonl'


# ──────────────────────────────────────────────────────────────
# Exchange-aware chunking (matches Node.js chunker.ts)
# ──────────────────────────────────────────────────────────────


def chunk_exchanges(exchanges, project: str, session_id: str, timestamp: str) -> list[dict]:
    """Chunk conversation exchanges into groups, matching Node.js behavior.

    Returns list of chunk dicts ready for embedding.
    """
    chunks = []
    current_texts = []
    current_chars = 0
    chunk_start = 0

    def flush(end_idx):
        if not current_texts:
            return
        text = '\n\n---\n\n'.join(current_texts)
        # Build title from first user message
        first_line = current_texts[0][:80] if current_texts else ''
        title = f'{project} — {first_line}'
        chunks.append({
            'content': text,
            'title': title,
            'start_line': chunk_start + 1,
            'end_line': end_idx + 1,
        })

    for i, ex in enumerate(exchanges):
        formatted = f'User: {ex.user_message}'
        if ex.assistant_message:
            formatted += f'\n\nAssistant: {ex.assistant_message}'
        if ex.tool_names:
            unique_tools = list(dict.fromkeys(ex.tool_names))
            formatted += f'\n\nTools: {", ".join(unique_tools)}'

        ex_chars = len(formatted)

        if current_chars + ex_chars > MAX_CHUNK_CHARS and current_texts:
            flush(i - 1)
            current_texts = []
            current_chars = 0
            chunk_start = i

        current_texts.append(formatted)
        current_chars += ex_chars

    flush(len(exchanges) - 1)
    return chunks


# ──────────────────────────────────────────────────────────────
# Embedding (from bulk_index.py)
# ──────────────────────────────────────────────────────────────


MODEL_PREFIXES = {
    'bge-base-en-v1.5': 'BAAI/bge-base-en-v1.5',
    'bge-small-en-v1.5': 'BAAI/bge-small-en-v1.5',
    'bge-large-en-v1.5': 'BAAI/bge-large-en-v1.5',
    'all-MiniLM-L6-v2': 'all-MiniLM-L6-v2',
}


def load_model(model_name: str):
    from sentence_transformers import SentenceTransformer
    full_name = MODEL_PREFIXES.get(model_name, model_name)
    print(f'[ingest-archive] Loading model: {full_name}', file=sys.stderr)
    return SentenceTransformer(full_name)


def load_quantization_params(conn, model_name, dims):
    try:
        row = conn.execute(
            'SELECT bit_width, rotation_seed, codebook FROM quantization_meta '
            'WHERE model_name = ? AND dims = ? ORDER BY created_at DESC LIMIT 1',
            (model_name, dims),
        ).fetchone()
        if not row:
            return None, None
        from quantize import generate_rotation
        bit_width = row['bit_width']
        seed = row['rotation_seed']
        n_centroids = 1 << bit_width
        codebook = np.array(
            struct.unpack(f'{n_centroids}f', row['codebook']),
            dtype=np.float32,
        )
        fwd, _ = generate_rotation(dims, seed)
        return fwd, codebook
    except Exception:
        return None, None


def embed_and_store_batch(conn, model, chunks, file_path, rotate_fn, codebook, batch_size=32):
    """Embed chunks and store with quantized embeddings."""
    if not chunks:
        return 0

    from quantize import quantize as quant_fn

    texts = [c['content'] for c in chunks]
    embeddings = model.encode(texts, normalize_embeddings=True, batch_size=batch_size)

    count = 0
    for chunk, emb in zip(chunks, embeddings):
        chunk_id = f"{file_path}:{chunk['chunk_index']}"
        c_hash = content_hash(chunk['content'])

        emb_arr = np.array(emb, dtype=np.float32)
        if rotate_fn is not None and codebook is not None:
            blob = quant_fn(emb_arr, rotate_fn, codebook)
        else:
            dims = len(emb_arr)
            blob = struct.pack(f'{dims}f', *emb_arr.tolist())

        conn.execute(
            'INSERT OR REPLACE INTO chunks '
            '(id, file_path, chunk_index, start_line, end_line, '
            'title, content, embedding, hash, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                chunk_id, file_path, chunk['chunk_index'],
                chunk['start_line'], chunk['end_line'],
                chunk['title'], chunk['content'],
                blob, c_hash, int(datetime.now().timestamp() * 1000),
            ),
        )

        # Update FTS5
        row = conn.execute(
            'SELECT rowid FROM chunks WHERE id = ?', (chunk_id,)
        ).fetchone()
        if row:
            try:
                conn.execute('DELETE FROM chunks_fts WHERE rowid = ?', (row['rowid'],))
            except Exception:
                pass
            try:
                conn.execute(
                    'INSERT INTO chunks_fts(rowid, content, title) VALUES (?, ?, ?)',
                    (row['rowid'], chunk['content'], chunk['title']),
                )
            except Exception:
                pass

        count += 1

    conn.commit()
    return count


# ──────────────────────────────────────────────────────────────
# Main ingestion loop
# ──────────────────────────────────────────────────────────────


def ingest_directory(
    archive_dir: Path,
    conn: sqlite3.Connection,
    model,
    rotate_fn,
    codebook,
    progress: bool = False,
) -> dict:
    """Ingest all main session JSONL files from archive directory."""

    # Find main session files (skip agent files)
    all_files = sorted(archive_dir.glob('*.jsonl'))
    main_files = [f for f in all_files if 'agent-' not in f.name]

    print(f'[ingest-archive] Found {len(main_files)} main session files '
          f'(skipped {len(all_files) - len(main_files)} agent files)', file=sys.stderr)

    # Check which are already indexed
    existing_paths = set()
    rows = conn.execute(
        "SELECT DISTINCT file_path FROM chunks WHERE file_path LIKE 'conversations/%'"
    ).fetchall()
    for row in rows:
        existing_paths.add(row['file_path'])

    stats = {
        'total_files': len(main_files),
        'skipped_existing': 0,
        'skipped_empty': 0,
        'skipped_parse_fail': 0,
        'indexed': 0,
        'chunks_stored': 0,
    }

    batch = []
    batch_file_path = None

    for i, filepath in enumerate(main_files):
        if _shutdown:
            break

        index_path = archive_filename_to_index_path(filepath.name)

        if index_path in existing_paths:
            stats['skipped_existing'] += 1
            continue

        # Parse conversation
        result = parse_conversation_jsonl(str(filepath))
        if result is None:
            stats['skipped_parse_fail'] += 1
            continue

        if not result.exchanges:
            stats['skipped_empty'] += 1
            continue

        # Extract project name from index path
        parts = index_path.split('/')
        project = parts[1] if len(parts) > 2 else 'unknown'

        # Chunk exchanges
        chunks = chunk_exchanges(
            result.exchanges,
            project=project,
            session_id=result.session_id or filepath.stem,
            timestamp=result.timestamp or '',
        )

        if not chunks:
            stats['skipped_empty'] += 1
            continue

        # Assign chunk indices and file_path
        for j, chunk in enumerate(chunks):
            chunk['chunk_index'] = j
            chunk['file_path'] = index_path

        # Embed and store
        if not acquire_lock():
            # Wait briefly for lock
            time.sleep(2)
            if not acquire_lock():
                print('[ingest-archive] Could not acquire lock, skipping file', file=sys.stderr)
                continue

        stored = embed_and_store_batch(
            conn, model, chunks, index_path, rotate_fn, codebook
        )
        release_lock()

        stats['indexed'] += 1
        stats['chunks_stored'] += stored
        existing_paths.add(index_path)

        # Update files table
        conn.execute(
            'INSERT OR REPLACE INTO files (file_path, content_hash, last_indexed, chunk_count) '
            'VALUES (?, ?, ?, ?)',
            (
                index_path,
                content_hash(filepath.read_text(errors='replace')[:1000]),
                int(datetime.now().timestamp() * 1000),
                stored,
            ),
        )
        conn.commit()

        if progress and (stats['indexed'] % 25 == 0 or stats['indexed'] <= 5):
            elapsed = i + 1
            pct = (elapsed / len(main_files)) * 100
            print(
                f"[ingest-archive] {elapsed}/{len(main_files)} ({pct:.0f}%) — "
                f"{stats['indexed']} indexed, {stats['chunks_stored']} chunks, "
                f"{stats['skipped_existing']} existing, {stats['skipped_parse_fail']} failed",
                file=sys.stderr,
            )

    return stats


def daemonize():
    pid = os.fork()
    if pid > 0:
        print(f'[ingest-archive] Started background process (PID in {PID_PATH})', file=sys.stderr)
        sys.exit(0)
    os.setsid()
    pid = os.fork()
    if pid > 0:
        sys.exit(0)
    log_fd = open(LOG_PATH, 'a')
    os.dup2(log_fd.fileno(), sys.stdout.fileno())
    os.dup2(log_fd.fileno(), sys.stderr.fileno())
    PID_PATH.write_text(str(os.getpid()))


def main():
    parser = argparse.ArgumentParser(description='Ingest conversation archive into unified-memory')
    parser.add_argument('archive_dir', help='Directory containing extracted JSONL files')
    parser.add_argument('--model', default='bge-base-en-v1.5', help='Embedding model')
    parser.add_argument('--progress', action='store_true', help='Show progress')
    parser.add_argument('--background', action='store_true', help='Run as daemon')
    parser.add_argument('--dry-run', action='store_true', help='Count files without indexing')
    args = parser.parse_args()

    archive_dir = Path(args.archive_dir)
    if not archive_dir.exists():
        print(f'Error: {archive_dir} does not exist', file=sys.stderr)
        sys.exit(1)

    model_dims = {
        'bge-base-en-v1.5': 768,
        'bge-small-en-v1.5': 384,
        'all-MiniLM-L6-v2': 384,
    }
    dims = model_dims.get(args.model, 768)

    if args.dry_run:
        all_files = sorted(archive_dir.glob('*.jsonl'))
        main_files = [f for f in all_files if 'agent-' not in f.name]
        print(f'Would index {len(main_files)} main session files '
              f'(skipping {len(all_files) - len(main_files)} agent files)')
        return

    if args.background:
        daemonize()

    print(f'[ingest-archive] Starting: model={args.model}, dims={dims}', file=sys.stderr)

    conn = get_conn()
    rotate_fn, codebook = load_quantization_params(conn, args.model, dims)
    if rotate_fn:
        print('[ingest-archive] Quantization params loaded — storing quantized', file=sys.stderr)
    else:
        print('[ingest-archive] No quantization — storing float32', file=sys.stderr)

    model = load_model(args.model)

    stats = ingest_directory(archive_dir, conn, model, rotate_fn, codebook, args.progress)

    conn.close()

    if args.background and PID_PATH.exists():
        PID_PATH.unlink(missing_ok=True)

    print(f'\n[ingest-archive] Complete:', file=sys.stderr)
    print(f'  Total files:      {stats["total_files"]}', file=sys.stderr)
    print(f'  Indexed:          {stats["indexed"]}', file=sys.stderr)
    print(f'  Chunks stored:    {stats["chunks_stored"]}', file=sys.stderr)
    print(f'  Skipped existing: {stats["skipped_existing"]}', file=sys.stderr)
    print(f'  Skipped empty:    {stats["skipped_empty"]}', file=sys.stderr)
    print(f'  Parse failures:   {stats["skipped_parse_fail"]}', file=sys.stderr)


if __name__ == '__main__':
    main()
