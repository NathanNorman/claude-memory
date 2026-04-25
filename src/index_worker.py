"""
Background index worker for webhook-driven codebase indexing.

Claims jobs from the SQLite queue, fetches changes via bare git mirrors,
and incrementally re-indexes only changed files using the existing chunking
and embedding pipeline.
"""

import hashlib
import logging
import os
import sqlite3
import struct
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

# Add src/ to path for code_chunker and quantize
sys.path.insert(0, str(Path(__file__).parent))

from mirror_manager import (
    ensure_mirror,
    git_diff_files,
    git_ls_tree,
    git_show_file,
)
from job_queue import IndexJob, JobQueue

log = logging.getLogger('webhook-pipeline')

HOME = Path.home()
MEMORY_DIR = HOME / '.claude-memory'
DB_PATH = MEMORY_DIR / 'index' / 'memory.db'

# Extensions to index (same as codebase-index.py)
SOURCE_EXTENSIONS = {
    '.py', '.java', '.kt', '.scala', '.sh', '.sql', '.js', '.ts', '.tf', '.md',
}

# Null SHA indicating a new branch
NULL_SHA = '0' * 40

# Model config (matches codebase-index.py — nomic-embed-text-v1.5 for codebase chunks)
CODEBASE_EMBEDDING_MODEL = 'nomic-ai/nomic-embed-text-v1.5'
DEFAULT_MODEL = os.environ.get('MEMORY_CODEBASE_MODEL', CODEBASE_EMBEDDING_MODEL)
MODEL_DIMS = {
    'all-MiniLM-L6-v2': 384,
    'bge-small-en-v1.5': 384,
    'bge-base-en-v1.5': 768,
    'all-mpnet-base-v2': 768,
    'bge-large-en-v1.5': 1024,
    'nomic-ai/CodeRankEmbed': 768,
    'nomic-ai/nomic-embed-text-v1.5': 768,
}
MODEL_PREFIXES = {
    'bge-base-en-v1.5': 'BAAI/bge-base-en-v1.5',
    'bge-small-en-v1.5': 'BAAI/bge-small-en-v1.5',
    'bge-large-en-v1.5': 'BAAI/bge-large-en-v1.5',
}

# Worker poll interval (seconds)
WORKER_POLL_INTERVAL = int(os.environ.get('WORKER_POLL_INTERVAL', '5'))


def content_hash(text: str) -> str:
    """SHA256 hash prefix for content dedup."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _build_structural_prefix(file_path: str, title: str) -> str:
    """Construct structural context prefix for embedding input.

    Format: "{rel_path} | {title}\n"
    """
    if '/' in file_path:
        rel_path = file_path.split('/', 1)[1] if ':' in file_path else file_path
    else:
        rel_path = file_path
    return f'{rel_path} | {title}\n'


def get_db() -> sqlite3.Connection:
    """Get a connection to the main memory database."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.execute('PRAGMA busy_timeout = 5000')
    conn.execute('PRAGMA journal_mode = WAL')
    conn.row_factory = sqlite3.Row
    # Ensure codebase_meta table exists
    conn.execute('''
        CREATE TABLE IF NOT EXISTS codebase_meta (
            codebase TEXT NOT NULL,
            file_path TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            indexed_at TEXT NOT NULL,
            PRIMARY KEY (codebase, file_path)
        )
    ''')
    # Add embedding_binary column if missing (for three-stage search)
    try:
        conn.execute('ALTER TABLE chunks ADD COLUMN embedding_binary BLOB')
    except Exception:
        pass  # Column already exists
    conn.commit()
    return conn


def load_model(model_name: str):
    """Load the sentence-transformers model (called once at startup)."""
    from sentence_transformers import SentenceTransformer
    full_name = MODEL_PREFIXES.get(model_name, model_name)
    log.info('Loading embedding model: %s', full_name)
    return SentenceTransformer(full_name)


def load_quantization_params(conn: sqlite3.Connection, model_name: str, dims: int):
    """Load quantization params if available. Returns (fwd, codebook) or (None, None)."""
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


def chunk_file_content(filepath: str, content: str) -> list[dict]:
    """Chunk file content without writing to disk.

    Writes content to a temporary file and uses code_chunker.chunk_file(),
    since the chunker expects a file path for language-specific parsing.
    """
    from code_chunker import chunk_file

    suffix = Path(filepath).suffix
    with tempfile.NamedTemporaryFile(
        mode='w', suffix=suffix, delete=True, dir=tempfile.gettempdir()
    ) as tmp:
        tmp.write(content)
        tmp.flush()
        return chunk_file(tmp.name)


def embed_and_store_batch(
    conn: sqlite3.Connection,
    model,
    chunks: list[dict],
    rotate_fn,
    codebook,
    batch_size: int = 32,
) -> int:
    """Embed a batch of chunks and store in the database.

    Reused from codebase-index.py with identical logic.
    """
    if not chunks:
        return 0

    from quantize import quantize as quant_fn, quantize_binary

    # Prepend structural context prefix for embedding input (not stored in content)
    texts = []
    for c in chunks:
        prefix = _build_structural_prefix(c['file_path'], c['title'])
        texts.append(prefix + c['content'])
    embeddings = model.encode(texts, normalize_embeddings=True, batch_size=batch_size)

    count = 0
    for chunk, emb in zip(chunks, embeddings):
        chunk_id = f"{chunk['file_path']}:{chunk['chunk_index']}"
        c_hash = content_hash(chunk['content'])

        emb_arr = np.array(emb, dtype=np.float32)
        if rotate_fn is not None and codebook is not None:
            blob = quant_fn(emb_arr, rotate_fn, codebook)
        else:
            dims = len(emb_arr)
            blob = struct.pack(f'{dims}f', *emb_arr.tolist())

        # Binary embedding for three-stage search
        binary_blob = quantize_binary(emb_arr.reshape(1, -1))[0].tobytes()

        conn.execute(
            'INSERT OR REPLACE INTO chunks '
            '(id, file_path, chunk_index, start_line, end_line, '
            'title, content, embedding, embedding_binary, hash, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                chunk_id, chunk['file_path'], chunk['chunk_index'],
                chunk['start_line'], chunk['end_line'],
                chunk['title'], chunk['content'],
                blob, binary_blob, c_hash, int(datetime.now().timestamp() * 1000),
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

    return count


def delete_file_chunks(conn: sqlite3.Connection, file_path_prefix: str) -> None:
    """Delete all chunks, FTS entries for a file path."""
    old_rows = conn.execute(
        'SELECT rowid FROM chunks WHERE file_path = ?', (file_path_prefix,)
    ).fetchall()
    for row in old_rows:
        try:
            conn.execute('DELETE FROM chunks_fts WHERE rowid = ?', (row['rowid'],))
        except Exception:
            pass
    conn.execute('DELETE FROM chunks WHERE file_path = ?', (file_path_prefix,))


def delete_file_deps(conn: sqlite3.Connection, codebase: str, rel_path: str) -> None:
    """Delete edges and symbols for a file (if dep tables exist)."""
    try:
        conn.execute(
            'DELETE FROM edges WHERE source_file = ? AND codebase = ?',
            (rel_path, codebase),
        )
    except sqlite3.OperationalError:
        pass  # Table doesn't exist
    try:
        conn.execute(
            'DELETE FROM symbols WHERE file_path = ? AND codebase = ?',
            (rel_path, codebase),
        )
    except sqlite3.OperationalError:
        pass  # Table doesn't exist


def _is_indexable(filepath: str) -> bool:
    """Check if a file has an indexable extension."""
    return Path(filepath).suffix in SOURCE_EXTENSIONS


def process_job(
    job: IndexJob,
    model,
    conn: sqlite3.Connection,
    rotate_fn,
    codebook,
) -> dict:
    """Process a single index job.

    1. Ensure mirror exists and is up-to-date
    2. Determine changed files (diff or full listing)
    3. Re-index changed files
    """
    repo_name = job.repo_name
    prefix = f'codebase:{repo_name}/'
    now = datetime.now().isoformat()

    # Step 1: Ensure mirror is up-to-date
    mirror_path = ensure_mirror(repo_name, job.clone_url)

    # Step 2: Determine what changed
    needs_full_reindex = False

    if job.before_sha == NULL_SHA:
        # New branch -- full re-index
        log.info('New branch for %s, performing full re-index', repo_name)
        needs_full_reindex = True
    else:
        try:
            changes = git_diff_files(mirror_path, job.before_sha, job.after_sha)
        except RuntimeError as e:
            # Force push or unrelated SHAs -- fall back to full re-index
            log.warning(
                'Diff failed for %s (%s..%s), falling back to full re-index: %s',
                repo_name, job.before_sha[:8], job.after_sha[:8], e,
            )
            needs_full_reindex = True

    if needs_full_reindex:
        return _full_reindex(job, model, conn, rotate_fn, codebook, mirror_path, prefix, now)

    # Step 3: Incremental update based on diff
    return _incremental_reindex(
        job, model, conn, rotate_fn, codebook,
        mirror_path, prefix, now, changes,
    )


def _full_reindex(
    job: IndexJob,
    model,
    conn: sqlite3.Connection,
    rotate_fn,
    codebook,
    mirror_path: Path,
    prefix: str,
    now: str,
) -> dict:
    """Full re-index: list all files at after_sha, re-index everything."""
    repo_name = job.repo_name
    after_sha = job.after_sha

    # Get all files at the after SHA
    all_files = git_ls_tree(mirror_path, after_sha)
    indexable = [f for f in all_files if _is_indexable(f)]

    # Delete all existing chunks for this codebase
    old_rows = conn.execute(
        'SELECT rowid FROM chunks WHERE file_path LIKE ?', (f'{prefix}%',)
    ).fetchall()
    for row in old_rows:
        try:
            conn.execute('DELETE FROM chunks_fts WHERE rowid = ?', (row['rowid'],))
        except Exception:
            pass
    conn.execute('DELETE FROM chunks WHERE file_path LIKE ?', (f'{prefix}%',))
    conn.execute('DELETE FROM codebase_meta WHERE codebase = ?', (repo_name,))
    conn.commit()

    total_chunks = 0
    total_files = 0
    batch: list[dict] = []
    batch_size = 32

    for filepath in indexable:
        try:
            content = git_show_file(mirror_path, after_sha, filepath)
        except RuntimeError:
            continue

        try:
            chunks = chunk_file_content(filepath, content)
        except Exception as e:
            log.debug('Chunk failed %s: %s', filepath, e)
            continue

        # Delete any existing chunks for this file (idempotency)
        delete_file_chunks(conn, f'{prefix}{filepath}')

        for ci, chunk in enumerate(chunks):
            batch.append({
                'file_path': f'{prefix}{filepath}',
                'chunk_index': ci,
                'start_line': chunk['start_line'],
                'end_line': chunk['end_line'],
                'title': chunk['title'],
                'content': chunk['content'],
            })

        # Update codebase_meta
        fhash = content_hash(content)
        conn.execute(
            'INSERT OR REPLACE INTO codebase_meta (codebase, file_path, content_hash, indexed_at) '
            'VALUES (?, ?, ?, ?)',
            (repo_name, filepath, fhash, now),
        )
        total_files += 1

        if len(batch) >= batch_size:
            stored = embed_and_store_batch(conn, model, batch, rotate_fn, codebook)
            total_chunks += stored
            conn.commit()
            batch = []

    # Flush remaining
    if batch:
        stored = embed_and_store_batch(conn, model, batch, rotate_fn, codebook)
        total_chunks += stored

    conn.commit()
    log.info(
        'Full re-index of %s: %d files, %d chunks',
        repo_name, total_files, total_chunks,
    )
    return {
        'mode': 'full',
        'files_indexed': total_files,
        'chunks_stored': total_chunks,
    }


def _incremental_reindex(
    job: IndexJob,
    model,
    conn: sqlite3.Connection,
    rotate_fn,
    codebook,
    mirror_path: Path,
    prefix: str,
    now: str,
    changes: list[tuple[str, str]],
) -> dict:
    """Incremental re-index based on git diff results."""
    repo_name = job.repo_name
    after_sha = job.after_sha

    files_added = 0
    files_deleted = 0
    total_chunks = 0
    batch: list[dict] = []
    batch_size = 32

    for status, filepath in changes:
        if status == 'D':
            # Deleted file: remove chunks, deps, and meta
            delete_file_chunks(conn, f'{prefix}{filepath}')
            delete_file_deps(conn, repo_name, filepath)
            conn.execute(
                'DELETE FROM codebase_meta WHERE codebase = ? AND file_path = ?',
                (repo_name, filepath),
            )
            files_deleted += 1
            log.debug('Removed deleted file: %s', filepath)
            continue

        # Added or Modified: re-index if it has an indexable extension
        if not _is_indexable(filepath):
            continue

        try:
            content = git_show_file(mirror_path, after_sha, filepath)
        except RuntimeError:
            continue

        try:
            chunks = chunk_file_content(filepath, content)
        except Exception as e:
            log.debug('Chunk failed %s: %s', filepath, e)
            continue

        # Delete old chunks for this file before re-indexing (idempotent)
        delete_file_chunks(conn, f'{prefix}{filepath}')
        delete_file_deps(conn, repo_name, filepath)

        for ci, chunk in enumerate(chunks):
            batch.append({
                'file_path': f'{prefix}{filepath}',
                'chunk_index': ci,
                'start_line': chunk['start_line'],
                'end_line': chunk['end_line'],
                'title': chunk['title'],
                'content': chunk['content'],
            })

        # Update codebase_meta
        fhash = content_hash(content)
        conn.execute(
            'INSERT OR REPLACE INTO codebase_meta (codebase, file_path, content_hash, indexed_at) '
            'VALUES (?, ?, ?, ?)',
            (repo_name, filepath, fhash, now),
        )
        files_added += 1

        if len(batch) >= batch_size:
            stored = embed_and_store_batch(conn, model, batch, rotate_fn, codebook)
            total_chunks += stored
            conn.commit()
            batch = []

    # Flush remaining
    if batch:
        stored = embed_and_store_batch(conn, model, batch, rotate_fn, codebook)
        total_chunks += stored

    conn.commit()
    log.info(
        'Incremental re-index of %s: %d added/modified, %d deleted, %d chunks',
        repo_name, files_added, files_deleted, total_chunks,
    )
    return {
        'mode': 'incremental',
        'files_added_or_modified': files_added,
        'files_deleted': files_deleted,
        'chunks_stored': total_chunks,
    }


def worker_loop(
    queue: JobQueue,
    model_name: str = DEFAULT_MODEL,
    poll_interval: int = WORKER_POLL_INTERVAL,
    stop_event=None,
) -> None:
    """Main worker loop: claim job, process, mark done/failed, repeat.

    Args:
        queue: The job queue to poll.
        model_name: Name of the embedding model to load.
        poll_interval: Seconds to sleep when no jobs are available.
        stop_event: threading.Event to signal the worker to stop.
    """
    import threading
    if stop_event is None:
        stop_event = threading.Event()

    # Load model once at startup
    model = load_model(model_name)
    conn = get_db()
    dims = MODEL_DIMS.get(model_name, 384)
    rotate_fn, codebook = load_quantization_params(conn, model_name, dims)

    log.info('Worker started (model=%s, poll_interval=%ds)', model_name, poll_interval)

    while not stop_event.is_set():
        try:
            job = queue.claim_next_job()
        except Exception as e:
            log.error('Failed to claim job: %s', e)
            stop_event.wait(poll_interval)
            continue

        if job is None:
            stop_event.wait(poll_interval)
            continue

        log.info(
            'Processing job %d: %s (%s..%s)',
            job.id, job.repo_name, job.before_sha[:8], job.after_sha[:8],
        )
        t0 = time.time()

        try:
            result = process_job(job, model, conn, rotate_fn, codebook)
            queue.mark_done(job.id)
            elapsed = time.time() - t0
            log.info('Job %d completed in %.1fs: %s', job.id, elapsed, result)
        except Exception as e:
            queue.mark_failed(job.id, str(e))
            log.error('Job %d failed: %s', job.id, e, exc_info=True)

    conn.close()
    log.info('Worker stopped')
