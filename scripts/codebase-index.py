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
import json
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

# Model config — dual-model architecture
# CodeRankEmbed for codebase (code-specialized), bge-base for memory (natural language)
CODEBASE_EMBEDDING_MODEL = 'nomic-ai/CodeRankEmbed'
CODEBASE_QUERY_PREFIX = 'Represent this query for searching relevant code: '
MEMORY_EMBEDDING_MODEL = 'BAAI/bge-base-en-v1.5'
DEFAULT_MODEL = os.environ.get('MEMORY_CODEBASE_MODEL', CODEBASE_EMBEDDING_MODEL)
MODEL_DIMS = {
    'all-MiniLM-L6-v2': 384,
    'bge-small-en-v1.5': 384,
    'bge-base-en-v1.5': 768,
    'all-mpnet-base-v2': 768,
    'bge-large-en-v1.5': 1024,
    'nomic-ai/CodeRankEmbed': 768,
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


def build_structural_prefix(file_path: str, title: str) -> str:
    """Construct structural context prefix for embedding input.

    Format: "{rel_path} | {title}\n"
    The file_path is expected to be like "codebase:name/rel/path.py" —
    we strip the "codebase:name/" prefix to get the relative path.
    """
    # Strip "codebase:<name>/" prefix to get relative path
    if '/' in file_path:
        rel_path = file_path.split('/', 1)[1] if ':' in file_path else file_path
    else:
        rel_path = file_path
    return f'{rel_path} | {title}\n'


def check_codebase_model(conn: sqlite3.Connection, model_name: str) -> bool:
    """Check if codebase model matches meta table. Returns True if reindex needed."""
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'codebase_embedding_model'"
        ).fetchone()
        if row:
            stored_model = row['value']
            if stored_model != model_name:
                print(
                    f'[codebase-index] Model changed: {stored_model} -> {model_name}. '
                    f'Full reindex required.',
                    file=sys.stderr,
                )
                return True
        return False
    except Exception:
        # meta table may not exist yet
        return False


def purge_all_codebase_chunks(conn: sqlite3.Connection) -> None:
    """Purge all codebase chunks and metadata for a fresh reindex."""
    print('[codebase-index] Purging all codebase chunks for model migration...', file=sys.stderr)
    # Delete FTS entries for codebase chunks
    try:
        conn.execute(
            "DELETE FROM chunks_fts WHERE rowid IN "
            "(SELECT rowid FROM chunks WHERE file_path LIKE 'codebase:%')"
        )
    except Exception:
        pass
    conn.execute("DELETE FROM chunks WHERE file_path LIKE 'codebase:%'")
    conn.execute('DELETE FROM codebase_meta')
    conn.commit()
    print('[codebase-index] Purge complete.', file=sys.stderr)


def write_codebase_model_meta(conn: sqlite3.Connection, model_name: str) -> None:
    """Write the codebase embedding model name to the meta table."""
    try:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('codebase_embedding_model', ?)",
            (model_name,),
        )
        conn.commit()
    except Exception as e:
        print(f'[codebase-index] Warning: could not write codebase model meta: {e}', file=sys.stderr)


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

    from quantize import quantize as quant_fn

    # Prepend structural context prefix for embedding input only (not stored content)
    texts = []
    for c in chunks:
        prefix = build_structural_prefix(c['file_path'], c['title'])
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
    # Add confidence column to edges table (idempotent migration)
    try:
        conn.execute('ALTER TABLE edges ADD COLUMN confidence REAL')
    except sqlite3.OperationalError:
        pass  # Column already exists
    conn.commit()


# Extensions that support dependency extraction
DEP_EXTENSIONS = {'.java', '.kt', '.py', '.ts'}


def index_dependencies(
    conn: sqlite3.Connection,
    name: str,
    repo_path: Path,
    incremental: bool = False,
) -> dict:
    """Extract imports, symbols, and type hierarchy from source files and store as edges/symbols."""
    from ast_parser import extract_imports, extract_symbols, extract_hierarchy
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
        lang = {'java': 'java', 'kt': 'kotlin', 'py': 'python', 'ts': 'typescript'}.get(fpath.suffix.lstrip('.'))
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

        # Extract type hierarchy (extends/implements/delegation)
        try:
            source_code = fpath.read_text(errors='replace')
            hierarchy = extract_hierarchy(str(fpath), source_code, lang)
        except Exception as e:
            print(f'[deps] Hierarchy parse failed {rel}: {e}', file=sys.stderr)
            hierarchy = []

        # Build import map for parent name resolution: simple_name -> FQN
        import_map: dict[str, str] = {}
        for imp in imports:
            fqn = imp['import_name']
            if not fqn.endswith('.*'):
                simple = fqn.rsplit('.', 1)[-1]
                import_map[simple] = fqn

        for hier in hierarchy:
            parent = hier['parent_name']
            # Try to resolve parent via import map
            parent_fqn = import_map.get(parent, parent)
            target = resolve_import(parent_fqn, repo_str, lang)
            conn.execute(
                'INSERT INTO edges (codebase, source_file, target_file, edge_type, metadata, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (name, rel, target, hier['relationship_type'], parent, now_ts),
            )
            total_edges += 1

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


def build_symbol_table(conn: sqlite3.Connection, codebase_name: str) -> dict[str, list[dict]]:
    """Load all symbols from the DB into the dict format the resolver expects.

    Returns: dict mapping symbol name -> list of {file_path, kind, start_line, end_line}
    """
    rows = conn.execute(
        'SELECT name, file_path, kind, start_line, end_line FROM symbols WHERE codebase = ?',
        (codebase_name,),
    ).fetchall()

    table: dict[str, list[dict]] = {}
    for r in rows:
        entry = {
            'file_path': r['file_path'],
            'kind': r['kind'],
            'start_line': r['start_line'],
            'end_line': r['end_line'],
        }
        table.setdefault(r['name'], []).append(entry)
    return table


def build_import_map(conn: sqlite3.Connection, codebase_name: str) -> dict[tuple[str, str], str]:
    """Load import edges and build (file_path, imported_name) -> target_file mapping.

    Only includes resolved imports (target_file IS NOT NULL).
    """
    rows = conn.execute(
        "SELECT source_file, target_file, metadata FROM edges "
        "WHERE codebase = ? AND edge_type IN ('import', 'static_import', 'wildcard_import') "
        "AND target_file IS NOT NULL",
        (codebase_name,),
    ).fetchall()

    imap: dict[tuple[str, str], str] = {}
    for r in rows:
        source = r['source_file']
        target = r['target_file']
        imported_name = r['metadata']  # metadata stores the import_name string
        if imported_name:
            # Store full import name
            imap[(source, imported_name)] = target
            # Also store just the last component (class/function name)
            short_name = imported_name.rsplit('.', 1)[-1]
            if short_name != '*':
                imap[(source, short_name)] = target
    return imap


def index_call_graph(
    conn: sqlite3.Connection,
    name: str,
    repo_path: Path,
    incremental: bool = False,
) -> dict:
    """Extract call sites and resolve them to target symbols.

    Orchestrates: extract call sites per file, run resolution cascade, store edges.
    """
    from ast_parser import extract_call_sites, extract_symbols
    from call_resolver import resolve_call_targets

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

    # Build symbol table and import map from DB
    print('[calls] Building symbol table and import map...', file=sys.stderr)
    symbol_table = build_symbol_table(conn, name)
    import_map = build_import_map(conn, name)
    print(f'[calls] {len(symbol_table)} symbols, {len(import_map)} import mappings', file=sys.stderr)

    now_ts = int(time.time())
    total_calls = 0
    total_resolved = 0
    total_unresolved = 0
    total_files = 0
    skipped = 0
    t0 = time.time()

    all_call_sites: list[dict] = []
    lang_map = {'java': 'java', 'kt': 'kotlin', 'py': 'python'}

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

        lang = lang_map.get(fpath.suffix.lstrip('.'))
        if not lang:
            continue

        # Delete old call edges for this file
        conn.execute(
            "DELETE FROM edges WHERE source_file = ? AND codebase = ? AND edge_type IN ('calls', 'calls_unresolved')",
            (rel, name),
        )

        # Extract symbols for this file (for caller identification)
        try:
            syms = extract_symbols(str(fpath))
        except Exception:
            syms = []

        # Read source and extract call sites
        try:
            source_code = fpath.read_bytes() if lang in ('java', 'kotlin') else fpath.read_text(errors='replace')
            calls = extract_call_sites(str(fpath), source_code, lang, syms)
        except Exception as e:
            print(f'[calls] Extraction failed {rel}: {e}', file=sys.stderr)
            calls = []

        # Normalize file_path in call sites to relative path
        for call in calls:
            call['file_path'] = rel

        all_call_sites.extend(calls)
        total_files += 1

        if total_files % 100 == 0:
            elapsed = time.time() - t0
            print(
                f'\r[calls] {total_files}/{len(dep_files)} files, '
                f'{len(all_call_sites)} call sites, {elapsed:.1f}s',
                end='', file=sys.stderr,
            )

    # Run resolution cascade on all collected call sites
    print(f'\n[calls] Resolving {len(all_call_sites)} call sites...', file=sys.stderr)
    resolved_edges = resolve_call_targets(all_call_sites, symbol_table, import_map)

    # Store edges
    for edge in resolved_edges:
        metadata_json = json.dumps(edge['metadata'])
        conn.execute(
            'INSERT INTO edges (codebase, source_file, target_file, edge_type, metadata, confidence, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (name, edge['source_file'], edge['target_file'], edge['edge_type'],
             metadata_json, edge['confidence'], now_ts),
        )
        total_calls += 1
        if edge['edge_type'] == 'calls':
            total_resolved += 1
        else:
            total_unresolved += 1

    conn.commit()
    elapsed = time.time() - t0
    print(f'[calls] Done: {total_files} files, {total_calls} call edges '
          f'({total_resolved} resolved, {total_unresolved} unresolved), '
          f'{skipped} skipped, {elapsed:.1f}s', file=sys.stderr)

    return {
        'files_processed': total_files,
        'call_edges': total_calls,
        'resolved': total_resolved,
        'unresolved': total_unresolved,
        'files_skipped': skipped,
        'elapsed_seconds': round(elapsed, 1),
    }


def resolve_hierarchy_edges(conn: sqlite3.Connection) -> dict:
    """Resolve unresolved hierarchy edges by matching parent names against the symbols table.

    Queries hierarchy edges (extends/implements/delegation) where target_file IS NULL,
    then looks up the parent name in metadata against symbols across all codebases.
    Updates target_file for matches found.
    """
    ensure_dep_tables(conn)

    # Find unresolved hierarchy edges
    unresolved = conn.execute(
        'SELECT id, metadata, codebase FROM edges '
        'WHERE target_file IS NULL AND edge_type IN (?, ?, ?)',
        ('extends', 'implements', 'delegation'),
    ).fetchall()

    if not unresolved:
        print('[resolve-hierarchy] No unresolved hierarchy edges found.', file=sys.stderr)
        return {'resolved': 0, 'unresolved': 0}

    resolved = 0
    still_unresolved = 0

    for row in unresolved:
        parent_name = row['metadata']
        edge_id = row['id']

        # Look up parent name in symbols table across all codebases
        sym = conn.execute(
            'SELECT file_path, codebase FROM symbols WHERE name = ? LIMIT 1',
            (parent_name,),
        ).fetchone()

        if sym:
            # Construct the full path as codebase:file_path
            target = f"codebase:{sym['codebase']}/{sym['file_path']}"
            conn.execute(
                'UPDATE edges SET target_file = ? WHERE id = ?',
                (target, edge_id),
            )
            resolved += 1
        else:
            still_unresolved += 1

    conn.commit()
    print(f'[resolve-hierarchy] Resolved {resolved}, still unresolved {still_unresolved}',
          file=sys.stderr)

    return {'resolved': resolved, 'unresolved': still_unresolved}


def main():
    parser = argparse.ArgumentParser(description='Index codebases for semantic search')
    parser.add_argument('--path', type=str, help='Path to repository root')
    parser.add_argument('--name', type=str, help='Codebase name')
    parser.add_argument('--update', action='store_true', help='Incremental update (only changed files)')
    parser.add_argument('--list', action='store_true', help='List indexed codebases')
    parser.add_argument('--remove', action='store_true', help='Remove a codebase (requires --name)')
    parser.add_argument('--model', type=str, default=DEFAULT_MODEL, help='Embedding model name')
    parser.add_argument('--deps', action='store_true', help='Extract dependency graph (imports + symbols + hierarchy)')
    parser.add_argument('--deps-only', action='store_true', help='Only extract dependencies, skip chunk embedding')
    parser.add_argument('--calls', action='store_true', help='Extract call graph (function-level call edges)')
    parser.add_argument('--resolve-hierarchy', action='store_true',
                        help='Resolve unresolved hierarchy edges against symbols table across all codebases')

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

    if args.resolve_hierarchy:
        result = resolve_hierarchy_edges(conn)
        print(f'\nHierarchy resolution result: {result}')
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
        dims = MODEL_DIMS.get(model_name, 768)

        # Check if model changed — if so, purge all codebase chunks and force full reindex
        needs_reindex = check_codebase_model(conn, model_name)
        if needs_reindex:
            purge_all_codebase_chunks(conn)
            # Force full reindex (disable incremental)
            args.update = False

        rotate_fn, codebook = load_quantization_params(conn, model_name, dims)
        model = load_model(model_name)

        result = index_codebase(
            conn, model, rotate_fn, codebook,
            args.name, repo_path,
            incremental=args.update,
        )
        print(f'\nChunk indexing result: {result}')

        # Record codebase embedding model in meta table
        write_codebase_model_meta(conn, model_name)

    # Dependency extraction pass
    if args.deps or args.deps_only:
        dep_result = index_dependencies(
            conn, args.name, repo_path,
            incremental=args.update,
        )
        print(f'\nDependency indexing result: {dep_result}')

    # Call graph extraction pass (runs after deps since it needs symbols/imports)
    if args.calls:
        call_result = index_call_graph(
            conn, args.name, repo_path,
            incremental=args.update,
        )
        print(f'\nCall graph result: {call_result}')

    conn.close()


if __name__ == '__main__':
    main()
