#!/usr/bin/env python3
"""
Bulk indexer for unified-memory.

Embeds and indexes large corpora (codebases, fine-grained conversation
re-chunking) as a one-time or periodic batch job. Supports incremental
operation, progress tracking, background execution, and feeds into the
TurboQuant quantization pipeline.

Usage:
    python3 scripts/bulk_index.py --source codebase --progress
    python3 scripts/bulk_index.py --source codebase --background
    python3 scripts/bulk_index.py --source all --progress
"""

import argparse
import hashlib
import json
import os
import signal
import sqlite3
import struct
import sys
import time
from datetime import datetime
from pathlib import Path

# Add src/ to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import numpy as np

HOME = Path.home()
MEMORY_DIR = HOME / '.claude-memory'
DB_PATH = MEMORY_DIR / 'index' / 'memory.db'
LOCK_PATH = MEMORY_DIR / 'index' / 'reindex.lock'
PID_PATH = MEMORY_DIR / 'bulk-index.pid'
LOG_PATH = MEMORY_DIR / 'bulk-index.log'
CODEBASE_CONFIG = MEMORY_DIR / 'codebase-sources.json'
STALE_LOCK_SECONDS = 300  # 5 minutes, matches Node.js indexer

# Graceful shutdown flag
_shutdown = False


def _signal_handler(sig, frame):
    global _shutdown
    _shutdown = True
    print('\n[bulk-index] Caught signal, finishing current batch...', file=sys.stderr)


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ──────────────────────────────────────────────────────────────
# Lock management (matches Node.js indexer behavior)
# ──────────────────────────────────────────────────────────────


def acquire_lock() -> bool:
    """Acquire reindex.lock. Reclaim stale locks older than 5 minutes."""
    try:
        if LOCK_PATH.exists():
            age = time.time() - LOCK_PATH.stat().st_mtime
            if age > STALE_LOCK_SECONDS:
                print(f'[bulk-index] Reclaiming stale lock ({age:.0f}s old)', file=sys.stderr)
                LOCK_PATH.unlink()
            else:
                return False
        # Create lock file with PID
        LOCK_PATH.write_text(str(os.getpid()))
        return True
    except Exception:
        return False


def release_lock():
    """Release reindex.lock if we own it."""
    try:
        if LOCK_PATH.exists():
            owner = LOCK_PATH.read_text().strip()
            if owner == str(os.getpid()):
                LOCK_PATH.unlink()
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────
# Database helpers
# ──────────────────────────────────────────────────────────────


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.execute('PRAGMA busy_timeout = 5000')
    conn.execute('PRAGMA journal_mode = WAL')
    conn.row_factory = sqlite3.Row
    return conn


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def is_already_indexed(conn: sqlite3.Connection, chunk_hash: str) -> bool:
    """Check if a chunk with this content hash already exists."""
    row = conn.execute(
        'SELECT 1 FROM chunks WHERE hash = ? LIMIT 1', (chunk_hash,)
    ).fetchone()
    return row is not None


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


# ──────────────────────────────────────────────────────────────
# Embedding
# ──────────────────────────────────────────────────────────────


MODEL_PREFIXES = {
    'bge-base-en-v1.5': 'BAAI/bge-base-en-v1.5',
    'bge-small-en-v1.5': 'BAAI/bge-small-en-v1.5',
    'bge-large-en-v1.5': 'BAAI/bge-large-en-v1.5',
    'all-MiniLM-L6-v2': 'all-MiniLM-L6-v2',
    'all-mpnet-base-v2': 'all-mpnet-base-v2',
}


def load_model(model_name: str):
    """Load sentence-transformers model."""
    from sentence_transformers import SentenceTransformer
    full_name = MODEL_PREFIXES.get(model_name, model_name)
    print(f'[bulk-index] Loading model: {full_name}', file=sys.stderr)
    return SentenceTransformer(full_name)


def embed_and_store_batch(
    conn: sqlite3.Connection,
    model,
    chunks: list[dict],
    rotate_fn,
    codebook,
    batch_size: int = 32,
) -> int:
    """Embed a batch of chunks and store in the database.

    Each chunk dict must have: file_path, chunk_index, start_line, end_line,
    title, content.

    Returns number of chunks stored.
    """
    if not chunks:
        return 0

    from quantize import quantize as quant_fn

    texts = [c['content'] for c in chunks]
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

        conn.execute(
            'INSERT OR REPLACE INTO chunks '
            '(id, file_path, chunk_index, start_line, end_line, '
            'title, content, embedding, hash, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                chunk_id, chunk['file_path'], chunk['chunk_index'],
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
                conn.execute(
                    'DELETE FROM chunks_fts WHERE rowid = ?', (row['rowid'],)
                )
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
# Source: Codebase
# ──────────────────────────────────────────────────────────────


def load_codebase_config() -> list[dict]:
    """Load codebase source configuration."""
    if not CODEBASE_CONFIG.exists():
        print('[bulk-index] No codebase-sources.json found, skipping codebase indexing',
              file=sys.stderr)
        return []

    with open(CODEBASE_CONFIG) as f:
        config = json.load(f)

    repos = config.get('repos', [])
    valid = []
    for repo in repos:
        path = Path(os.path.expanduser(repo['path']))
        if not path.exists():
            print(f'[bulk-index] WARNING: repo path not found: {path}', file=sys.stderr)
            continue
        repo['resolved_path'] = path
        valid.append(repo)

    return valid


def get_repo_head_sha(repo_path: Path) -> str:
    """Get current HEAD SHA of a git repo."""
    import subprocess
    result = subprocess.run(
        ['git', 'rev-parse', 'HEAD'],
        cwd=str(repo_path),
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ''


def get_stored_sha(conn: sqlite3.Connection, repo_name: str) -> str:
    """Get the last indexed commit SHA for a repo from the files table."""
    sentinel = f'codebase/{repo_name}/.git-sha'
    row = conn.execute(
        'SELECT content_hash FROM files WHERE file_path = ?', (sentinel,)
    ).fetchone()
    return row['content_hash'] if row else ''


def store_repo_sha(conn: sqlite3.Connection, repo_name: str, sha: str) -> None:
    """Store the indexed commit SHA for a repo."""
    sentinel = f'codebase/{repo_name}/.git-sha'
    conn.execute(
        'INSERT OR REPLACE INTO files (file_path, content_hash, last_indexed, chunk_count) '
        'VALUES (?, ?, ?, 0)',
        (sentinel, sha, int(datetime.now().timestamp() * 1000)),
    )
    conn.commit()


def get_changed_files(repo_path: Path, old_sha: str) -> tuple[list[str], list[str]]:
    """Get files changed since old_sha. Returns (changed, deleted) file lists."""
    import subprocess
    result = subprocess.run(
        ['git', 'diff', '--name-status', f'{old_sha}..HEAD'],
        cwd=str(repo_path),
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return [], []

    changed = []
    deleted = []
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue
        parts = line.split('\t', 1)
        if len(parts) < 2:
            continue
        status, filepath = parts[0], parts[1]
        if status.startswith('D'):
            deleted.append(filepath)
        else:
            changed.append(filepath)

    return changed, deleted


def delete_chunks_for_file(conn: sqlite3.Connection, file_path_prefix: str) -> int:
    """Delete all chunks for a given file_path prefix."""
    old_rows = conn.execute(
        'SELECT rowid FROM chunks WHERE file_path = ?', (file_path_prefix,)
    ).fetchall()
    for row in old_rows:
        try:
            conn.execute('DELETE FROM chunks_fts WHERE rowid = ?', (row['rowid'],))
        except Exception:
            pass
    conn.execute('DELETE FROM chunks WHERE file_path = ?', (file_path_prefix,))
    return len(old_rows)


def index_codebase(
    conn: sqlite3.Connection,
    model,
    rotate_fn,
    codebook,
    repos: list[dict],
    progress: bool = False,
) -> int:
    """Index codebase sources with git-based incremental updates."""
    from code_chunker import chunk_file

    total = 0
    for repo in repos:
        if _shutdown:
            break

        repo_path = repo['resolved_path']
        repo_name = repo_path.name
        include = repo.get('include', ['**/*'])
        exclude = repo.get('exclude', [])

        print(f'[bulk-index] Indexing repo: {repo_name} ({repo_path})', file=sys.stderr)

        current_sha = get_repo_head_sha(repo_path)
        stored_sha = get_stored_sha(conn, repo_name)

        # Determine which files to process
        import subprocess
        if stored_sha and current_sha != stored_sha:
            # Incremental: only changed files since last index
            changed, deleted = get_changed_files(repo_path, stored_sha)
            print(f'[bulk-index] Incremental: {len(changed)} changed, {len(deleted)} deleted '
                  f'(since {stored_sha[:8]})', file=sys.stderr)

            # Delete chunks for removed files
            for f in deleted:
                prefix = f'codebase/{repo_name}/{f}'
                n = delete_chunks_for_file(conn, prefix)
                if n:
                    print(f'[bulk-index] Deleted {n} chunks for {f}', file=sys.stderr)
            conn.commit()

            files = changed
        elif stored_sha == current_sha:
            print(f'[bulk-index] Up to date at {current_sha[:8]}, skipping', file=sys.stderr)
            continue
        else:
            # Full index
            result = subprocess.run(
                ['git', 'ls-files'],
                cwd=str(repo_path),
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                print(f'[bulk-index] WARNING: git ls-files failed for {repo_name}',
                      file=sys.stderr)
                continue
            files = result.stdout.strip().split('\n')

        # Filter by include/exclude globs
        import fnmatch
        filtered = []
        for f in files:
            if any(fnmatch.fnmatch(f, pat) for pat in include):
                if not any(fnmatch.fnmatch(f, pat) for pat in exclude):
                    filtered.append(f)

        print(f'[bulk-index] {len(filtered)} files to process', file=sys.stderr)

        # Chunk and embed in batches
        batch = []
        for i, rel_path in enumerate(filtered):
            if _shutdown:
                break

            full_path = repo_path / rel_path
            if not full_path.exists() or full_path.stat().st_size == 0:
                continue

            file_path_prefix = f'codebase/{repo_name}/{rel_path}'

            try:
                chunks = chunk_file(str(full_path))
            except Exception as e:
                continue

            for j, chunk in enumerate(chunks):
                c_hash = content_hash(chunk['content'])
                if is_already_indexed(conn, c_hash):
                    continue
                batch.append({
                    'file_path': file_path_prefix,
                    'chunk_index': j,
                    'start_line': chunk.get('start_line', 1),
                    'end_line': chunk.get('end_line', 1),
                    'title': chunk.get('title', rel_path),
                    'content': chunk['content'],
                })

            # Embed when batch is full
            if len(batch) >= 32:
                if not acquire_lock():
                    print('[bulk-index] Waiting for lock...', file=sys.stderr)
                    time.sleep(5)
                    continue

                stored = embed_and_store_batch(conn, model, batch, rotate_fn, codebook)
                release_lock()
                total += stored
                batch = []

                if progress:
                    print(f'[bulk-index] {repo_name}: {i+1}/{len(filtered)} files, '
                          f'{total} chunks stored', file=sys.stderr)

        # Flush remaining
        if batch and not _shutdown:
            if acquire_lock():
                stored = embed_and_store_batch(conn, model, batch, rotate_fn, codebook)
                release_lock()
                total += stored

        # Store the indexed SHA for incremental next time
        if current_sha and not _shutdown:
            store_repo_sha(conn, repo_name, current_sha)
            print(f'[bulk-index] {repo_name}: stored SHA {current_sha[:8]}', file=sys.stderr)

        print(f'[bulk-index] {repo_name}: done, {total} total chunks', file=sys.stderr)

    return total


# ──────────────────────────────────────────────────────────────
# Background execution
# ──────────────────────────────────────────────────────────────


def daemonize():
    """Double-fork to daemonize, redirect output to log."""
    # First fork
    pid = os.fork()
    if pid > 0:
        print(f'[bulk-index] Started background process (PID will be in {PID_PATH})',
              file=sys.stderr)
        sys.exit(0)

    os.setsid()

    # Second fork
    pid = os.fork()
    if pid > 0:
        sys.exit(0)

    # Redirect stdout/stderr to log
    log_fd = open(LOG_PATH, 'a')
    os.dup2(log_fd.fileno(), sys.stdout.fileno())
    os.dup2(log_fd.fileno(), sys.stderr.fileno())

    # Write PID file
    PID_PATH.write_text(str(os.getpid()))


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description='Bulk indexer for unified-memory')
    parser.add_argument('--source', choices=['codebase', 'all'], default='all',
                        help='What to index (default: all)')
    parser.add_argument('--model', default=None,
                        help='Embedding model (default: from env or all-MiniLM-L6-v2)')
    parser.add_argument('--progress', action='store_true', help='Show progress bar')
    parser.add_argument('--background', action='store_true', help='Run as background daemon')
    args = parser.parse_args()

    model_name = args.model or os.environ.get('MEMORY_EMBEDDING_MODEL', 'all-MiniLM-L6-v2')

    # Model dimensions lookup
    model_dims = {
        'all-MiniLM-L6-v2': 384,
        'bge-small-en-v1.5': 384,
        'bge-base-en-v1.5': 768,
        'all-mpnet-base-v2': 768,
        'bge-large-en-v1.5': 1024,
    }
    dims = model_dims.get(model_name, 384)

    if args.background:
        daemonize()

    print(f'[bulk-index] Starting: source={args.source}, model={model_name}, dims={dims}',
          file=sys.stderr)

    conn = get_conn()
    rotate_fn, codebook = load_quantization_params(conn, model_name, dims)
    if rotate_fn:
        print('[bulk-index] Quantization params loaded — will store quantized embeddings',
              file=sys.stderr)
    else:
        print('[bulk-index] No quantization params — will store float32 embeddings',
              file=sys.stderr)

    model = load_model(model_name)
    total = 0

    if args.source in ('codebase', 'all'):
        repos = load_codebase_config()
        if repos:
            total += index_codebase(conn, model, rotate_fn, codebook, repos, args.progress)

    conn.close()

    # Clean up PID file
    if args.background and PID_PATH.exists():
        PID_PATH.unlink(missing_ok=True)

    print(f'[bulk-index] Complete: {total} chunks indexed', file=sys.stderr)


if __name__ == '__main__':
    main()
