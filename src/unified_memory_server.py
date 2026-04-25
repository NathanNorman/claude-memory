#!/usr/bin/env python3
"""
Unified Memory MCP Server

Combines flat text search (SQLite FTS5) with local vector search
(sentence-transformers, 384-dim all-MiniLM-L6-v2) for hybrid retrieval.
A single MCP server for persistent memory across Claude Code sessions.

Architecture:
  - Flat backend: Opens claude-memory's SQLite DB, runs FTS5 keyword search
  - Vector backend: Brute-force cosine similarity over pre-computed embeddings
  - Hybrid retrieval: RRF merge of keyword + vector results
  - Graceful degradation: Each backend can fail independently
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import signal
import sqlite3
import struct
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

# ──────────────────────────────────────────────────────────────
# Constants & Logging
# ──────────────────────────────────────────────────────────────

HOME = Path.home()
MEMORY_DIR = HOME / '.claude-memory'
DB_PATH = MEMORY_DIR / 'index' / 'memory.db'
ARCHIVE_DIR = HOME / '.claude' / 'projects'
CONV_PREFIX = 'conversations/'
PLUGINS_JSON = HOME / '.claude' / 'plugins' / 'installed_plugins.json'
SKILLS_DIR = HOME / '.claude' / 'skills'
KNOWN_SOURCE_FILTERS = {'curated', 'conversations', 'codebase'}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [unified-memory] %(levelname)s %(message)s',
    stream=sys.stderr,
)
log = logging.getLogger('unified-memory')


# ──────────────────────────────────────────────────────────────
# Section 0: Addon Discovery
# ──────────────────────────────────────────────────────────────

# Module-level addon state (populated by discover_addon_dbs + init_addon_backends)
addon_backends: dict[str, dict] = {}  # source_name -> {'flat': FlatSearchBackend, 'vector': VectorSearchBackend, 'db_path': Path}
_addon_warmup_done = threading.Event()


def _check_addon_model(db_path: Path, expected_model: str) -> bool:
    """Check if an addon DB's embedding model matches the expected model.

    Returns True if compatible, False if mismatched or unreadable.
    """
    try:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'embedding_model'"
            ).fetchone()
        finally:
            conn.close()
        if not row:
            log.warning(f'Skipping addon {db_path.name}: no embedding_model in meta table')
            return False
        db_model = row['value']
        # Meta may store with prefix like 'Xenova/' — strip it
        db_model_short = db_model.split('/')[-1] if '/' in db_model else db_model
        if db_model_short != expected_model:
            log.warning(
                f'Skipping addon {db_path.name}: model mismatch '
                f'({db_model_short} != {expected_model})'
            )
            return False
        return True
    except Exception as e:
        log.warning(f'Skipping addon {db_path.name}: cannot read meta table: {e}')
        return False


def discover_addon_dbs(expected_model: str) -> dict[str, Path]:
    """Discover addon .db files from plugins and local skills.

    Returns a dict mapping source name to DB path.
    Precedence: local skills > plugins (local shadows plugin on collision).
    """
    import glob as globmod

    addons: dict[str, Path] = {}

    # 1. Plugins (lower precedence)
    if PLUGINS_JSON.exists():
        try:
            with open(PLUGINS_JSON) as f:
                data = json.load(f)
            for key, entries in data.get('plugins', {}).items():
                plugin_name = key.split('@')[0]  # "toast-analytics@marketplace" -> "toast-analytics"
                for entry in entries:
                    install_path = entry.get('installPath', '')
                    if not install_path or not Path(install_path).is_dir():
                        continue
                    for db_file in globmod.glob(
                        str(Path(install_path) / '**' / '*.db'), recursive=True
                    ):
                        db_path = Path(db_file)
                        source_name = f'{plugin_name}:{db_path.stem}'
                        if _check_addon_model(db_path, expected_model):
                            addons[source_name] = db_path
                            log.info(f'Discovered plugin addon: {source_name} -> {db_path}')
        except Exception as e:
            log.warning(f'Failed to read installed_plugins.json: {e}')

    # 2. Local skills (higher precedence — shadows plugins on stem collision)
    if SKILLS_DIR.is_dir():
        for db_file in globmod.glob(
            str(SKILLS_DIR / '**' / '*.db'), recursive=True
        ):
            db_path = Path(db_file)
            source_name = db_path.stem
            if _check_addon_model(db_path, expected_model):
                # Remove shadowed plugin entries
                for existing_key in list(addons.keys()):
                    if existing_key.endswith(f':{source_name}'):
                        log.info(
                            f'Local skill {source_name} shadows plugin {existing_key}'
                        )
                        del addons[existing_key]
                addons[source_name] = db_path
                log.info(f'Discovered local addon: {source_name} -> {db_path}')

    return addons


def init_addon_backends(discovered: dict[str, Path]) -> None:
    """Initialize FlatSearchBackend + VectorSearchBackend for each addon DB.

    Populates the module-level addon_backends dict.
    """
    global addon_backends
    for source_name, db_path in discovered.items():
        try:
            flat = FlatSearchBackend(db_path, readonly=True)
            flat._ensure_conn()
            vec = VectorSearchBackend(db_path)
            vec._ensure_index()
            addon_backends[source_name] = {
                'flat': flat,
                'vector': vec,
                'db_path': db_path,
            }
            log.info(f'Addon backend ready: {source_name}')
        except Exception as e:
            log.warning(f'Failed to init addon {source_name}: {e}')


# ──────────────────────────────────────────────────────────────
# Section 1: FlatSearchBackend — SQLite FTS5 keyword search
# ──────────────────────────────────────────────────────────────


class FlatSearchBackend:
    """Keyword search over the claude-memory SQLite index.

    Opens the existing memory.db (created/maintained by the Node.js indexer)
    and performs FTS5 keyword searches. Also handles minimal FTS5 updates
    after memory_write operations.
    """

    def __init__(self, db_path: Path, readonly: bool = False):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._readonly = readonly

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            if not self.db_path.exists():
                raise FileNotFoundError(f'Memory database not found: {self.db_path}')
            self._conn = sqlite3.connect(str(self.db_path), timeout=10.0)
            self._conn.execute('PRAGMA busy_timeout = 5000')
            self._conn.execute('PRAGMA journal_mode = WAL')
            self._conn.row_factory = sqlite3.Row
            if not self._readonly:
                self._ensure_codebase_meta_table()
        return self._conn

    def _ensure_codebase_meta_table(self) -> None:
        """Create codebase_meta table for tracking indexed codebase files."""
        self._conn.execute('''
            CREATE TABLE IF NOT EXISTS codebase_meta (
                codebase TEXT NOT NULL,
                file_path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                indexed_at TEXT NOT NULL,
                PRIMARY KEY (codebase, file_path)
            )
        ''')
        self._conn.commit()
        self._ensure_dep_tables()

    def _ensure_dep_tables(self) -> None:
        """Create edges and symbols tables for dependency graph."""
        self._conn.execute('''
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
        self._conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_edges_source
            ON edges(source_file, codebase)
        ''')
        self._conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_edges_target
            ON edges(target_file, codebase)
        ''')
        self._conn.execute('''
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
        self._conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_symbols_name
            ON symbols(name, codebase)
        ''')
        self._conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_symbols_file
            ON symbols(file_path, codebase)
        ''')
        # Composite indexes for graph traversal (direction + edge_type filtering)
        self._conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_edges_target_type
            ON edges(target_file, edge_type, codebase)
        ''')
        self._conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_edges_source_type
            ON edges(source_file, edge_type, codebase)
        ''')
        # Add metadata column to symbols table for LLM labels (idempotent migration)
        try:
            self._conn.execute('ALTER TABLE symbols ADD COLUMN metadata TEXT')
        except sqlite3.OperationalError:
            pass  # Column already exists
        # Communities table for Louvain clustering results
        self._conn.execute('''
            CREATE TABLE IF NOT EXISTS communities (
                codebase TEXT NOT NULL,
                file_path TEXT NOT NULL,
                community_id INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (codebase, file_path)
            )
        ''')
        self._conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_communities_codebase_community
            ON communities(codebase, community_id)
        ''')
        # Track edge count at community computation time
        self._conn.execute('''
            CREATE TABLE IF NOT EXISTS community_meta (
                codebase TEXT PRIMARY KEY,
                edge_count INTEGER NOT NULL,
                community_count INTEGER NOT NULL,
                computed_at INTEGER NOT NULL
            )
        ''')
        self._conn.execute('ANALYZE')
        self._conn.commit()

    def resolve_start_node(self, start_node: str, codebase: str = '') -> list[str]:
        """Resolve a start_node to file path(s) for graph traversal.

        If start_node contains '/' or looks like a file path (has a file extension),
        treat as a file path literal. Otherwise, treat as a symbol name and look up
        in the symbols table.
        """
        if '/' in start_node or re.search(r'\.\w{1,4}$', start_node):
            return [start_node]

        conn = self._ensure_conn()
        query = 'SELECT DISTINCT file_path FROM symbols WHERE name = ?'
        params: list = [start_node]
        if codebase:
            query += ' AND codebase = ?'
            params.append(codebase)

        rows = conn.execute(query, params).fetchall()
        if not rows:
            raise ValueError(f'No symbol found matching "{start_node}"')
        return [row['file_path'] for row in rows]

    def upsert_codebase_meta(self, codebase: str, file_path: str, content_hash: str) -> None:
        """Upsert a row into codebase_meta for the given codebase and file."""
        conn = self._ensure_conn()
        conn.execute(
            '''INSERT INTO codebase_meta (codebase, file_path, content_hash, indexed_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT (codebase, file_path) DO UPDATE SET
                   content_hash = excluded.content_hash,
                   indexed_at = excluded.indexed_at''',
            (codebase, file_path, content_hash, datetime.utcnow().isoformat()),
        )
        conn.commit()

    def get_codebase_meta(self, codebase: str) -> list[dict]:
        """Return all codebase_meta rows for a codebase as dicts."""
        conn = self._ensure_conn()
        rows = conn.execute(
            'SELECT file_path, content_hash, indexed_at FROM codebase_meta WHERE codebase = ?',
            (codebase,),
        ).fetchall()
        return [dict(row) for row in rows]

    def delete_codebase_chunks(self, codebase: str) -> None:
        """Delete all chunks, codebase_meta rows, and FTS entries for a codebase."""
        conn = self._ensure_conn()
        prefix = f'codebase:{codebase}/%'
        # Delete from FTS index (content-synced, needs explicit delete)
        conn.execute(
            "DELETE FROM chunks_fts WHERE rowid IN (SELECT rowid FROM chunks WHERE file_path LIKE ?)",
            (prefix,),
        )
        # Delete main chunks
        conn.execute('DELETE FROM chunks WHERE file_path LIKE ?', (prefix,))
        # Delete tracking metadata
        conn.execute('DELETE FROM codebase_meta WHERE codebase = ?', (codebase,))
        conn.commit()

    # --- Search primitives (ported from hybrid.ts / search.ts) ---

    @staticmethod
    def build_fts_query(query: str) -> str:
        """Sanitize query for FTS5 MATCH: tokenize, quote, join with OR.

        Port of buildFtsQuery() from hybrid.ts.
        """
        tokens = re.findall(r'[A-Za-z0-9_]+', query)
        if not tokens:
            return ''
        return ' OR '.join(f'"{t}"' for t in tokens)

    @staticmethod
    def bm25_rank_to_score(rank: float) -> float:
        """Convert negative BM25 rank to 0-1 score.

        FTS5 bm25() returns negative values where more negative = better.
        Formula: 1 / (1 - rank), so rank=-5 -> 1/6 ~ 0.167.
        Port of bm25RankToScore() from hybrid.ts.
        """
        try:
            if not isinstance(rank, (int, float)):
                return 0.0
            return 1.0 / (1.0 - rank)
        except (ZeroDivisionError, OverflowError):
            return 0.0

    @staticmethod
    def merge_rrf(hits_a: list[dict], hits_b: list[dict], k: int = 60) -> list[dict]:
        """Reciprocal Rank Fusion merge of two result lists.

        RRF scores each result as 1/(k + rank) summed across retrieval systems.
        k=60 is the standard constant from the original RRF paper.
        Port of mergeHybridResults() from hybrid.ts.
        """
        by_id: dict[str, dict] = {}

        for rank, r in enumerate(hits_a):
            by_id[r['id']] = {
                'result': r,
                'rrf_score': 1.0 / (k + rank + 1),
            }

        for rank, r in enumerate(hits_b):
            rid = r['id']
            if rid in by_id:
                by_id[rid]['rrf_score'] += 1.0 / (k + rank + 1)
            else:
                by_id[rid] = {
                    'result': r,
                    'rrf_score': 1.0 / (k + rank + 1),
                }

        merged = []
        for v in by_id.values():
            entry = dict(v['result'])
            entry['score'] = v['rrf_score']
            merged.append(entry)
        merged.sort(key=lambda x: x['score'], reverse=True)
        return merged

    def search_keyword(self, query: str, limit: int) -> list[dict]:
        """FTS5 keyword search returning scored chunk dicts.

        Port of searchKeyword() from search.ts.
        """
        fts_query = self.build_fts_query(query)
        if not fts_query or limit <= 0:
            return []

        conn = self._ensure_conn()
        try:
            rows = conn.execute(
                'SELECT rowid, rank FROM chunks_fts '
                'WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?',
                (fts_query, limit),
            ).fetchall()
        except sqlite3.OperationalError as e:
            log.warning(f'FTS5 search failed: {e}')
            return []

        results = []
        for row in rows:
            chunk = conn.execute(
                'SELECT id, file_path, chunk_index, start_line, end_line, '
                'title, content FROM chunks WHERE rowid = ?',
                (row['rowid'],),
            ).fetchone()
            if chunk:
                results.append({
                    'id': chunk['id'],
                    'file_path': chunk['file_path'],
                    'chunk_index': chunk['chunk_index'],
                    'start_line': chunk['start_line'],
                    'end_line': chunk['end_line'],
                    'title': chunk['title'],
                    'content': chunk['content'],
                    'score': self.bm25_rank_to_score(row['rank']),
                })
        return results

    # --- File / UUID helpers ---

    def get_file_summary(self, file_path: str) -> Optional[str]:
        conn = self._ensure_conn()
        row = conn.execute(
            'SELECT summary FROM files WHERE file_path = ?', (file_path,)
        ).fetchone()
        return row['summary'] if row and row['summary'] else None

    def resolve_uuid(self, uuid: str) -> Optional[str]:
        """Look up a conversation file path by session UUID."""
        conn = self._ensure_conn()
        row = conn.execute(
            'SELECT file_path FROM files WHERE file_path LIKE ?', (f'%{uuid}%',)
        ).fetchone()
        return row['file_path'] if row else None

    def get_stats(self) -> dict:
        """Get database statistics."""
        try:
            conn = self._ensure_conn()
            chunks = conn.execute('SELECT COUNT(*) as cnt FROM chunks').fetchone()['cnt']
            files = conn.execute('SELECT COUNT(*) as cnt FROM files').fetchone()['cnt']
            return {'status': 'ok', 'chunks': chunks, 'files': files}
        except Exception as e:
            return {'status': 'error', 'error': str(e)}

    # --- Write support: minimal FTS5 update after memory_write ---

    def index_written_file(self, file_path_relative: str, full_path: Path) -> None:
        """Update FTS5 index for a single curated memory file after write.

        Creates simple chunks split by ## headings and inserts into
        chunks + chunks_fts tables. Skips vec0 (no local embeddings in v1).
        """
        conn = self._ensure_conn()
        content = full_path.read_text()
        if not content.strip():
            return

        # Preserve existing summary before replacing
        existing_summary = None
        try:
            row = conn.execute(
                'SELECT summary FROM files WHERE file_path = ?',
                (file_path_relative,),
            ).fetchone()
            if row:
                existing_summary = row['summary']
        except Exception:
            pass

        # Delete old chunks for this file
        old_rows = conn.execute(
            'SELECT rowid FROM chunks WHERE file_path = ?', (file_path_relative,)
        ).fetchall()
        for row in old_rows:
            try:
                conn.execute(
                    'DELETE FROM chunks_fts WHERE rowid = ?', (row['rowid'],)
                )
            except Exception:
                pass
        conn.execute(
            'DELETE FROM chunks WHERE file_path = ?', (file_path_relative,)
        )

        # Create chunks by splitting on ## headings
        chunks = self._chunk_markdown(content, file_path_relative)

        for i, chunk in enumerate(chunks):
            chunk_id = f'{file_path_relative}:{i}'
            content_hash = hashlib.sha256(chunk['content'].encode()).hexdigest()[:16]

            conn.execute(
                'INSERT INTO chunks (id, file_path, chunk_index, start_line, '
                'end_line, title, content, embedding, hash, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)',
                (
                    chunk_id, file_path_relative, i,
                    chunk['start_line'], chunk['end_line'],
                    chunk['title'], chunk['content'],
                    content_hash, int(datetime.now().timestamp() * 1000),
                ),
            )

            row = conn.execute(
                'SELECT rowid FROM chunks WHERE id = ?', (chunk_id,)
            ).fetchone()
            if row:
                try:
                    conn.execute(
                        'INSERT INTO chunks_fts(rowid, content, title) '
                        'VALUES (?, ?, ?)',
                        (row['rowid'], chunk['content'], chunk['title']),
                    )
                except Exception:
                    pass

        # Update files table (preserve existing summary)
        file_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        conn.execute(
            'INSERT OR REPLACE INTO files '
            '(file_path, content_hash, last_indexed, chunk_count, summary) '
            'VALUES (?, ?, ?, ?, ?)',
            (
                file_path_relative, file_hash,
                int(datetime.now().timestamp() * 1000),
                len(chunks), existing_summary,
            ),
        )
        conn.commit()

    @staticmethod
    def _chunk_markdown(content: str, file_path: str) -> list[dict]:
        """Split markdown by ## headings into chunks."""
        lines = content.split('\n')
        chunks: list[dict] = []
        current_title = file_path
        current_lines: list[str] = []
        start_line = 1

        for i, line in enumerate(lines, 1):
            if line.startswith('## ') and current_lines:
                chunks.append({
                    'title': current_title,
                    'content': '\n'.join(current_lines),
                    'start_line': start_line,
                    'end_line': i - 1,
                })
                current_title = line.lstrip('#').strip()
                current_lines = [line]
                start_line = i
            else:
                current_lines.append(line)

        if current_lines:
            chunks.append({
                'title': current_title,
                'content': '\n'.join(current_lines),
                'start_line': start_line,
                'end_line': len(lines),
            })

        return chunks or [{
            'title': file_path,
            'content': content,
            'start_line': 1,
            'end_line': len(lines),
        }]

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


# ──────────────────────────────────────────────────────────────
# Section 1b: VectorSearchBackend — sqlite-vec + sentence-transformers
# ──────────────────────────────────────────────────────────────


class VectorSearchBackend:
    """Local vector similarity search using numpy + sentence-transformers.

    Supports two storage modes:
    - float32: Raw 384-dim embeddings (legacy, from Node.js indexer)
    - quantized: TurboQuant-style 4-bit packed vectors (post-migration)

    Mixed-mode is supported: float32 BLOBs are detected by size and
    converted to quantized on-the-fly during index load.

    Search uses two-stage approach when quantized:
    1. Approximate search via quantized dot products (all vectors)
    2. Exact reranking of top-30 candidates from float32 matrix
    This achieves recall@10 ≥ 0.998 with 8x storage compression.
    """

    DEFAULT_MODEL = 'bge-base-en-v1.5'
    DEFAULT_BIT_WIDTH = 4
    RERANK_K = 30  # Candidates for exact reranking

    # Dual-model architecture: nomic-embed-text-v1.5 for codebase, bge-base for memory
    CODEBASE_EMBEDDING_MODEL = 'nomic-ai/nomic-embed-text-v1.5'
    CODEBASE_QUERY_PREFIX = 'search_query: '

    # Model name → native embedding dimensions (before Matryoshka truncation)
    MODEL_DIMS = {
        'all-MiniLM-L6-v2': 384,
        'bge-small-en-v1.5': 384,
        'bge-base-en-v1.5': 768,
        'all-mpnet-base-v2': 768,
        'bge-large-en-v1.5': 1024,
        'nomic-ai/CodeRankEmbed': 768,
        'nomic-ai/nomic-embed-text-v1.5': 768,
    }

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._model = None
        self._codebase_model = None  # Lazy-loaded nomic-embed-text-v1.5 for codebase queries
        self._init_failed = False
        self._codebase_stored_dims: int = 0  # Auto-detected from BLOB size

        # Configurable model via env var (for memory/conversation)
        self.MODEL_NAME = os.environ.get('MEMORY_EMBEDDING_MODEL', self.DEFAULT_MODEL)
        self.EMBEDDING_DIMS = self.MODEL_DIMS.get(self.MODEL_NAME, 384)
        # In-memory embedding index (lazy-loaded)
        self._rowids: Optional[list[int]] = None
        self._matrix = None  # numpy array (N x dims), normalized float32
        # Quantization state (loaded from quantization_meta if available)
        self._quantized = False
        self._packed_list: Optional[list[bytes]] = None
        self._rotate_fn = None
        self._inv_rotate_fn = None
        self._codebook = None
        # Binary (1-bit) quantization state for Hamming coarse pass
        self._binary_matrix = None   # numpy uint8 array (N, packed_dims)
        self._binary_available = False

    def _ensure_conn(self) -> Optional[sqlite3.Connection]:
        if self._conn is not None:
            return self._conn
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=10.0)
            conn.execute('PRAGMA busy_timeout = 5000')
            conn.execute('PRAGMA journal_mode = WAL')
            conn.row_factory = sqlite3.Row
            self._ensure_quantization_table(conn)
            self._conn = conn
            return conn
        except Exception as e:
            log.warning(f'Vector backend connection failed: {e}')
            self._init_failed = True
            return None

    @staticmethod
    def _ensure_quantization_table(conn: sqlite3.Connection) -> None:
        """Create quantization_meta table if it doesn't exist (backward compatible)."""
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
        # Add binary embedding column (nullable, metadata-only in SQLite)
        try:
            conn.execute('ALTER TABLE chunks ADD COLUMN embedding_binary BLOB')
        except sqlite3.OperationalError:
            pass  # Column already exists

    def _load_quantization_params(self, conn: sqlite3.Connection) -> bool:
        """Load rotation + codebook from quantization_meta if available."""
        try:
            row = conn.execute(
                'SELECT dims, bit_width, rotation_seed, codebook '
                'FROM quantization_meta WHERE model_name = ? '
                'ORDER BY created_at DESC LIMIT 1',
                (self.MODEL_NAME,),
            ).fetchone()
            if not row:
                return False

            import numpy as np
            from quantize import generate_rotation

            dims = row['dims']
            bit_width = row['bit_width']
            seed = row['rotation_seed']
            codebook_blob = row['codebook']
            n_centroids = 1 << bit_width
            codebook = np.array(
                struct.unpack(f'{n_centroids}f', codebook_blob),
                dtype=np.float32,
            )

            fwd, inv = generate_rotation(dims, seed)
            self._rotate_fn = fwd
            self._inv_rotate_fn = inv
            self._codebook = codebook
            log.info(
                f'Quantization params loaded: {dims}d, {bit_width}-bit, '
                f'seed={seed}, {n_centroids} centroids'
            )
            return True
        except Exception as e:
            log.warning(f'Failed to load quantization params: {e}')
            return False

    def _check_model_version(self, conn: sqlite3.Connection) -> None:
        """Check if configured models match what's in the meta table."""
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'embedding_model'"
            ).fetchone()
            if row:
                db_model = row['value']
                # Meta stores with prefix like 'Xenova/' — strip it
                db_model_short = db_model.split('/')[-1] if '/' in db_model else db_model
                if db_model_short != self.MODEL_NAME:
                    log.warning(
                        f'Memory model mismatch: DB has {db_model}, configured {self.MODEL_NAME}. '
                        f'Run a full reindex to update embeddings.'
                    )
        except Exception:
            pass  # meta table may not exist yet

        # Check codebase model separately (does NOT trigger memory reindex)
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'codebase_embedding_model'"
            ).fetchone()
            if row:
                cb_model = row['value']
                if cb_model != self.CODEBASE_EMBEDDING_MODEL:
                    log.warning(
                        f'Codebase model mismatch: DB has {cb_model}, '
                        f'configured {self.CODEBASE_EMBEDDING_MODEL}. '
                        f'Run codebase-index.py to reindex codebase chunks.'
                    )
        except Exception:
            pass

        # Auto-detect codebase stored dimension from meta table or BLOB size
        try:
            dims_row = conn.execute(
                "SELECT value FROM meta WHERE key = 'codebase_embedding_dims'"
            ).fetchone()
            if dims_row:
                self._codebase_stored_dims = int(dims_row['value'])
                log.info(f'Codebase stored dimension: {self._codebase_stored_dims}d')
            else:
                # Fallback: detect from first codebase BLOB size
                blob_row = conn.execute(
                    "SELECT embedding FROM chunks WHERE file_path LIKE 'codebase:%' "
                    "AND embedding IS NOT NULL LIMIT 1"
                ).fetchone()
                if blob_row and blob_row['embedding']:
                    blob_len = len(blob_row['embedding'])
                    self._codebase_stored_dims = blob_len // 4  # float32 = 4 bytes
                    log.info(
                        f'Codebase dimension auto-detected from BLOB: '
                        f'{self._codebase_stored_dims}d ({blob_len} bytes)'
                    )
        except Exception:
            pass

    def _ensure_index(self) -> bool:
        """Load all embeddings from chunks table into memory.

        For quantized DBs: loads only packed bytes (no dequantization).
        Dequantization happens on-demand during reranking (top-30 only).
        For float32 DBs: builds full matrix as before.

        Builds:
        - self._packed_list: packed quantized bytes for approximate search
        - self._matrix: float32 matrix ONLY for float32 embeddings (legacy)
        - self._quantized: True if any quantized embeddings found
        """
        if self._rowids is not None:
            return True
        conn = self._ensure_conn()
        if conn is None:
            return False
        try:
            import numpy as np
            from quantize import (
                quantize as quant_fn, packed_size,
            )

            self._check_model_version(conn)

            # Try loading quantization params
            has_quant = self._load_quantization_params(conn)
            float32_size = self.EMBEDDING_DIMS * 4
            quant_size = packed_size(self.EMBEDDING_DIMS, self.DEFAULT_BIT_WIDTH) if has_quant else 0

            rows = conn.execute(
                'SELECT rowid, embedding, embedding_binary FROM chunks '
                'WHERE embedding IS NOT NULL'
            ).fetchall()

            valid_rowids = []
            valid_embeddings = []  # Only used for float32 (legacy)
            packed_list = [] if has_quant else None
            binary_blobs = []     # Collected binary embeddings
            n_float32 = 0
            n_quantized = 0
            n_binary = 0
            n_missing_binary = 0

            for row in rows:
                blob = row['embedding']
                if not blob:
                    continue

                if len(blob) == float32_size:
                    # Float32 embedding — need full decode
                    vec = np.array(
                        struct.unpack(f'{self.EMBEDDING_DIMS}f', blob),
                        dtype=np.float32,
                    )
                    norm = np.linalg.norm(vec)
                    if norm > 0:
                        vec = vec / norm
                    valid_embeddings.append(vec)
                    valid_rowids.append(row['rowid'])
                    if has_quant:
                        packed_list.append(
                            quant_fn(vec, self._rotate_fn, self._codebook)
                        )
                    n_float32 += 1

                elif has_quant and len(blob) == quant_size:
                    # Quantized embedding — store packed bytes only (no dequantize)
                    valid_rowids.append(row['rowid'])
                    packed_list.append(blob)
                    n_quantized += 1

                # Track binary embedding
                bin_blob = row['embedding_binary']
                if bin_blob:
                    binary_blobs.append(np.frombuffer(bin_blob, dtype=np.uint8))
                    n_binary += 1
                else:
                    n_missing_binary += 1

            if not valid_rowids:
                log.warning('Vector backend: no valid embeddings found')
                return False

            self._rowids = valid_rowids
            # Only build float32 matrix if we have float32 vectors
            if valid_embeddings:
                self._matrix = np.array(valid_embeddings, dtype=np.float32)
            if has_quant and packed_list:
                self._packed_list = packed_list
                self._quantized = True

            # Build binary matrix only if every vector has a binary embedding
            if n_binary > 0 and n_missing_binary == 0:
                self._binary_matrix = np.array(binary_blobs, dtype=np.uint8)
                self._binary_available = True
                log.info(
                    f'Binary matrix loaded: {n_binary} vectors, '
                    f'{self._binary_matrix.shape[1]} packed bytes each'
                )
            else:
                self._binary_matrix = None
                self._binary_available = False
                if n_binary > 0:
                    log.info(
                        f'Binary matrix skipped: incomplete coverage '
                        f'({n_binary} present, {n_missing_binary} missing)'
                    )

            mode = 'quantized' if self._quantized else 'float32'
            log.info(
                f'Vector index loaded: {len(valid_rowids)} embeddings '
                f'({n_float32} float32, {n_quantized} quantized, mode={mode})'
            )
            return True
        except Exception as e:
            log.warning(f'Vector index load failed: {e}')
            return False

    def _ensure_model(self):
        """Lazy-load the sentence-transformers model on first query."""
        if self._model is not None:
            return self._model
        if self._init_failed:
            return None
        try:
            from sentence_transformers import SentenceTransformer

            # Resolve short model names to full HuggingFace repo IDs
            _MODEL_REPOS = {
                'bge-base-en-v1.5': 'BAAI/bge-base-en-v1.5',
                'bge-small-en-v1.5': 'BAAI/bge-small-en-v1.5',
                'bge-large-en-v1.5': 'BAAI/bge-large-en-v1.5',
            }
            repo_id = _MODEL_REPOS.get(self.MODEL_NAME, self.MODEL_NAME)
            self._model = SentenceTransformer(repo_id)
            log.info(f'Vector backend: loaded {repo_id} model')
            return self._model
        except Exception as e:
            log.warning(f'Vector backend model load failed: {e}')
            self._init_failed = True
            return None

    def _ensure_codebase_model(self):
        """Lazy-load nomic-embed-text-v1.5 for codebase queries on first call."""
        if self._codebase_model is not None:
            return self._codebase_model
        if self._init_failed:
            return None
        try:
            from sentence_transformers import SentenceTransformer
            repo_id = self.CODEBASE_EMBEDDING_MODEL
            self._codebase_model = SentenceTransformer(repo_id)
            log.info(f'Vector backend: loaded {repo_id} codebase model')
            return self._codebase_model
        except Exception as e:
            log.warning(f'Codebase model load failed: {e}')
            return None

    def search_codebase(self, query: str, limit: int) -> list[dict]:
        """Vector search for codebase queries using nomic-embed-text-v1.5 + query prefix.

        Uses the codebase-specific model with asymmetric query prefix.
        Falls back to the default model if the codebase model is unavailable.
        """
        if self._init_failed or limit <= 0:
            return []

        if not self._ensure_index():
            return []

        # Try codebase model first, fall back to default model
        model = self._ensure_codebase_model()
        if model is None:
            model = self._ensure_model()
        if model is None:
            return []

        import numpy as np
        from quantize import batch_quantized_dot_products

        # Apply asymmetric query prefix
        prefixed_query = self.CODEBASE_QUERY_PREFIX + query
        query_vec = model.encode(prefixed_query, normalize_embeddings=True)
        query_vec = np.array(query_vec, dtype=np.float32).flatten()

        # Matryoshka: truncate query embedding to match stored dimension
        if self._codebase_stored_dims > 0 and len(query_vec) > self._codebase_stored_dims:
            query_vec = query_vec[:self._codebase_stored_dims]
            norm = np.linalg.norm(query_vec)
            if norm > 0:
                query_vec = query_vec / norm

        if self._quantized and self._packed_list:
            from quantize import dequantize

            query_rotated = self._rotate_fn(query_vec)
            approx_sims = batch_quantized_dot_products(
                query_rotated, self._packed_list, self._codebook,
                self.EMBEDDING_DIMS,
            )
            rerank_k = max(self.RERANK_K, limit * 3)
            rerank_k = min(rerank_k, len(approx_sims))
            candidate_indices = np.argsort(approx_sims)[-rerank_k:]

            candidate_vecs = []
            for ci in candidate_indices:
                deq = dequantize(
                    self._packed_list[ci], self._inv_rotate_fn,
                    self._codebook, self.EMBEDDING_DIMS,
                )
                norm = np.linalg.norm(deq)
                if norm > 0:
                    deq = deq / norm
                candidate_vecs.append(deq)
            candidate_matrix = np.array(candidate_vecs, dtype=np.float32)

            exact_sims = candidate_matrix @ query_vec
            top_within = np.argsort(exact_sims)[-limit:][::-1]
            top_indices = [candidate_indices[j] for j in top_within]
            similarities = np.array([exact_sims[j] for j in top_within])
        else:
            query_vec_2d = query_vec.reshape(1, -1)
            all_sims = (self._matrix @ query_vec_2d.T).flatten()
            top_k = min(limit, len(all_sims))
            top_indices = np.argpartition(all_sims, -top_k)[-top_k:]
            top_indices = top_indices[np.argsort(all_sims[top_indices])[::-1]]
            similarities = all_sims[top_indices]

        conn = self._ensure_conn()
        if conn is None:
            return []

        results = []
        for idx, score in zip(top_indices, similarities):
            score = float(score)
            if score <= 0:
                continue
            rowid = self._rowids[idx]
            chunk = conn.execute(
                'SELECT id, file_path, chunk_index, start_line, end_line, '
                'title, content FROM chunks WHERE rowid = ?',
                (rowid,),
            ).fetchone()
            if chunk:
                results.append({
                    'id': chunk['id'],
                    'file_path': chunk['file_path'],
                    'chunk_index': chunk['chunk_index'],
                    'start_line': chunk['start_line'],
                    'end_line': chunk['end_line'],
                    'title': chunk['title'],
                    'content': chunk['content'],
                    'score': score,
                })
        return results

    # Candidate counts for three-stage pipeline
    BINARY_TOP_K = 1000   # Stage 1: binary Hamming → top candidates
    TURBOQUANT_TOP_K = 50  # Stage 2: TurboQuant → top candidates

    def search(self, query: str, limit: int) -> list[dict]:
        """Vector similarity search with optional three-stage acceleration.

        If binary + quantized index available: three-stage search
          1. Binary Hamming distance over all vectors → top 1000
          2. TurboQuant 4-bit dot products on 1000 → top 50
          3. Exact float32 reranking on 50 → top k
        If only quantized index available: two-stage search (TurboQuant + exact)
        Otherwise: brute-force cosine similarity (legacy path).
        """
        if self._init_failed or limit <= 0:
            return []

        if not self._ensure_index():
            return []

        model = self._ensure_model()
        if model is None:
            return []

        import numpy as np
        from quantize import batch_quantized_dot_products

        # Embed and normalize query
        query_vec = model.encode(query, normalize_embeddings=True)
        query_vec = np.array(query_vec, dtype=np.float32).flatten()

        if self._binary_available and self._quantized and self._packed_list:
            # Three-stage pipeline: binary → TurboQuant → float32
            from quantize import dequantize, quantize_binary, hamming_distance

            # Stage 1: Binary Hamming coarse pass
            binary_query = quantize_binary(query_vec)
            distances = hamming_distance(binary_query, self._binary_matrix)
            stage1_k = min(self.BINARY_TOP_K, len(distances))
            # argpartition for top-k smallest distances
            if stage1_k < len(distances):
                stage1_indices = np.argpartition(distances, stage1_k)[:stage1_k]
            else:
                stage1_indices = np.arange(len(distances))

            # Stage 2: TurboQuant dot products on Stage 1 candidates
            query_rotated = self._rotate_fn(query_vec)
            stage1_packed = [self._packed_list[i] for i in stage1_indices]
            approx_sims = batch_quantized_dot_products(
                query_rotated, stage1_packed, self._codebook,
                self.EMBEDDING_DIMS,
            )
            stage2_k = min(self.TURBOQUANT_TOP_K, len(approx_sims))
            stage2_local = np.argsort(approx_sims)[-stage2_k:]
            stage2_indices = stage1_indices[stage2_local]

            # Stage 3: Exact float32 reranking via dequantization
            candidate_vecs = []
            for ci in stage2_indices:
                deq = dequantize(
                    self._packed_list[ci], self._inv_rotate_fn,
                    self._codebook, self.EMBEDDING_DIMS,
                )
                norm = np.linalg.norm(deq)
                if norm > 0:
                    deq = deq / norm
                candidate_vecs.append(deq)
            candidate_matrix = np.array(candidate_vecs, dtype=np.float32)

            exact_sims = candidate_matrix @ query_vec
            top_within = np.argsort(exact_sims)[-limit:][::-1]
            top_indices = [stage2_indices[j] for j in top_within]
            similarities = np.array([exact_sims[j] for j in top_within])

        elif self._quantized and self._packed_list:
            # Two-stage quantized search (fallback when binary not available)
            from quantize import dequantize

            query_rotated = self._rotate_fn(query_vec)
            approx_sims = batch_quantized_dot_products(
                query_rotated, self._packed_list, self._codebook,
                self.EMBEDDING_DIMS,
            )
            rerank_k = max(self.RERANK_K, limit * 3)
            rerank_k = min(rerank_k, len(approx_sims))
            candidate_indices = np.argsort(approx_sims)[-rerank_k:]

            # Dequantize only the top-K candidates for exact reranking
            candidate_vecs = []
            for ci in candidate_indices:
                deq = dequantize(
                    self._packed_list[ci], self._inv_rotate_fn,
                    self._codebook, self.EMBEDDING_DIMS,
                )
                norm = np.linalg.norm(deq)
                if norm > 0:
                    deq = deq / norm
                candidate_vecs.append(deq)
            candidate_matrix = np.array(candidate_vecs, dtype=np.float32)

            exact_sims = candidate_matrix @ query_vec
            top_within = np.argsort(exact_sims)[-limit:][::-1]
            top_indices = [candidate_indices[j] for j in top_within]
            similarities = np.array([exact_sims[j] for j in top_within])
        else:
            # Legacy float32 brute-force
            query_vec_2d = query_vec.reshape(1, -1)
            all_sims = (self._matrix @ query_vec_2d.T).flatten()
            top_k = min(limit, len(all_sims))
            top_indices = np.argpartition(all_sims, -top_k)[-top_k:]
            top_indices = top_indices[np.argsort(all_sims[top_indices])[::-1]]
            similarities = all_sims[top_indices]

        conn = self._ensure_conn()
        if conn is None:
            return []

        results = []
        for idx, score in zip(top_indices, similarities):
            score = float(score)
            if score <= 0:
                continue
            rowid = self._rowids[idx]
            chunk = conn.execute(
                'SELECT id, file_path, chunk_index, start_line, end_line, '
                'title, content FROM chunks WHERE rowid = ?',
                (rowid,),
            ).fetchone()
            if chunk:
                results.append({
                    'id': chunk['id'],
                    'file_path': chunk['file_path'],
                    'chunk_index': chunk['chunk_index'],
                    'start_line': chunk['start_line'],
                    'end_line': chunk['end_line'],
                    'title': chunk['title'],
                    'content': chunk['content'],
                    'score': score,
                })
        return results

    def embed_written_chunks(self, file_path_relative: str) -> int:
        """Generate embeddings for newly written chunks and invalidate cache.

        Stores quantized BLOBs if quantization is configured, else float32.
        """
        conn = self._ensure_conn()
        if conn is None:
            return 0

        model = self._ensure_model()
        if model is None:
            return 0

        rows = conn.execute(
            'SELECT rowid, content FROM chunks '
            'WHERE file_path = ? AND embedding IS NULL',
            (file_path_relative,),
        ).fetchall()

        if not rows:
            return 0

        texts = [row['content'] for row in rows]
        try:
            embeddings = model.encode(texts, normalize_embeddings=True)
        except Exception as e:
            log.warning(f'Embedding generation failed: {e}')
            return 0

        # Check if quantization is configured
        has_quant = self._codebook is not None and self._rotate_fn is not None
        if not has_quant:
            has_quant = self._load_quantization_params(conn)

        count = 0
        for row, emb in zip(rows, embeddings):
            import numpy as np
            emb_arr = np.array(emb, dtype=np.float32)

            # Binary embedding from unrotated normalized vector
            from quantize import quantize_binary
            binary_blob = bytes(quantize_binary(emb_arr.reshape(1, -1))[0])

            if has_quant:
                from quantize import quantize as quant_fn
                blob = quant_fn(emb_arr, self._rotate_fn, self._codebook)
            else:
                blob = struct.pack(f'{self.EMBEDDING_DIMS}f', *emb_arr.tolist())

            conn.execute(
                'UPDATE chunks SET embedding = ?, embedding_binary = ? WHERE rowid = ?',
                (blob, binary_blob, row['rowid']),
            )
            count += 1

        conn.commit()

        # Invalidate in-memory index so next search reloads
        self._invalidate_index()

        log.info(f'Embedded {count} chunks for {file_path_relative} '
                 f'({"quantized" if has_quant else "float32"})')
        return count

    def _invalidate_index(self) -> None:
        """Clear in-memory index so it reloads on next search."""
        self._matrix = None
        self._rowids = None
        self._packed_list = None
        self._quantized = False
        self._binary_matrix = None
        self._binary_available = False

    def get_stats(self) -> dict:
        """Get vector index statistics."""
        conn = self._ensure_conn()
        if conn is None:
            return {'status': 'unavailable'}
        try:
            row = conn.execute(
                'SELECT count(*) as cnt FROM chunks '
                'WHERE embedding IS NOT NULL'
            ).fetchone()
            result = {
                'status': 'ok',
                'vectors': row['cnt'],
                'model': self.MODEL_NAME,
                'dims': self.EMBEDDING_DIMS,
            }
            # Check if quantization is active
            qrow = conn.execute(
                'SELECT bit_width FROM quantization_meta '
                'WHERE model_name = ? LIMIT 1',
                (self.MODEL_NAME,),
            ).fetchone()
            if qrow:
                result['quantized'] = True
                result['bit_width'] = qrow['bit_width']
            # Binary vector stats
            result['binary_available'] = self._binary_available
            try:
                brow = conn.execute(
                    'SELECT count(*) as cnt FROM chunks '
                    'WHERE embedding_binary IS NOT NULL'
                ).fetchone()
                result['binary_vectors'] = brow['cnt']
            except Exception:
                result['binary_vectors'] = 0
            return result
        except Exception as e:
            return {'status': 'error', 'error': str(e)}

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        self._invalidate_index()
        self._rotate_fn = None
        self._inv_rotate_fn = None
        self._codebook = None
        self._codebase_model = None


# ──────────────────────────────────────────────────────────────
# Section 1c: GraphSidecar — igraph in-process graph queries
# ──────────────────────────────────────────────────────────────


class GraphSidecar:
    """In-process igraph graph for fast BFS/DFS traversal.

    Loads edges from SQLite into an igraph directed graph on startup.
    Falls back gracefully if igraph is unavailable. Supports:
    - Codebase-scoped or all-codebase loading
    - Memory-bounded loading (max_edges cap)
    - Atomic rebuild via SIGHUP or staleness detection
    - Edge type filtering during traversal
    """

    MAX_EDGES = int(os.environ.get('GRAPH_MAX_EDGES', '5000000'))
    STALENESS_THRESHOLD = 0.10  # 10% edge count drift triggers rebuild

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._graph = None  # igraph.Graph
        self._node_index: dict[str, int] = {}  # file_path -> vertex id
        self._edge_count_at_load: int = 0
        self._codebase: Optional[str] = None  # None = all codebases
        self._loaded = False
        self._load_time: float = 0.0

    def load(self, codebase: Optional[str] = None) -> bool:
        """Load edges from SQLite into igraph. Returns True on success."""
        try:
            import igraph as ig
        except ImportError:
            log.warning('igraph not installed — graph traversal will use CTE fallback')
            return False

        self._codebase = codebase
        t0 = time.time()

        try:
            conn = sqlite3.connect(str(self.db_path), timeout=10.0)
            conn.execute('PRAGMA busy_timeout = 5000')
            conn.row_factory = sqlite3.Row

            query = (
                'SELECT source_file, target_file, edge_type, metadata '
                'FROM edges WHERE target_file IS NOT NULL'
            )
            params: list = []
            if codebase:
                query += ' AND codebase = ?'
                params.append(codebase)
            query += ' ORDER BY updated_at DESC LIMIT ?'
            params.append(self.MAX_EDGES)

            rows = conn.execute(query, params).fetchall()

            # Count total edges for staleness detection
            count_query = 'SELECT COUNT(*) as cnt FROM edges WHERE target_file IS NOT NULL'
            count_params: list = []
            if codebase:
                count_query += ' AND codebase = ?'
                count_params.append(codebase)
            total_edges = conn.execute(count_query, count_params).fetchone()['cnt']
            conn.close()

            if not rows:
                log.info('GraphSidecar: no edges to load')
                self._loaded = False
                return False

            if total_edges > self.MAX_EDGES:
                log.warning(
                    'GraphSidecar: edge count %d exceeds limit %d, '
                    'loading most recent %d edges',
                    total_edges, self.MAX_EDGES, self.MAX_EDGES,
                )

            # Build igraph graph
            node_set: set[str] = set()
            edge_list: list[tuple[str, str]] = []
            edge_attrs: dict[str, list] = {'edge_type': [], 'metadata': []}

            for row in rows:
                src, tgt = row['source_file'], row['target_file']
                node_set.add(src)
                node_set.add(tgt)
                edge_list.append((src, tgt))
                edge_attrs['edge_type'].append(row['edge_type'])
                edge_attrs['metadata'].append(row['metadata'])

            node_list = sorted(node_set)
            self._node_index = {name: idx for idx, name in enumerate(node_list)}

            g = ig.Graph(directed=True)
            g.add_vertices(len(node_list))
            g.vs['name'] = node_list

            indexed_edges = [
                (self._node_index[src], self._node_index[tgt])
                for src, tgt in edge_list
            ]
            g.add_edges(indexed_edges)
            g.es['edge_type'] = edge_attrs['edge_type']
            g.es['metadata'] = edge_attrs['metadata']

            self._graph = g
            self._edge_count_at_load = total_edges
            self._loaded = True
            self._load_time = time.time() - t0

            log.info(
                'GraphSidecar loaded: %d nodes, %d edges in %.2fs%s',
                g.vcount(), g.ecount(), self._load_time,
                f' (codebase={codebase})' if codebase else ' (all codebases)',
            )
            return True

        except Exception as e:
            log.warning('GraphSidecar load failed: %s', e)
            self._loaded = False
            return False

    def rebuild(self, codebase: Optional[str] = None) -> bool:
        """Atomic rebuild: build new graph, swap reference."""
        old_graph = self._graph
        old_index = self._node_index
        cb = codebase if codebase is not None else self._codebase
        success = self.load(cb)
        if not success:
            # Restore old graph on failure
            self._graph = old_graph
            self._node_index = old_index
            self._loaded = old_graph is not None
        return success

    def is_stale(self) -> bool:
        """Check if edge count has drifted >10% since last load."""
        if not self._loaded:
            return False
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=5.0)
            conn.execute('PRAGMA busy_timeout = 3000')
            query = 'SELECT COUNT(*) as cnt FROM edges WHERE target_file IS NOT NULL'
            params: list = []
            if self._codebase:
                query += ' AND codebase = ?'
                params.append(self._codebase)
            current = conn.execute(query, params).fetchone()[0]
            conn.close()
            if self._edge_count_at_load == 0:
                return current > 0
            drift = abs(current - self._edge_count_at_load) / self._edge_count_at_load
            return drift > self.STALENESS_THRESHOLD
        except Exception:
            return False

    def traverse(
        self,
        start_file: str,
        direction: str = 'downstream',
        edge_types: Optional[list[str]] = None,
        max_depth: int = 5,
        max_results: int = 100,
        include_paths: bool = False,
    ) -> list[dict]:
        """BFS traversal using igraph. Returns list of {file, depth, [path]}."""
        if not self._loaded or self._graph is None:
            return []

        if start_file not in self._node_index:
            return []

        import igraph as ig

        g = self._graph
        start_vid = self._node_index[start_file]

        # Filter edges by type if requested
        if edge_types:
            edge_mask = [e['edge_type'] in edge_types for e in g.es]
            subgraph_edges = [i for i, keep in enumerate(edge_mask) if keep]
            g = g.subgraph_edges(subgraph_edges)
            # Rebuild node index for subgraph
            node_index = {v['name']: v.index for v in g.vs}
            if start_file not in node_index:
                return []
            start_vid = node_index[start_file]
        else:
            node_index = self._node_index

        mode = ig.OUT if direction == 'downstream' else ig.IN

        # BFS
        bfs_result = g.bfs(start_vid, mode=mode)
        order = bfs_result[0]  # vertex visit order
        layers = bfs_result[1]  # layer boundaries

        # Build depth map from layers
        depth_map: dict[int, int] = {}
        for depth, layer_start in enumerate(layers):
            if depth + 1 < len(layers):
                layer_end = layers[depth + 1]
            else:
                layer_end = len(order)
            for i in range(layer_start, layer_end):
                vid = order[i]
                if vid >= 0 and vid != start_vid:
                    depth_map[vid] = depth

        results = []
        for vid, depth in sorted(depth_map.items(), key=lambda x: x[1]):
            if depth > max_depth or depth == 0:
                continue
            if len(results) >= max_results:
                break
            node_name = g.vs[vid]['name']
            entry: dict[str, Any] = {
                'file': node_name,
                'depth': depth,
                'start': start_file,
            }
            if include_paths:
                try:
                    paths = g.get_all_shortest_paths(start_vid, to=vid, mode=mode)
                    if paths:
                        path_names = [g.vs[v]['name'] for v in paths[0]]
                        entry['path'] = ' -> '.join(path_names)
                except Exception:
                    pass
            results.append(entry)

        return results

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def has_codebase(self, codebase: str) -> bool:
        """Check if a specific codebase's nodes are present in the graph."""
        if not self._loaded:
            return False
        # If loaded with a specific codebase, only that one is present
        if self._codebase:
            return self._codebase == codebase
        # Loaded all codebases — check if any node exists
        # (heuristic: check the edge count query)
        return True

    def get_stats(self) -> dict:
        if not self._loaded or self._graph is None:
            return {'status': 'not_loaded'}
        return {
            'status': 'loaded',
            'nodes': self._graph.vcount(),
            'edges': self._graph.ecount(),
            'codebase': self._codebase or 'all',
            'load_time_seconds': round(self._load_time, 2),
            'edge_count_at_load': self._edge_count_at_load,
        }


# Module-level graph sidecar (initialized at startup)
graph_sidecar: Optional[GraphSidecar] = None


# ──────────────────────────────────────────────────────────────
# Section 2: Post-filtering helpers (ported from tools.ts)
# ──────────────────────────────────────────────────────────────

_home_parts = str(HOME).split(os.sep)
_HOME_USER = (
    _home_parts[-1].replace('.', '-') if len(_home_parts) >= 2 else ''
)


def normalize_project(raw: str) -> str:
    """Normalize a project string for comparison.

    Strips home-dir prefixes like -Users-nathan-norman-.
    Port of normalizeProject() from tools.ts.
    """
    s = raw
    if _HOME_USER:
        s = re.sub(
            r'^-*Users-' + re.escape(_HOME_USER) + r'-',
            '', s, flags=re.IGNORECASE,
        )
    else:
        s = re.sub(r'^-*Users-[^/\\-]+-', '', s, flags=re.IGNORECASE)
    return re.sub(r'^[-/\\]+', '', s).lower()


def parse_chunk_title(title: str) -> dict[str, Optional[str]]:
    """Parse metadata from chunk title.

    Exchange titles use format: "projectDir | date | Tools: X, Y"
    Port of parseChunkTitle() from tools.ts.
    """
    segments = [s.strip() for s in title.split(' | ')]
    project: Optional[str] = None
    date: Optional[str] = None

    for seg in segments:
        if seg.startswith('Tools: '):
            continue
        if re.match(r'^\d{4}-\d{2}-\d{2}$', seg):
            date = seg
        elif seg and not project:
            project = seg
    return {'project': project, 'date': date}


def date_from_path(fp: str) -> Optional[str]:
    """Extract YYYY-MM-DD date from a file path."""
    m = re.search(r'(\d{4}-\d{2}-\d{2})', fp)
    return m.group(1) if m else None


def smart_truncate(text: str, max_len: int = 800) -> str:
    """Truncate at nearest paragraph/sentence/word boundary.

    Port of smartTruncate() from tools.ts.
    """
    if len(text) <= max_len:
        return text
    s = text[:max_len]
    # Try paragraph break
    p = s.rfind('\n\n')
    if p > max_len * 0.6:
        return s[:p]
    # Try sentence break
    sent = max(s.rfind('. '), s.rfind('.\n'))
    if sent > max_len * 0.6:
        return s[:sent + 1]
    # Try word break
    w = s.rfind(' ')
    if w > max_len * 0.6:
        return s[:w]
    return s


# ──────────────────────────────────────────────────────────────
# Section 4: Path validation & conversation parser
# ──────────────────────────────────────────────────────────────

UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)


def validate_read_path(p: str) -> Path:
    """Validate and resolve a read path within ~/.claude-memory/."""
    n = os.path.normpath(p)
    if '..' in n:
        raise ValueError(f'Path traversal not allowed: {p}')
    full = (MEMORY_DIR / n).resolve()
    if not str(full).startswith(str(MEMORY_DIR.resolve())):
        raise ValueError(f'Path must be within ~/.claude-memory/: {p}')
    return full


def validate_write_path(f: str) -> Path:
    """Validate and resolve a write path (MEMORY.md or memory/*.md only)."""
    n = os.path.normpath(f)
    if '..' in n:
        raise ValueError(f'Path traversal not allowed: {f}')
    if n == 'MEMORY.md' or n.startswith('memory/') or n.startswith('memory\\'):
        if not n.endswith('.md'):
            raise ValueError(f'File must end with .md: {f}')
        return (MEMORY_DIR / n).resolve()
    raise ValueError(f'File must be MEMORY.md or memory/*.md: {f}')


# --- Conversation JSONL parser (for memory_read UUID support) ---

_SKIP_TYPES = {'progress', 'queue-operation', 'file-history-snapshot'}
_SKIP_BLOCKS = {'tool_use', 'tool_result', 'thinking'}


def _extract_msg_text(message: Optional[dict]) -> Optional[str]:
    """Extract plain text from a JSONL message content field."""
    if not message:
        return None
    content = message.get('content')
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get('type') in _SKIP_BLOCKS:
                continue
            if block.get('type') == 'text' and isinstance(block.get('text'), str):
                t = block['text'].strip()
                if t:
                    parts.append(t)
        return '\n\n'.join(parts) if parts else None
    return None


def parse_conversation(absolute_path: str) -> Optional[dict]:
    """Parse a conversation JSONL file into structured exchanges.

    Port of parseConversationExchanges() from conversation-parser.ts.
    """
    fp = Path(absolute_path)
    if not fp.exists():
        return None
    try:
        size = fp.stat().st_size
    except OSError:
        return None
    if size > 20 * 1024 * 1024 or size == 0:
        return None

    session_id = cwd = timestamp = None
    exchanges: list[dict] = []
    cur_user = ''
    cur_assistant: list[str] = []
    has_user = False

    def flush():
        nonlocal cur_user, cur_assistant, has_user
        if has_user and cur_user.strip():
            exchanges.append({
                'user': cur_user.strip(),
                'assistant': '\n\n'.join(cur_assistant).strip(),
            })
        cur_user = ''
        cur_assistant = []
        has_user = False

    with open(absolute_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            rt = rec.get('type')
            if not rt or rt in _SKIP_TYPES:
                continue
            if not session_id:
                session_id = rec.get('sessionId')
            if not cwd:
                cwd = rec.get('cwd')
            if not timestamp:
                timestamp = rec.get('timestamp')
            msg = rec.get('message', {})
            if rt == 'user':
                flush()
                cur_user = _extract_msg_text(msg) or ''
                has_user = True
            elif rt in ('assistant', 'summary'):
                text = _extract_msg_text(msg)
                if text:
                    cur_assistant.append(text)
    flush()

    if not exchanges:
        return None
    return {
        'session_id': session_id,
        'cwd': cwd,
        'timestamp': timestamp,
        'exchanges': exchanges,
    }


# ──────────────────────────────────────────────────────────────
# Section 5: MCP Server & Tool Definitions
# ──────────────────────────────────────────────────────────────

mcp_app = FastMCP(
    'unified-memory',
    instructions=(
        'Unified memory system combining flat text search (SQLite FTS5) with '
        'vector similarity search (sentence-transformers). Use memory_search for '
        'finding relevant past context, memory_read for reading specific files '
        'or conversation sessions by UUID, memory_write for persisting new '
        'knowledge, and get_status to check backend health.'
    ),
)

# Backends (initialized at startup)
flat_backend: Optional[FlatSearchBackend] = None
vector_backend: Optional[VectorSearchBackend] = None


def _search_addon(
    query: str, source: str, max_results: int, min_score: float
) -> dict:
    """Run hybrid search against an addon database and return results."""
    backends = addon_backends[source]
    flat: FlatSearchBackend = backends['flat']
    vec: VectorSearchBackend = backends['vector']
    fetch_limit = max_results * 3

    # Keyword search
    flat_hits = flat.search_keyword(query, fetch_limit * 2)

    # Vector search
    vector_hits: list[dict] = []
    try:
        vector_hits = vec.search(query, fetch_limit * 2)
    except Exception as e:
        log.warning(f'Addon vector search failed for {source}: {e}')

    # RRF merge
    if vector_hits:
        merged = FlatSearchBackend.merge_rrf(flat_hits, vector_hits)
    else:
        merged = flat_hits

    # Format results
    results = []
    for r in merged[:max_results]:
        score = r.get('score', 0)
        if score < min_score:
            continue
        results.append({
            'path': r['file_path'],
            'title': r.get('title', ''),
            'snippet': smart_truncate(r.get('content', ''), 800),
            'score': round(score, 3),
            'startLine': r.get('start_line'),
            'endLine': r.get('end_line'),
            'source': source,
        })

    return {'results': results}


@mcp_app.tool()
async def memory_search(
    query: str,
    maxResults: int = 10,
    minScore: float = 0,
    after: str = '',
    before: str = '',
    project: str = '',
    source: str = '',
) -> dict:
    """Search memory for relevant content using hybrid keyword+vector retrieval.

    Combines FTS5 keyword search with vector similarity search over indexed
    memory files and conversation archives. Results merged via Reciprocal
    Rank Fusion (RRF) for best of both precision and recall.

    Args:
        query: Search query text
        maxResults: Maximum results to return (default 10)
        minScore: Minimum relevance score 0-1 (default 0)
        after: Filter: only results after this date (YYYY-MM-DD)
        before: Filter: only results before this date (YYYY-MM-DD)
        project: Filter: only results from this project directory
        source: Filter: "curated" for memory files only, "conversations" for session history only, addon source name for addon databases, empty for primary only. Note: after/before/project filters are ignored for addon sources.
    """
    # --- Addon source routing: route to addon DB exclusively ---
    if source and source not in KNOWN_SOURCE_FILTERS:
        # Wait for addon discovery to complete before checking (up to 30s)
        _addon_warmup_done.wait(timeout=30)
        if source in addon_backends:
            return _search_addon(query, source, maxResults, minScore)
        return {'error': f'Unknown source: {source}', 'results': []}

    if not flat_backend:
        return {'error': 'Flat search backend not available', 'results': []}

    has_filters = bool(after or before or project or source)
    fetch_limit = maxResults * (5 if has_filters else 3)

    # Flat keyword search — synchronous but fast (<50ms typically)
    flat_original = flat_backend.search_keyword(query, fetch_limit * 2)

    # Vector similarity search — synchronous, first call loads model (~2s), then fast
    # Use codebase model with query prefix when searching codebase source
    vector_hits: list[dict] = []
    if vector_backend:
        try:
            if source == 'codebase':
                vector_hits = vector_backend.search_codebase(query, fetch_limit * 2)
            else:
                vector_hits = vector_backend.search(query, fetch_limit * 2)
        except Exception as e:
            log.warning(f'Vector search failed: {e}')

    # Merge keyword + vector via RRF
    if vector_hits:
        merged = FlatSearchBackend.merge_rrf(flat_original, vector_hits)
    else:
        merged = flat_original

    # Post-filter (ported from tools.ts handleMemorySearch)
    norm_project = normalize_project(project) if project else ''
    summary_cache: dict[str, Optional[str]] = {}
    session_counts: dict[str, int] = {}
    filtered: list[dict] = []

    for r in merged:
        if len(filtered) >= maxResults:
            break
        if r.get('score', 0) < minScore:
            continue

        fp = r['file_path']
        is_conv = fp.startswith(CONV_PREFIX)
        is_codebase = fp.startswith('codebase:')

        # Source filter
        if source == 'codebase' and not is_codebase:
            continue
        if source == 'curated' and (is_conv or is_codebase):
            continue
        if source == 'conversations' and not is_conv:
            continue

        # Extract metadata
        entry_project: Optional[str] = None
        entry_date: Optional[str] = None
        if is_conv:
            meta = parse_chunk_title(r.get('title', ''))
            entry_project = meta.get('project')
            entry_date = meta.get('date')
        else:
            entry_date = date_from_path(fp)

        # Date filter
        if after and (not entry_date or entry_date < after):
            continue
        if before and (not entry_date or entry_date > before):
            continue

        # Project filter (conversations only; curated passes through)
        if norm_project and is_conv:
            if not entry_project or norm_project not in normalize_project(entry_project):
                continue

        # Session dedup: max 2 results per conversation file
        if is_conv:
            cnt = session_counts.get(fp, 0)
            if cnt >= 2:
                continue
            session_counts[fp] = cnt + 1

        # Build result entry
        entry: dict[str, Any] = {
            'path': (
                fp.replace('conversations/', '').replace('.jsonl', '')
                .split('/')[-1]
                if is_conv
                else fp
            ),
            'score': round(r.get('score', 0), 3),
            'snippet': smart_truncate(r.get('content', ''), 800),
        }

        if not is_conv:
            entry['startLine'] = r.get('start_line')
            entry['endLine'] = r.get('end_line')

        if is_codebase:
            entry['title'] = r.get('title', '')
            entry['source'] = 'codebase'

        if is_conv:
            if entry_project:
                entry['project'] = normalize_project(entry_project)
            if entry_date:
                entry['date'] = entry_date
            # Summary lookup (cached per file)
            if fp not in summary_cache:
                summary_cache[fp] = flat_backend.get_file_summary(fp)
            summary = summary_cache[fp]
            if summary:
                entry['summary'] = (
                    summary[:200] + ('...' if len(summary) > 200 else '')
                )

        filtered.append(entry)

    return {'results': filtered}


@mcp_app.tool()
async def codebase_search(
    query: str,
    codebase: str = '',
    maxResults: int = 10,
) -> dict:
    """Search indexed codebases for relevant source code.

    Runs hybrid search (FTS5 keyword + vector similarity + RRF merge)
    filtered to indexed codebase chunks. Use this to find existing
    implementations before writing new code.

    Args:
        query: Search query (e.g., "manifest discovery", "sync schema from S3")
        codebase: Filter to a specific codebase name (e.g., "toast-analytics"). Empty = all codebases.
        maxResults: Maximum results to return (default 10)
    """
    if not flat_backend:
        return {'error': 'Flat search backend not available', 'results': []}

    prefix = f'codebase:{codebase}/' if codebase else 'codebase:'
    fetch_limit = maxResults * 3

    # Keyword search
    flat_hits = flat_backend.search_keyword(query, fetch_limit * 2)
    flat_hits = [h for h in flat_hits if h['file_path'].startswith(prefix)]

    # Vector search — use codebase model with query prefix
    vector_hits: list[dict] = []
    if vector_backend:
        try:
            all_vec = vector_backend.search_codebase(query, fetch_limit * 2)
            vector_hits = [h for h in all_vec if h['file_path'].startswith(prefix)]
        except Exception as e:
            log.warning(f'Vector search failed in codebase_search: {e}')

    # Merge via RRF
    if vector_hits:
        merged = FlatSearchBackend.merge_rrf(flat_hits, vector_hits)
    else:
        merged = flat_hits

    results = []
    conn = flat_backend._ensure_conn()
    for r in merged[:maxResults]:
        entry = {
            'path': r['file_path'],
            'title': r.get('title', ''),
            'snippet': smart_truncate(r.get('content', ''), 300),
            'score': round(r.get('score', 0), 3),
            'startLine': r.get('start_line'),
            'endLine': r.get('end_line'),
        }
        # Look up LLM label for matching symbols in this file
        fp = r['file_path']
        if fp.startswith('codebase:'):
            # Strip codebase prefix: "codebase:name/rel/path" -> "rel/path"
            parts = fp.split('/', 1)
            rel_path = parts[1] if len(parts) > 1 else fp
            cb_name = parts[0].replace('codebase:', '') if ':' in parts[0] else ''
            try:
                sym_row = conn.execute(
                    'SELECT metadata FROM symbols WHERE file_path = ? AND codebase = ? '
                    'AND metadata IS NOT NULL LIMIT 1',
                    (rel_path, cb_name),
                ).fetchone()
                if sym_row and sym_row['metadata']:
                    meta = json.loads(sym_row['metadata'])
                    if 'label' in meta:
                        entry['label'] = meta['label']
            except Exception:
                pass
        results.append(entry)

    return {'results': results}


@mcp_app.tool()
async def dependency_search(
    file_path: str,
    codebase: str = '',
    direction: str = 'imported_by',
    edge_type: str = '',
    maxResults: int = 50,
) -> dict:
    """Search the dependency graph for files that import or are imported by a given file.

    Use this to understand blast radius: which files depend on a changed file,
    or what a file depends on.

    Args:
        file_path: Relative file path within the codebase (e.g., "src/main/java/.../Foo.java")
        codebase: Codebase name (e.g., "toast-analytics"). Empty = search all codebases.
        direction: "imported_by" = find files that import this file (reverse deps),
                   "imports" = find files this file imports (forward deps),
                   "depended_on_by" = find build_dependency edges where metadata matches file_path
        edge_type: Filter by edge type (e.g., "calls", "extends", "build_dependency"). Empty = all.
        maxResults: Maximum results to return (default 50)
    """
    if not flat_backend:
        return {'error': 'Flat search backend not available', 'results': []}

    conn = flat_backend._ensure_conn()

    if direction == 'depended_on_by':
        # Reverse build dependency lookup: find who depends on this artifact
        query = 'SELECT source_file, edge_type, metadata, codebase FROM edges WHERE metadata LIKE ? AND edge_type = ?'
        params: list = [f'%{file_path}%', 'build_dependency']
    elif direction == 'imports':
        query = 'SELECT target_file, edge_type, metadata, codebase FROM edges WHERE source_file = ?'
        params = [file_path]
    else:
        query = 'SELECT source_file, edge_type, metadata, codebase FROM edges WHERE target_file = ?'
        params = [file_path]

    if codebase and direction != 'depended_on_by':
        query += ' AND codebase = ?'
        params.append(codebase)

    if edge_type and direction != 'depended_on_by':
        query += ' AND edge_type = ?'
        params.append(edge_type)

    query += f' LIMIT {int(maxResults)}'

    try:
        rows = conn.execute(query, params).fetchall()
    except Exception as e:
        return {'error': str(e), 'results': []}

    results = []
    for row in rows:
        dep_file = row[0]
        if dep_file is None:
            continue
        entry: dict[str, Any] = {
            'file': dep_file,
            'edge_type': row[1],
            'metadata': row[2],
        }
        # Include codebase in cross-codebase results
        if not codebase:
            entry['codebase'] = row[3]
        results.append(entry)

    return {
        'file': file_path,
        'direction': direction,
        'count': len(results),
        'results': results,
    }


@mcp_app.tool()
async def symbol_search(
    name: str,
    codebase: str = '',
    kind: str = '',
    maxResults: int = 20,
) -> dict:
    """Search for symbol declarations (classes, interfaces, functions, methods) by name.

    Use this to find where a class or function is defined, or to discover
    all classes matching a pattern.

    Args:
        name: Symbol name or pattern to search for. Supports SQL LIKE patterns (% for wildcard).
        codebase: Codebase name (e.g., "toast-analytics"). Empty = search all.
        kind: Filter by kind: "class", "interface", "enum", "function", "method", "object". Empty = all.
        maxResults: Maximum results to return (default 20)
    """
    if not flat_backend:
        return {'error': 'Flat search backend not available', 'results': []}

    conn = flat_backend._ensure_conn()

    # Support both exact match and LIKE patterns
    if '%' in name:
        name_clause = 'name LIKE ?'
    else:
        name_clause = 'name = ?'

    query = f'SELECT id, codebase, file_path, name, kind, start_line, end_line, metadata FROM symbols WHERE {name_clause}'
    params: list = [name]

    if codebase:
        query += ' AND codebase = ?'
        params.append(codebase)
    if kind:
        query += ' AND kind = ?'
        params.append(kind)

    query += f' LIMIT {int(maxResults)}'

    try:
        rows = conn.execute(query, params).fetchall()
    except Exception as e:
        return {'error': str(e), 'results': []}

    results = []
    for row in rows:
        entry = {
            'codebase': row['codebase'],
            'file': row['file_path'],
            'name': row['name'],
            'kind': row['kind'],
            'startLine': row['start_line'],
            'endLine': row['end_line'],
        }
        # Include LLM label if available
        if row['metadata']:
            try:
                meta = json.loads(row['metadata'])
                if 'label' in meta:
                    entry['label'] = meta['label']
            except (json.JSONDecodeError, TypeError):
                pass
        results.append(entry)

    return {'count': len(results), 'results': results}


@mcp_app.tool()
async def graph_traverse(
    start_node: str,
    codebase: str = '',
    direction: str = 'downstream',
    edge_types: str = '',
    max_depth: int = 5,
    max_results: int = 100,
    include_paths: bool = False,
) -> dict:
    """Traverse the code dependency graph starting from a file or symbol.

    Walk upstream (callers/dependents) or downstream (callees/dependencies)
    through multi-hop edges (calls, extends, implements, imports).

    Args:
        start_node: File path, or bare symbol name to resolve via symbols table.
        codebase: Codebase name. Empty = search all.
        direction: "downstream" = walk callees/deps, "upstream" = walk callers/dependents.
        edge_types: Comma-separated edge types to follow (e.g. "calls,extends"). Empty = all.
        max_depth: Maximum traversal depth (default 5, cap 10).
        max_results: Maximum nodes to return (default 100, cap 500).
        include_paths: If true, return full paths from start to each node (slower).
    """
    if not flat_backend:
        return {'error': 'Flat search backend not available', 'results': []}

    conn = flat_backend._ensure_conn()

    # Clamp parameters
    max_depth = min(max(1, max_depth), 10)
    max_results = min(max(1, max_results), 500)

    # Resolve start_node to file paths
    try:
        start_files = flat_backend.resolve_start_node(start_node, codebase)
    except ValueError as e:
        return {'error': str(e), 'results': []}

    # Parse edge_types filter
    type_list = [t.strip() for t in edge_types.split(',') if t.strip()] if edge_types else []

    # --- Try igraph sidecar first ---
    use_igraph = (
        graph_sidecar is not None
        and graph_sidecar.is_loaded
        and (not codebase or graph_sidecar.has_codebase(codebase))
    )

    # Check staleness and trigger background rebuild if needed
    if use_igraph and graph_sidecar.is_stale():
        log.info('GraphSidecar: staleness detected, triggering background rebuild')
        threading.Thread(
            target=graph_sidecar.rebuild,
            daemon=True,
            name='graph-rebuild',
        ).start()

    if use_igraph:
        all_results = []
        for start_file in start_files:
            results = graph_sidecar.traverse(
                start_file,
                direction=direction,
                edge_types=type_list or None,
                max_depth=max_depth,
                max_results=max_results,
                include_paths=include_paths,
            )
            all_results.extend(results)

        # Deduplicate across multiple start files
        if len(start_files) > 1:
            seen: dict[str, dict] = {}
            for r in all_results:
                f = r['file']
                if f not in seen or r['depth'] < seen[f]['depth']:
                    seen[f] = r
            all_results = sorted(seen.values(), key=lambda x: x['depth'])

        all_results = all_results[:max_results]

        return {
            'start': start_node,
            'direction': direction,
            'edge_types': type_list or 'all',
            'nodes_found': len(all_results),
            'results': all_results,
            'engine': 'igraph',
        }

    # --- Fallback: recursive CTE traversal ---
    type_filter = ''
    type_params: list = []
    if type_list:
        placeholders = ','.join('?' for _ in type_list)
        type_filter = f' AND edge_type IN ({placeholders})'
        type_params = type_list

    # Direction: upstream walks source->target reversed, downstream walks source->target forward
    if direction == 'upstream':
        # Base case: find edges WHERE target_file = start, walk to source_file
        base_col = 'target_file'
        walk_col = 'source_file'
        join_col = 'target_file'
    else:
        # Base case: find edges WHERE source_file = start, walk to target_file
        base_col = 'source_file'
        walk_col = 'target_file'
        join_col = 'source_file'

    all_results = []

    for start_file in start_files:
        base_params = [start_file] + type_params
        codebase_filter = ''
        if codebase:
            codebase_filter = ' AND codebase = ?'
            base_params.append(codebase)

        if not include_paths:
            # Mode A: Reachability — UNION deduplicates automatically
            cte_sql = f'''
                WITH RECURSIVE reachable(file, depth) AS (
                    SELECT {walk_col}, 1
                    FROM edges
                    WHERE {base_col} = ? AND {walk_col} IS NOT NULL{type_filter}{codebase_filter}
                  UNION
                    SELECT e.{walk_col}, r.depth + 1
                    FROM edges e
                    JOIN reachable r ON e.{join_col} = r.file
                    WHERE e.{walk_col} IS NOT NULL
                      AND r.depth < ?{type_filter}{codebase_filter}
                )
                SELECT file, MIN(depth) as min_depth
                FROM reachable
                GROUP BY file
                ORDER BY min_depth
                LIMIT ?
            '''
            # Build params: base_params for base case, then depth + type_params + codebase for recursive
            recursive_type_params = type_params.copy()
            recursive_codebase_params = [codebase] if codebase else []
            params = base_params + [max_depth] + recursive_type_params + recursive_codebase_params + [max_results]

            try:
                rows = conn.execute(cte_sql, params).fetchall()
            except Exception as e:
                return {'error': f'Traversal query failed: {e}', 'results': []}

            for row in rows:
                all_results.append({
                    'file': row[0],
                    'depth': row[1],
                    'start': start_file,
                })
        else:
            # Mode B: Path tracking — UNION ALL + cycle detection via INSTR
            cte_sql = f'''
                WITH RECURSIVE paths(file, depth, path) AS (
                    SELECT {walk_col}, 1, ? || ' -> ' || {walk_col}
                    FROM edges
                    WHERE {base_col} = ? AND {walk_col} IS NOT NULL{type_filter}{codebase_filter}
                  UNION ALL
                    SELECT e.{walk_col}, p.depth + 1, p.path || ' -> ' || e.{walk_col}
                    FROM edges e
                    JOIN paths p ON e.{join_col} = p.file
                    WHERE e.{walk_col} IS NOT NULL
                      AND p.depth < ?
                      AND INSTR(p.path, e.{walk_col}) = 0{type_filter}{codebase_filter}
                )
                SELECT p.file, p.depth as min_depth, p.path
                FROM paths p
                INNER JOIN (
                    SELECT file, MIN(depth) as md FROM paths GROUP BY file
                ) best ON p.file = best.file AND p.depth = best.md
                GROUP BY p.file
                ORDER BY min_depth
                LIMIT ?
            '''
            recursive_type_params = type_params.copy()
            recursive_codebase_params = [codebase] if codebase else []
            params = [start_file, start_file] + type_params + ([codebase] if codebase else []) + [max_depth] + recursive_type_params + recursive_codebase_params + [max_results]

            try:
                rows = conn.execute(cte_sql, params).fetchall()
            except Exception as e:
                return {'error': f'Path traversal query failed: {e}', 'results': []}

            for row in rows:
                entry: dict[str, Any] = {
                    'file': row[0],
                    'depth': row[1],
                    'start': start_file,
                    'path': row[2],
                }
                all_results.append(entry)

    # Deduplicate across multiple start files, keep shortest depth
    if len(start_files) > 1:
        seen2: dict[str, dict] = {}
        for r in all_results:
            f = r['file']
            if f not in seen2 or r['depth'] < seen2[f]['depth']:
                seen2[f] = r
        all_results = sorted(seen2.values(), key=lambda x: x['depth'])

    all_results = all_results[:max_results]

    return {
        'start': start_node,
        'direction': direction,
        'edge_types': type_list or 'all',
        'nodes_found': len(all_results),
        'results': all_results,
        'engine': 'cte',
    }


def compute_communities(conn: sqlite3.Connection, codebase: str) -> dict:
    """Run Louvain community detection on call+import edges for a codebase.

    Stores results in the communities table. Returns stats.
    """
    try:
        import igraph as ig
    except ImportError:
        return {'error': 'igraph not installed'}

    # Load call+import edges for this codebase
    rows = conn.execute(
        "SELECT source_file, target_file FROM edges "
        "WHERE codebase = ? AND target_file IS NOT NULL "
        "AND edge_type IN ('calls', 'import', 'static_import', 'wildcard_import', "
        "'extends', 'implements')",
        (codebase,),
    ).fetchall()

    if not rows:
        return {'error': 'No edges found for codebase', 'codebase': codebase}

    # Build igraph
    node_set: set[str] = set()
    edge_list: list[tuple[str, str]] = []
    for row in rows:
        node_set.add(row['source_file'])
        node_set.add(row['target_file'])
        edge_list.append((row['source_file'], row['target_file']))

    node_list = sorted(node_set)
    node_index = {name: idx for idx, name in enumerate(node_list)}

    g = ig.Graph(directed=True)
    g.add_vertices(len(node_list))
    g.vs['name'] = node_list
    g.add_edges([(node_index[s], node_index[t]) for s, t in edge_list])

    # Run Louvain on undirected version (Louvain requires undirected)
    g_undirected = g.as_undirected(mode='collapse')
    partition = g_undirected.community_multilevel()

    # Store results
    now_ts = int(time.time() * 1000)
    conn.execute('DELETE FROM communities WHERE codebase = ?', (codebase,))

    for vid, community_id in enumerate(partition.membership):
        conn.execute(
            'INSERT INTO communities (codebase, file_path, community_id, updated_at) '
            'VALUES (?, ?, ?, ?)',
            (codebase, node_list[vid], community_id, now_ts),
        )

    # Store meta for staleness detection
    edge_count = len(rows)
    community_count = max(partition.membership) + 1 if partition.membership else 0
    conn.execute(
        'INSERT OR REPLACE INTO community_meta (codebase, edge_count, community_count, computed_at) '
        'VALUES (?, ?, ?, ?)',
        (codebase, edge_count, community_count, now_ts),
    )
    conn.commit()

    return {
        'codebase': codebase,
        'nodes': len(node_list),
        'edges': len(edge_list),
        'communities': community_count,
        'modularity': round(partition.modularity, 4),
    }


def _communities_are_stale(conn: sqlite3.Connection, codebase: str) -> bool:
    """Check if community assignments are stale (>10% edge drift)."""
    meta = conn.execute(
        'SELECT edge_count FROM community_meta WHERE codebase = ?', (codebase,)
    ).fetchone()
    if not meta:
        return True  # Never computed

    current_edges = conn.execute(
        "SELECT COUNT(*) as cnt FROM edges "
        "WHERE codebase = ? AND target_file IS NOT NULL "
        "AND edge_type IN ('calls', 'import', 'static_import', 'wildcard_import', "
        "'extends', 'implements')",
        (codebase,),
    ).fetchone()['cnt']

    old_count = meta['edge_count']
    if old_count == 0:
        return current_edges > 0
    drift = abs(current_edges - old_count) / old_count
    return drift > 0.10


@mcp_app.tool()
async def community_search(
    codebase: str,
    file_path: str = '',
    list_all: bool = False,
    show_bridges: bool = False,
) -> dict:
    """Search for architectural communities in a codebase's dependency graph.

    Communities are groups of files that are tightly coupled via call/import edges,
    detected using Louvain clustering. Use this to understand module boundaries.

    Args:
        codebase: Codebase name (required).
        file_path: Find all files in the same community as this file.
        list_all: List all communities with file counts and representative files.
        show_bridges: Show edges that cross community boundaries (coupling points).
    """
    if not flat_backend:
        return {'error': 'Flat search backend not available', 'results': []}

    conn = flat_backend._ensure_conn()

    # Check staleness — recompute if needed
    recomputed = False
    if _communities_are_stale(conn, codebase):
        result = compute_communities(conn, codebase)
        if 'error' in result:
            return result
        recomputed = True

    if file_path:
        # Find community for this file, return all members
        row = conn.execute(
            'SELECT community_id FROM communities WHERE codebase = ? AND file_path = ?',
            (codebase, file_path),
        ).fetchone()
        if not row:
            return {'error': f'File not found in communities: {file_path}'}

        community_id = row['community_id']
        members = conn.execute(
            'SELECT file_path FROM communities WHERE codebase = ? AND community_id = ?',
            (codebase, community_id),
        ).fetchall()

        # Sort by degree (edge count) descending
        member_files = [m['file_path'] for m in members]
        degree_counts = []
        for mf in member_files:
            cnt = conn.execute(
                'SELECT COUNT(*) as cnt FROM edges WHERE codebase = ? AND (source_file = ? OR target_file = ?)',
                (codebase, mf, mf),
            ).fetchone()['cnt']
            degree_counts.append((mf, cnt))
        degree_counts.sort(key=lambda x: x[1], reverse=True)

        return {
            'community_id': community_id,
            'file_count': len(member_files),
            'files': [{'file': f, 'degree': d} for f, d in degree_counts],
            'recomputed': recomputed,
        }

    elif list_all:
        # List all communities
        rows = conn.execute(
            'SELECT community_id, COUNT(*) as file_count '
            'FROM communities WHERE codebase = ? '
            'GROUP BY community_id ORDER BY file_count DESC',
            (codebase,),
        ).fetchall()

        communities = []
        for row in rows:
            cid = row['community_id']
            # Get top 3 files by degree
            top_files = conn.execute(
                'SELECT c.file_path, '
                '(SELECT COUNT(*) FROM edges e WHERE e.codebase = ? '
                ' AND (e.source_file = c.file_path OR e.target_file = c.file_path)) as degree '
                'FROM communities c WHERE c.codebase = ? AND c.community_id = ? '
                'ORDER BY degree DESC LIMIT 3',
                (codebase, codebase, cid),
            ).fetchall()

            communities.append({
                'community_id': cid,
                'file_count': row['file_count'],
                'representative_files': [
                    {'file': r['file_path'], 'degree': r['degree']} for r in top_files
                ],
            })

        return {
            'codebase': codebase,
            'total_communities': len(communities),
            'communities': communities,
            'recomputed': recomputed,
        }

    elif show_bridges:
        # Find edges crossing community boundaries
        bridge_rows = conn.execute(
            'SELECT e.source_file, e.target_file, e.edge_type, '
            'c1.community_id as source_community, c2.community_id as target_community '
            'FROM edges e '
            'JOIN communities c1 ON c1.codebase = e.codebase AND c1.file_path = e.source_file '
            'JOIN communities c2 ON c2.codebase = e.codebase AND c2.file_path = e.target_file '
            'WHERE e.codebase = ? AND c1.community_id != c2.community_id '
            'LIMIT 200',
            (codebase,),
        ).fetchall()

        bridges = []
        for row in bridge_rows:
            bridges.append({
                'source': row['source_file'],
                'target': row['target_file'],
                'edge_type': row['edge_type'],
                'source_community': row['source_community'],
                'target_community': row['target_community'],
            })

        return {
            'codebase': codebase,
            'bridge_count': len(bridges),
            'bridges': bridges,
            'recomputed': recomputed,
        }

    return {'error': 'Specify file_path, list_all=true, or show_bridges=true'}


@mcp_app.tool()
async def memory_read(
    path: str,
    from_line: int = 1,
    lines: int = 0,
) -> dict:
    """Read a specific memory file or conversation session.

    For curated files, pass a relative path within ~/.claude-memory/.
    For conversation sessions, pass the session UUID from search results.

    Args:
        path: Relative path within ~/.claude-memory/, or a session UUID
        from_line: Starting line number, 1-based (default 1). Maps to original 'from' parameter.
        lines: Number of lines to return, 0 = all (default 0)
    """
    # UUID -> conversation lookup
    if UUID_RE.match(path):
        if not flat_backend:
            return {'error': 'Flat backend not available'}

        db_path = flat_backend.resolve_uuid(path)
        if not db_path:
            return {'error': f'No conversation found for UUID: {path}'}

        relative = db_path.replace('conversations/', '')
        absolute = str(ARCHIVE_DIR / relative)

        parsed = parse_conversation(absolute)
        if not parsed:
            return {'error': f'Could not parse conversation: {path}'}

        # Format exchanges as readable text
        out: list[str] = []
        if parsed.get('session_id'):
            out.append(f"Session: {parsed['session_id']}")
        if parsed.get('cwd'):
            out.append(f"Project: {parsed['cwd']}")
        if parsed.get('timestamp'):
            out.append(f"Date: {parsed['timestamp'][:10]}")
        out.append('---')

        for ex in parsed['exchanges']:
            out.append(f"User: {ex['user']}")
            if ex['assistant']:
                out.append(f"Assistant: {ex['assistant']}")
            out.append('---')

        all_lines = '\n'.join(out).split('\n')
        total = len(all_lines)
        start = max(0, from_line - 1)
        sliced = (
            all_lines[start:start + lines] if lines > 0
            else all_lines[start:]
        )
        return {'text': '\n'.join(sliced), 'path': path, 'totalLines': total}

    # Regular file
    full_path = validate_read_path(path)
    if not full_path.exists():
        return {'error': f'File not found: {path}'}

    content = full_path.read_text()
    all_lines = content.split('\n')
    total = len(all_lines)
    start = max(0, from_line - 1)
    sliced = (
        all_lines[start:start + lines] if lines > 0
        else all_lines[start:]
    )
    return {'text': '\n'.join(sliced), 'path': path, 'totalLines': total}


@mcp_app.tool()
async def memory_write(
    content: str,
    file: str = '',
    append: bool = True,
) -> dict:
    """Write content to a memory file. Defaults to daily log.

    Writes to the flat file system, updates the FTS5 search index,
    and generates vector embeddings for immediate search coverage.

    Args:
        content: Content to write
        file: Target file (MEMORY.md or memory/*.md). Default: memory/YYYY-MM-DD.md
        append: Append to file (true) or overwrite (false)
    """
    target = file or f'memory/{datetime.now().strftime("%Y-%m-%d")}.md'
    full_path = validate_write_path(target)

    # Ensure parent directory exists
    full_path.parent.mkdir(parents=True, exist_ok=True)

    # Write or append
    if append and full_path.exists():
        existing = full_path.read_text()
        sep = '' if existing.endswith('\n') else '\n'
        full_path.write_text(existing + sep + content)
    else:
        full_path.write_text(content)

    lines_written = content.count('\n') + 1

    # Update FTS5 index for the written file
    if flat_backend:
        try:
            flat_backend.index_written_file(target, full_path)
        except Exception as e:
            log.warning(f'FTS5 index update after write failed: {e}')

    # Generate embeddings for written chunks (immediate vector coverage)
    if vector_backend:
        try:
            embedded = vector_backend.embed_written_chunks(target)
            if embedded:
                log.info(f'Embedded {embedded} chunks on write for {target}')
        except Exception as e:
            log.warning(f'Embedding on write failed: {e}')

    return {'path': target, 'linesWritten': lines_written}


@mcp_app.tool()
async def get_status() -> dict:
    """Check health status of memory backends (flat + vector)."""
    result: dict[str, Any] = {}

    if flat_backend:
        result['flat'] = flat_backend.get_stats()
    else:
        result['flat'] = {'status': 'unavailable'}

    if vector_backend:
        result['vector'] = vector_backend.get_stats()
    else:
        result['vector'] = {'status': 'unavailable'}

    # Pipeline health (from job queue)
    try:
        from job_queue import JobQueue
        jq = JobQueue(DB_PATH)
        result['pipeline'] = jq.get_pipeline_health()
    except Exception:
        pass  # job_queue may not be available

    # Graph sidecar
    if graph_sidecar is not None:
        result['graph'] = graph_sidecar.get_stats()

    # Addon backends
    if addon_backends:
        addons_status = {}
        for name, backends in addon_backends.items():
            try:
                flat_stats = backends['flat'].get_stats()
                vec_stats = backends['vector'].get_stats()
                addons_status[name] = {
                    'chunks': flat_stats.get('chunks', 0),
                    'vectors': vec_stats.get('vectors', 0),
                    'db_path': str(backends['db_path']),
                }
            except Exception:
                addons_status[name] = {'status': 'error'}
        result['addons'] = addons_status

    return result


@mcp_app.tool()
async def index_session(
    session_path: str,
) -> dict:
    """Index a conversation session JSONL file into the search index.

    Parses the JSONL into exchange-aware chunks, embeds with the configured
    model, and stores as quantized embeddings. Purely additive — never
    deletes existing chunks. Skips noise (hook output, lint, etc.).

    Called by the SessionEnd hook to capture conversations before Claude Code
    deletes the JSONL files.

    Args:
        session_path: Absolute path to the session JSONL file
    """
    from pathlib import Path as P

    session_file = P(session_path)
    if not session_file.exists():
        return {'error': f'File not found: {session_path}'}
    if not session_file.suffix == '.jsonl':
        return {'error': f'Not a JSONL file: {session_path}'}

    # Derive the index path: conversations/<project>/<uuid>.jsonl
    # Session files live in ~/.claude/projects/<project>/sessions/<uuid>.jsonl
    # or sometimes directly as ~/.claude/projects/<project>/<uuid>.jsonl
    parts = session_file.parts
    try:
        proj_idx = parts.index('projects')
        project = parts[proj_idx + 1]
        uuid_name = session_file.name
        index_path = f'conversations/{project}/{uuid_name}'
    except (ValueError, IndexError):
        # Fallback: use parent dir name
        project = session_file.parent.name
        index_path = f'conversations/{project}/{session_file.name}'

    # Check if already indexed
    if flat_backend:
        conn = flat_backend._ensure_conn()
        existing = conn.execute(
            'SELECT COUNT(*) as cnt FROM chunks WHERE file_path = ?',
            (index_path,),
        ).fetchone()
        if existing and existing['cnt'] > 0:
            return {'status': 'already_indexed', 'path': index_path}

    # Parse conversation
    sys_path_backup = sys.path[:]
    scripts_dir = str(Path(__file__).parent.parent / 'scripts')
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        from conversation_parser import parse_conversation_jsonl
    finally:
        sys.path = sys_path_backup

    result = parse_conversation_jsonl(session_path)
    if result is None or not result.exchanges:
        return {'status': 'empty', 'path': index_path}

    # Noise filters — skip chunks that are pure noise
    NOISE_PREFIXES = [
        'Edit operation feedback',
        'Write operation feedback',
    ]
    NOISE_CONTAINS_NO_ASSISTANT = [
        'Base directory for this skill',
        'local-command-caveat',
        'PreToolUse',
    ]

    def is_noise(user_msg: str, assistant_msg: str) -> bool:
        for prefix in NOISE_PREFIXES:
            if user_msg.startswith(prefix):
                return True
        if not assistant_msg.strip():
            for pattern in NOISE_CONTAINS_NO_ASSISTANT:
                if pattern in user_msg:
                    return True
        return False

    # Exchange-aware chunking (matches Node.js chunker.ts)
    MAX_CHUNK_CHARS = 1600
    chunks = []
    current_texts = []
    current_chars = 0
    chunk_start = 0

    filtered_exchanges = [
        ex for ex in result.exchanges
        if not is_noise(ex.user_message, ex.assistant_message)
    ]

    if not filtered_exchanges:
        return {'status': 'all_noise', 'path': index_path}

    for i, ex in enumerate(filtered_exchanges):
        formatted = f'User: {ex.user_message}'
        if ex.assistant_message:
            formatted += f'\n\nAssistant: {ex.assistant_message}'
        if ex.tool_names:
            unique_tools = list(dict.fromkeys(ex.tool_names))
            formatted += f'\n\nTools: {", ".join(unique_tools)}'

        ex_chars = len(formatted)

        if current_chars + ex_chars > MAX_CHUNK_CHARS and current_texts:
            text = '\n\n---\n\n'.join(current_texts)
            title = f'{project} — {current_texts[0][:80]}'
            chunks.append({
                'content': text, 'title': title,
                'start_line': chunk_start + 1,
                'end_line': i,
            })
            current_texts = []
            current_chars = 0
            chunk_start = i

        current_texts.append(formatted)
        current_chars += ex_chars

    # Flush last chunk
    if current_texts:
        text = '\n\n---\n\n'.join(current_texts)
        title = f'{project} — {current_texts[0][:80]}'
        chunks.append({
            'content': text, 'title': title,
            'start_line': chunk_start + 1,
            'end_line': len(filtered_exchanges),
        })

    if not chunks:
        return {'status': 'no_chunks', 'path': index_path}

    # Insert chunks into DB
    if not flat_backend:
        return {'error': 'flat backend unavailable'}

    conn = flat_backend._ensure_conn()
    for i, chunk in enumerate(chunks):
        chunk_id = f'{index_path}:{i}'
        c_hash = hashlib.sha256(chunk['content'].encode()).hexdigest()[:16]

        conn.execute(
            'INSERT OR REPLACE INTO chunks '
            '(id, file_path, chunk_index, start_line, end_line, '
            'title, content, embedding, hash, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)',
            (
                chunk_id, index_path, i,
                chunk['start_line'], chunk['end_line'],
                chunk['title'], chunk['content'],
                c_hash, int(datetime.now().timestamp() * 1000),
            ),
        )

        # FTS5
        row = conn.execute(
            'SELECT rowid FROM chunks WHERE id = ?', (chunk_id,)
        ).fetchone()
        if row:
            try:
                conn.execute(
                    'INSERT INTO chunks_fts(rowid, content, title) VALUES (?, ?, ?)',
                    (row['rowid'], chunk['content'], chunk['title']),
                )
            except Exception:
                pass

    # Update files table
    file_hash = hashlib.sha256(
        session_file.read_text(errors='replace')[:1000].encode()
    ).hexdigest()[:16]
    conn.execute(
        'INSERT OR REPLACE INTO files '
        '(file_path, content_hash, last_indexed, chunk_count) '
        'VALUES (?, ?, ?, ?)',
        (index_path, file_hash,
         int(datetime.now().timestamp() * 1000), len(chunks)),
    )
    conn.commit()

    # Embed chunks
    embedded = 0
    if vector_backend:
        try:
            embedded = vector_backend.embed_written_chunks(index_path)
        except Exception as e:
            log.warning(f'Embedding failed for {index_path}: {e}')

    log.info(f'Indexed session {index_path}: {len(chunks)} chunks, '
             f'{embedded} embedded, {len(result.exchanges) - len(filtered_exchanges)} noise filtered')

    return {
        'status': 'indexed',
        'path': index_path,
        'chunks': len(chunks),
        'embedded': embedded,
        'noise_filtered': len(result.exchanges) - len(filtered_exchanges),
    }


# ──────────────────────────────────────────────────────────────
# Section 6: Main — startup, signals, shutdown
# ──────────────────────────────────────────────────────────────


async def run() -> None:
    global flat_backend, vector_backend, graph_sidecar

    # Initialize flat backend
    try:
        flat_backend = FlatSearchBackend(DB_PATH)
        flat_backend._ensure_conn()
        stats = flat_backend.get_stats()
        log.info(
            f"Flat backend ready: {stats.get('chunks', 0)} chunks, "
            f"{stats.get('files', 0)} files"
        )
    except Exception as e:
        log.error(f'Flat backend init failed: {e}')
        flat_backend = None

    # Initialize vector backend (model lazy-loads on first query)
    try:
        vector_backend = VectorSearchBackend(DB_PATH)
        vec_stats = vector_backend.get_stats()
        if vec_stats.get('status') == 'ok':
            log.info(
                f"Vector backend ready: {vec_stats.get('vectors', 0)} vectors "
                f"({vec_stats.get('model')}, {vec_stats.get('dims')}d)"
            )
        else:
            log.warning(f"Vector backend unavailable: {vec_stats}")
            vector_backend = None
    except Exception as e:
        log.warning(f'Vector backend init failed: {e}')
        vector_backend = None

    # Initialize graph sidecar (igraph for fast traversal)
    try:
        graph_sidecar = GraphSidecar(DB_PATH)
        if graph_sidecar.load():
            stats = graph_sidecar.get_stats()
            log.info(
                'Graph sidecar ready: %d nodes, %d edges (%.2fs)',
                stats['nodes'], stats['edges'], stats['load_time_seconds'],
            )
        else:
            log.info('Graph sidecar: no edges loaded (will use CTE fallback)')
    except Exception as e:
        log.warning('Graph sidecar init failed: %s', e)
        graph_sidecar = None

    # SIGHUP handler: rebuild graph sidecar atomically
    def _handle_sighup(sig, frame):
        log.info('SIGHUP received — rebuilding graph sidecar')
        if graph_sidecar is not None:
            threading.Thread(
                target=graph_sidecar.rebuild,
                daemon=True,
                name='graph-rebuild-sighup',
            ).start()

    signal.signal(signal.SIGHUP, _handle_sighup)

    # Warmup: pre-load model + index + addon backends in background
    def _warmup():
        try:
            if vector_backend is not None:
                log.info('Warmup: pre-loading vector index and model...')
                vector_backend._ensure_index()
                vector_backend._ensure_model()
                log.info('Warmup: vector backend ready')

            # Discover and init addon databases
            expected_model = (
                vector_backend.MODEL_NAME if vector_backend
                else VectorSearchBackend.DEFAULT_MODEL
            )
            discovered = discover_addon_dbs(expected_model)
            if discovered:
                log.info(f'Discovered {len(discovered)} addon database(s)')
                init_addon_backends(discovered)
                # Ensure model is loaded for addon vector backends
                for name, backends in addon_backends.items():
                    vec = backends.get('vector')
                    if vec:
                        vec._ensure_model()
            else:
                log.info('No addon databases found')
        except Exception as e:
            log.warning(f'Warmup failed (non-fatal): {e}')
        finally:
            _addon_warmup_done.set()

    threading.Thread(target=_warmup, daemon=True, name='warmup').start()

    # Graceful shutdown
    def shutdown(sig=None, frame=None):
        log.info('Shutting down...')
        if flat_backend:
            flat_backend.close()
        if vector_backend:
            vector_backend.close()
        for name, backends in addon_backends.items():
            try:
                backends['flat'].close()
                backends['vector'].close()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log.info('Starting unified-memory MCP server (stdio)')
    await mcp_app.run_stdio_async()


def main():
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info('Interrupted')
    except Exception as e:
        log.error(f'Fatal: {e}')
        raise


if __name__ == '__main__':
    main()
