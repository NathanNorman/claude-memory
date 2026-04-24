#!/usr/bin/env python3
"""
Codebase indexer for unified-memory.

Indexes source code from a git repository into the unified-memory SQLite DB
with embeddings for semantic search. Supports full and incremental indexing.

Usage:
    python3 scripts/codebase-index.py --path ~/toast-analytics --name toast-analytics
    python3 scripts/codebase-index.py --path ~/toast-analytics --name toast-analytics --update
    python3 scripts/codebase-index.py --list
    python3 scripts/codebase-index.py --remove --name toast-analytics
"""

import argparse
import hashlib
import os
import sqlite3
import struct
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Add src/ to path for code_chunker and quantize
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import numpy as np

HOME = Path.home()
MEMORY_DIR = HOME / '.claude-memory'
DB_PATH = MEMORY_DIR / 'index' / 'memory.db'

# Extensions to index
SOURCE_EXTENSIONS = {
    '.py', '.java', '.kt', '.scala', '.sh', '.sql', '.js', '.ts', '.tf', '.md',
}

# Model config (matches unified_memory_server.py defaults)
DEFAULT_MODEL = os.environ.get('MEMORY_EMBEDDING_MODEL', 'bge-base-en-v1.5')
MODEL_DIMS = {
    'all-MiniLM-L6-v2': 384,
    'bge-small-en-v1.5': 384,
    'bge-base-en-v1.5': 768,
    'all-mpnet-base-v2': 768,
    'bge-large-en-v1.5': 1024,
}
MODEL_PREFIXES = {
    'bge-base-en-v1.5': 'BAAI/bge-base-en-v1.5',
    'bge-small-en-v1.5': 'BAAI/bge-small-en-v1.5',
    'bge-large-en-v1.5': 'BAAI/bge-large-en-v1.5',
}


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def get_db() -> sqlite3.Connection:
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
    # Add binary embedding column (nullable, metadata-only in SQLite)
    try:
        conn.execute('ALTER TABLE chunks ADD COLUMN embedding_binary BLOB')
    except sqlite3.OperationalError:
        pass  # Column already exists
    conn.commit()
    return conn


def load_model(model_name: str):
    from sentence_transformers import SentenceTransformer
    full_name = MODEL_PREFIXES.get(model_name, model_name)
    print(f'[codebase-index] Loading model: {full_name}', file=sys.stderr)
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


def discover_files(repo_path: Path) -> list[Path]:
    """Use git ls-files to discover source files, respecting .gitignore."""
    result = subprocess.run(
        ['git', 'ls-files'],
        capture_output=True, text=True, cwd=str(repo_path),
    )
    if result.returncode != 0:
        print(f'[codebase-index] git ls-files failed: {result.stderr}', file=sys.stderr)
        return []

    files = []
    for line in result.stdout.strip().split('\n'):
        if not line:
            continue
        p = repo_path / line
        if p.suffix in SOURCE_EXTENSIONS and p.is_file():
            files.append(p)
    return files


def embed_and_store_batch(
    conn: sqlite3.Connection,
    model,
    chunks: list[dict],
    rotate_fn,
    codebook,
    batch_size: int = 32,
) -> int:
    """Embed a batch of chunks and store in the database."""
    if not chunks:
        return 0

    from quantize import quantize as quant_fn, quantize_binary

    texts = [c['content'] for c in chunks]
    embeddings = model.encode(texts, normalize_embeddings=True, batch_size=batch_size)

    count = 0
    for chunk, emb in zip(chunks, embeddings):
        chunk_id = f"{chunk['file_path']}:{chunk['chunk_index']}"
        c_hash = content_hash(chunk['content'])

        emb_arr = np.array(emb, dtype=np.float32)

        # Binary embedding from unrotated normalized vector
        binary_blob = bytes(quantize_binary(emb_arr.reshape(1, -1))[0])

        if rotate_fn is not None and codebook is not None:
            blob = quant_fn(emb_arr, rotate_fn, codebook)
        else:
            dims = len(emb_arr)
            blob = struct.pack(f'{dims}f', *emb_arr.tolist())

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


def index_codebase(
    conn: sqlite3.Connection,
    model,
    rotate_fn,
    codebook,
    name: str,
    repo_path: Path,
    incremental: bool = False,
) -> dict:
    """Index a codebase into the chunks table."""
    from code_chunker import chunk_file

    files = discover_files(repo_path)
    if not files:
        return {'error': 'No source files found'}

    prefix = f'codebase:{name}/'
    now = datetime.now().isoformat()

    # Load existing hashes for incremental mode
    existing_hashes: dict[str, str] = {}
    if incremental:
        rows = conn.execute(
            'SELECT file_path, content_hash FROM codebase_meta WHERE codebase = ?',
            (name,),
        ).fetchall()
        existing_hashes = {r['file_path']: r['content_hash'] for r in rows}

    # Track which files we see this run (for deletion detection)
    seen_files: set[str] = set()

    total_chunks = 0
    total_files = 0
    skipped_files = 0
    batch: list[dict] = []
    batch_size = 32
    t0 = time.time()

    for i, fpath in enumerate(files):
        rel = str(fpath.relative_to(repo_path))
        seen_files.add(rel)

        # Compute hash
        try:
            fhash = file_hash(fpath)
        except Exception:
            continue

        # Skip unchanged files in incremental mode
        if incremental and existing_hashes.get(rel) == fhash:
            skipped_files += 1
            continue

        # Chunk the file
        try:
            chunks = chunk_file(str(fpath))
        except Exception as e:
            print(f'[codebase-index] Chunk failed {rel}: {e}', file=sys.stderr)
            continue

        # Delete old chunks for this file before re-indexing
        old_prefix = f'{prefix}{rel}'
        old_rows = conn.execute(
            'SELECT rowid FROM chunks WHERE file_path = ?', (old_prefix,)
        ).fetchall()
        for row in old_rows:
            try:
                conn.execute('DELETE FROM chunks_fts WHERE rowid = ?', (row['rowid'],))
            except Exception:
                pass
        conn.execute('DELETE FROM chunks WHERE file_path = ?', (old_prefix,))

        for ci, chunk in enumerate(chunks):
            batch.append({
                'file_path': f'{prefix}{rel}',
                'chunk_index': ci,
                'start_line': chunk['start_line'],
                'end_line': chunk['end_line'],
                'title': chunk['title'],
                'content': chunk['content'],
            })

        # Update codebase_meta
        conn.execute(
            'INSERT OR REPLACE INTO codebase_meta (codebase, file_path, content_hash, indexed_at) '
            'VALUES (?, ?, ?, ?)',
            (name, rel, fhash, now),
        )

        total_files += 1

        # Embed in batches
        if len(batch) >= batch_size:
            stored = embed_and_store_batch(conn, model, batch, rotate_fn, codebook)
            total_chunks += stored
            conn.commit()
            batch = []
            elapsed = time.time() - t0
            print(
                f'\r[codebase-index] {total_files}/{len(files)} files, '
                f'{total_chunks} chunks, {elapsed:.1f}s',
                end='', file=sys.stderr,
            )

    # Flush remaining batch
    if batch:
        stored = embed_and_store_batch(conn, model, batch, rotate_fn, codebook)
        total_chunks += stored

    # Remove chunks for deleted files (incremental mode)
    if incremental:
        deleted_files = set(existing_hashes.keys()) - seen_files
        for del_rel in deleted_files:
            del_path = f'{prefix}{del_rel}'
            old_rows = conn.execute(
                'SELECT rowid FROM chunks WHERE file_path = ?', (del_path,)
            ).fetchall()
            for row in old_rows:
                try:
                    conn.execute('DELETE FROM chunks_fts WHERE rowid = ?', (row['rowid'],))
                except Exception:
                    pass
            conn.execute('DELETE FROM chunks WHERE file_path = ?', (del_path,))
            conn.execute(
                'DELETE FROM codebase_meta WHERE codebase = ? AND file_path = ?',
                (name, del_rel),
            )
            print(f'[codebase-index] Removed deleted: {del_rel}', file=sys.stderr)

    conn.commit()
    elapsed = time.time() - t0
    print(f'\n[codebase-index] Done: {total_files} files, {total_chunks} chunks, '
          f'{skipped_files} skipped, {elapsed:.1f}s', file=sys.stderr)

    return {
        'files_indexed': total_files,
        'chunks_stored': total_chunks,
        'files_skipped': skipped_files,
        'elapsed_seconds': round(elapsed, 1),
    }


def list_codebases(conn: sqlite3.Connection) -> None:
    """List all indexed codebases."""
    rows = conn.execute(
        'SELECT codebase, COUNT(*) as file_count, MAX(indexed_at) as last_indexed '
        'FROM codebase_meta GROUP BY codebase ORDER BY codebase'
    ).fetchall()

    if not rows:
        print('No codebases indexed.')
        return

    print(f'{"Codebase":<30} {"Files":<10} {"Chunks":<10} {"Last Indexed"}')
    print('-' * 75)
    for r in rows:
        # Count chunks
        prefix = f'codebase:{r["codebase"]}/'
        chunk_row = conn.execute(
            'SELECT COUNT(*) as cnt FROM chunks WHERE file_path LIKE ?',
            (f'{prefix}%',),
        ).fetchone()
        chunk_count = chunk_row['cnt'] if chunk_row else 0
        print(f'{r["codebase"]:<30} {r["file_count"]:<10} {chunk_count:<10} {r["last_indexed"]}')


def remove_codebase(conn: sqlite3.Connection, name: str) -> None:
    """Remove all chunks and metadata for a codebase."""
    prefix = f'codebase:{name}/'

    # Delete FTS entries
    old_rows = conn.execute(
        'SELECT rowid FROM chunks WHERE file_path LIKE ?', (f'{prefix}%',)
    ).fetchall()
    for row in old_rows:
        try:
            conn.execute('DELETE FROM chunks_fts WHERE rowid = ?', (row['rowid'],))
        except Exception:
            pass

    # Delete chunks
    result = conn.execute('DELETE FROM chunks WHERE file_path LIKE ?', (f'{prefix}%',))
    chunk_count = result.rowcount

    # Delete metadata
    result = conn.execute('DELETE FROM codebase_meta WHERE codebase = ?', (name,))
    meta_count = result.rowcount

    conn.commit()
    print(f'Removed {name}: {chunk_count} chunks, {meta_count} file records deleted.')


def ensure_dep_tables(conn: sqlite3.Connection) -> None:
    """Create edges and symbols tables if they don't exist."""
    conn.execute('''
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codebase TEXT NOT NULL,
            source_file TEXT NOT NULL,
            target_file TEXT,
            edge_type TEXT NOT NULL,
            metadata TEXT,
            updated_at INTEGER NOT NULL
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_file, codebase)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_file, codebase)')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS symbols (
            id TEXT PRIMARY KEY,
            codebase TEXT NOT NULL,
            file_path TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name, codebase)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_path, codebase)')
    conn.commit()


# Extensions that support dependency extraction
DEP_EXTENSIONS = {'.java', '.kt', '.py'}


def index_dependencies(
    conn: sqlite3.Connection,
    name: str,
    repo_path: Path,
    incremental: bool = False,
) -> dict:
    """Extract imports and symbols from source files and store as edges/symbols."""
    from ast_parser import extract_imports, extract_symbols
    from import_resolver import resolve_import, clear_cache

    clear_cache()
    ensure_dep_tables(conn)

    files = discover_files(repo_path)
    dep_files = [f for f in files if f.suffix in DEP_EXTENSIONS]

    if not dep_files:
        return {'error': 'No parseable source files found'}

    # Load existing hashes for incremental mode
    existing_hashes: dict[str, str] = {}
    if incremental:
        rows = conn.execute(
            'SELECT file_path, content_hash FROM codebase_meta WHERE codebase = ?',
            (name,),
        ).fetchall()
        existing_hashes = {r['file_path']: r['content_hash'] for r in rows}

    now_ts = int(time.time())
    total_edges = 0
    total_symbols = 0
    total_files = 0
    skipped = 0
    t0 = time.time()
    repo_str = str(repo_path)

    for i, fpath in enumerate(dep_files):
        rel = str(fpath.relative_to(repo_path))

        # Compute hash and skip unchanged in incremental mode
        try:
            fhash = file_hash(fpath)
        except Exception:
            continue

        if incremental and existing_hashes.get(rel) == fhash:
            skipped += 1
            continue

        # Determine language from extension
        lang = {'java': 'java', 'kt': 'kotlin', 'py': 'python'}.get(fpath.suffix.lstrip('.'))
        if not lang:
            continue

        # Delete old edges/symbols for this file
        conn.execute('DELETE FROM edges WHERE source_file = ? AND codebase = ?', (rel, name))
        conn.execute('DELETE FROM symbols WHERE file_path = ? AND codebase = ?', (rel, name))

        # Extract and store imports as edges
        try:
            imports = extract_imports(str(fpath))
        except Exception as e:
            print(f'[deps] Import parse failed {rel}: {e}', file=sys.stderr)
            imports = []

        for imp in imports:
            target = resolve_import(imp['import_name'], repo_str, lang)
            conn.execute(
                'INSERT INTO edges (codebase, source_file, target_file, edge_type, metadata, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (name, rel, target, imp['import_type'], imp['import_name'], now_ts),
            )
            total_edges += 1

        # Extract and store symbols
        try:
            syms = extract_symbols(str(fpath))
        except Exception as e:
            print(f'[deps] Symbol parse failed {rel}: {e}', file=sys.stderr)
            syms = []

        for sym in syms:
            sym_id = f'{name}:{rel}::{sym["name"]}'
            conn.execute(
                'INSERT OR REPLACE INTO symbols (id, codebase, file_path, name, kind, start_line, end_line, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (sym_id, name, rel, sym['name'], sym['kind'], sym['start_line'], sym['end_line'], now_ts),
            )
            total_symbols += 1

        total_files += 1

        if total_files % 100 == 0:
            conn.commit()
            elapsed = time.time() - t0
            print(
                f'\r[deps] {total_files}/{len(dep_files)} files, '
                f'{total_edges} edges, {total_symbols} symbols, {elapsed:.1f}s',
                end='', file=sys.stderr,
            )

    conn.commit()
    elapsed = time.time() - t0
    print(f'\n[deps] Done: {total_files} files, {total_edges} edges, '
          f'{total_symbols} symbols, {skipped} skipped, {elapsed:.1f}s', file=sys.stderr)

    return {
        'files_processed': total_files,
        'edges_stored': total_edges,
        'symbols_stored': total_symbols,
        'files_skipped': skipped,
        'elapsed_seconds': round(elapsed, 1),
    }


def main():
    parser = argparse.ArgumentParser(description='Index codebases for semantic search')
    parser.add_argument('--path', type=str, help='Path to repository root')
    parser.add_argument('--name', type=str, help='Codebase name')
    parser.add_argument('--update', action='store_true', help='Incremental update (only changed files)')
    parser.add_argument('--list', action='store_true', help='List indexed codebases')
    parser.add_argument('--remove', action='store_true', help='Remove a codebase (requires --name)')
    parser.add_argument('--model', type=str, default=DEFAULT_MODEL, help='Embedding model name')
    parser.add_argument('--deps', action='store_true', help='Extract dependency graph (imports + symbols)')
    parser.add_argument('--deps-only', action='store_true', help='Only extract dependencies, skip chunk embedding')

    args = parser.parse_args()

    conn = get_db()

    if args.list:
        list_codebases(conn)
        conn.close()
        return

    if args.remove:
        if not args.name:
            parser.error('--remove requires --name')
        remove_codebase(conn, args.name)
        conn.close()
        return

    if not args.path or not args.name:
        parser.error('--path and --name are required for indexing')

    repo_path = Path(args.path).expanduser().resolve()
    if not repo_path.is_dir():
        print(f'Error: {repo_path} is not a directory', file=sys.stderr)
        sys.exit(1)

    # Chunk embedding pass (skip if --deps-only)
    if not args.deps_only:
        model_name = args.model
        dims = MODEL_DIMS.get(model_name, 384)
        rotate_fn, codebook = load_quantization_params(conn, model_name, dims)
        model = load_model(model_name)

        result = index_codebase(
            conn, model, rotate_fn, codebook,
            args.name, repo_path,
            incremental=args.update,
        )
        print(f'\nChunk indexing result: {result}')

    # Dependency extraction pass
    if args.deps or args.deps_only:
        dep_result = index_dependencies(
            conn, args.name, repo_path,
            incremental=args.update,
        )
        print(f'\nDependency indexing result: {dep_result}')

    conn.close()


if __name__ == '__main__':
    main()
