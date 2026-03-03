#!/usr/bin/env python3
"""
Unified Memory MCP Server

Combines flat text search (SQLite FTS5) with local vector search
(sqlite-vec + sentence-transformers) and optional knowledge graph search
(FalkorDB/Graphiti) for hybrid retrieval. A single MCP server
that replaces both claude-memory and graphiti-memory.

Architecture:
  - Flat backend: Opens claude-memory's SQLite DB, runs FTS5 keyword search
  - Vector backend: Queries sqlite-vec (384-dim all-MiniLM-L6-v2 embeddings)
  - Graph backend: Uses graphiti_core to query FalkorDB for entities/facts
  - Hybrid retrieval: RRF merge of keyword + vector + graph-expanded results
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
GRAPHITI_CONFIG = MEMORY_DIR / 'graphiti-config' / 'config.yaml'
CONV_PREFIX = 'conversations/'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [unified-memory] %(levelname)s %(message)s',
    stream=sys.stderr,
)
log = logging.getLogger('unified-memory')


# ──────────────────────────────────────────────────────────────
# Section 1: FlatSearchBackend — SQLite FTS5 keyword search
# ──────────────────────────────────────────────────────────────


class FlatSearchBackend:
    """Keyword search over the claude-memory SQLite index.

    Opens the existing memory.db (created/maintained by the Node.js indexer)
    and performs FTS5 keyword searches. Also handles minimal FTS5 updates
    after memory_write operations.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            if not self.db_path.exists():
                raise FileNotFoundError(f'Memory database not found: {self.db_path}')
            self._conn = sqlite3.connect(str(self.db_path), timeout=10.0)
            self._conn.execute('PRAGMA busy_timeout = 5000')
            self._conn.execute('PRAGMA journal_mode = WAL')
            self._conn.row_factory = sqlite3.Row
        return self._conn

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

    Reads pre-computed embeddings from the chunks.embedding column (populated
    by the Node.js indexer using Xenova/all-MiniLM-L6-v2, 384-dim). Uses
    sentence-transformers to embed queries with the same model, then does
    brute-force cosine similarity via numpy. At ~4K chunks this is instant.

    No sqlite-vec extension needed — works with any Python sqlite3 build.
    """

    EMBEDDING_DIMS = 384
    MODEL_NAME = 'all-MiniLM-L6-v2'

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._model = None
        self._init_failed = False
        # In-memory embedding index (lazy-loaded)
        self._rowids: Optional[list[int]] = None
        self._matrix = None  # numpy array (N x 384), normalized

    def _ensure_conn(self) -> Optional[sqlite3.Connection]:
        if self._conn is not None:
            return self._conn
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=10.0)
            conn.execute('PRAGMA busy_timeout = 5000')
            conn.execute('PRAGMA journal_mode = WAL')
            conn.row_factory = sqlite3.Row
            self._conn = conn
            return conn
        except Exception as e:
            log.warning(f'Vector backend connection failed: {e}')
            self._init_failed = True
            return None

    def _ensure_index(self) -> bool:
        """Load all embeddings from chunks table into a numpy matrix."""
        if self._matrix is not None:
            return True
        conn = self._ensure_conn()
        if conn is None:
            return False
        try:
            import numpy as np

            rows = conn.execute(
                'SELECT rowid, embedding FROM chunks '
                'WHERE embedding IS NOT NULL'
            ).fetchall()

            valid_rowids = []
            valid_embeddings = []
            for row in rows:
                blob = row['embedding']
                if blob and len(blob) == self.EMBEDDING_DIMS * 4:
                    vec = struct.unpack(f'{self.EMBEDDING_DIMS}f', blob)
                    valid_embeddings.append(vec)
                    valid_rowids.append(row['rowid'])

            if not valid_embeddings:
                log.warning('Vector backend: no valid embeddings found')
                return False

            self._rowids = valid_rowids
            self._matrix = np.array(valid_embeddings, dtype=np.float32)
            # Normalize rows for cosine similarity via dot product
            norms = np.linalg.norm(self._matrix, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            self._matrix = self._matrix / norms

            log.info(f'Vector index loaded: {len(valid_rowids)} embeddings')
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

            self._model = SentenceTransformer(self.MODEL_NAME)
            log.info(f'Vector backend: loaded {self.MODEL_NAME} model')
            return self._model
        except Exception as e:
            log.warning(f'Vector backend model load failed: {e}')
            self._init_failed = True
            return None

    def search(self, query: str, limit: int) -> list[dict]:
        """Vector similarity search via brute-force cosine similarity.

        Embeds the query, computes dot product against all stored embeddings
        (pre-normalized), returns top-k results with similarity scores.
        """
        if self._init_failed or limit <= 0:
            return []

        if not self._ensure_index():
            return []

        model = self._ensure_model()
        if model is None:
            return []

        import numpy as np

        # Embed and normalize query
        query_vec = model.encode(query, normalize_embeddings=True)
        query_vec = np.array(query_vec, dtype=np.float32).reshape(1, -1)

        # Cosine similarity = dot product of normalized vectors
        similarities = (self._matrix @ query_vec.T).flatten()

        # Get top-k indices
        top_k = min(limit, len(similarities))
        top_indices = np.argpartition(similarities, -top_k)[-top_k:]
        top_indices = top_indices[np.argsort(similarities[top_indices])[::-1]]

        conn = self._ensure_conn()
        if conn is None:
            return []

        results = []
        for idx in top_indices:
            rowid = self._rowids[idx]
            score = float(similarities[idx])
            if score <= 0:
                continue
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
            return {
                'status': 'ok',
                'vectors': row['cnt'],
                'model': self.MODEL_NAME,
                'dims': self.EMBEDDING_DIMS,
            }
        except Exception as e:
            return {'status': 'error', 'error': str(e)}

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        self._matrix = None
        self._rowids = None


# ──────────────────────────────────────────────────────────────
# Section 2: GraphSearchBackend — FalkorDB / Graphiti
# ──────────────────────────────────────────────────────────────


class GraphSearchBackend:
    """Lazy-init graph search via Graphiti + FalkorDB.

    Doesn't connect until first query (lazy init). If FalkorDB is not
    running or OpenAI key is missing, marks itself as failed and returns
    empty results for all subsequent calls.
    """

    def __init__(self, config_path: Path):
        self.config_path = config_path
        self._client = None
        self._group_id = 'claude-memory'
        self._initialized = False
        self._init_failed = False

    async def _ensure_client(self):
        """Lazy initialization of Graphiti client."""
        if self._initialized:
            return self._client
        if self._init_failed:
            return None

        try:
            import yaml
            from graphiti_core import Graphiti
            from graphiti_core.driver.falkordb_driver import FalkorDriver
            from graphiti_core.embedder.openai import (
                OpenAIEmbedder,
                OpenAIEmbedderConfig,
            )
            from graphiti_core.llm_client import OpenAIClient
            from graphiti_core.llm_client.config import LLMConfig as CoreLLMConfig

            with open(self.config_path) as f:
                cfg = yaml.safe_load(f)

            api_key = os.environ.get('OPENAI_API_KEY', '')
            if not api_key:
                log.warning('OPENAI_API_KEY not set - graph search disabled')
                self._init_failed = True
                return None

            # LLM client
            llm_model = cfg.get('llm', {}).get('model', 'gpt-4o-mini')
            llm_config = CoreLLMConfig(
                api_key=api_key, model=llm_model, small_model=llm_model,
            )
            llm_client = OpenAIClient(
                config=llm_config, reasoning=None, verbosity=None,
            )

            # Embedder
            embed_model = cfg.get('embedder', {}).get('model', 'text-embedding-3-small')
            embedder = OpenAIEmbedder(config=OpenAIEmbedderConfig(
                api_key=api_key, embedding_model=embed_model,
            ))

            # FalkorDB driver
            from urllib.parse import urlparse

            self._group_id = cfg.get('graphiti', {}).get('group_id', 'claude-memory')

            db_cfg = cfg.get('database', {}).get('providers', {}).get('falkordb', {})
            uri = db_cfg.get('uri', 'redis://localhost:6379')
            parsed = urlparse(uri)

            driver = FalkorDriver(
                host=parsed.hostname or 'localhost',
                port=parsed.port or 6379,
                password=None,
                database=db_cfg.get('database', self._group_id),
            )

            self._client = Graphiti(
                graph_driver=driver,
                llm_client=llm_client,
                embedder=embedder,
            )
            self._initialized = True
            log.info('Graph backend initialized')
            return self._client

        except Exception as e:
            log.warning(f'Graph backend init failed: {e}')
            self._init_failed = True
            return None

    async def search_nodes(self, query: str, max_results: int = 5) -> list[dict]:
        """Search for entity nodes in the knowledge graph."""
        client = await self._ensure_client()
        if not client:
            return []
        try:
            from graphiti_core.search.search_config_recipes import (
                NODE_HYBRID_SEARCH_RRF,
            )

            results = await client.search_(
                query=query,
                config=NODE_HYBRID_SEARCH_RRF,
                group_ids=[self._group_id],
            )
            nodes = (results.nodes or [])[:max_results]
            return [
                {
                    'name': n.name,
                    'type': n.labels[0] if n.labels else 'Entity',
                    'summary': n.summary or '',
                }
                for n in nodes
            ]
        except Exception as e:
            log.warning(f'Graph node search failed: {e}')
            return []

    async def search_facts(self, query: str, max_results: int = 5) -> list[dict]:
        """Search for relationship facts in the knowledge graph."""
        client = await self._ensure_client()
        if not client:
            return []
        try:
            edges = await client.search(
                group_ids=[self._group_id],
                query=query,
                num_results=max_results,
            )
            return [
                {
                    'fact': e.fact or '',
                    'source': e.source_node_name or '',
                    'target': e.target_node_name or '',
                }
                for e in (edges or [])
            ]
        except Exception as e:
            log.warning(f'Graph fact search failed: {e}')
            return []

    async def add_episode(
        self, name: str, body: str, source_desc: str = '',
    ) -> bool:
        """Add an episode to the knowledge graph."""
        client = await self._ensure_client()
        if not client:
            return False
        try:
            from graphiti_core.nodes import EpisodeType

            await client.add_episode(
                name=name,
                episode_body=body,
                source=EpisodeType.text,
                source_description=source_desc,
                group_id=self._group_id,
                reference_time=datetime.now(),
            )
            return True
        except Exception as e:
            log.warning(f'Graph episode add failed: {e}')
            return False

    async def health_check(self) -> dict:
        """Test graph database connectivity and return stats."""
        client = await self._ensure_client()
        if not client:
            return {'status': 'unavailable'}
        try:
            records, _, _ = await client.driver.execute_query(
                'MATCH (n:Entity) RETURN count(n) as cnt'
            )
            entities = records[0]['cnt'] if records else 0

            records, _, _ = await client.driver.execute_query(
                'MATCH ()-[r]->() RETURN count(r) as cnt'
            )
            rels = records[0]['cnt'] if records else 0

            return {
                'status': 'ok',
                'entities': entities,
                'relationships': rels,
            }
        except Exception as e:
            return {'status': 'error', 'error': str(e)}

    async def close(self) -> None:
        if self._client:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None


# ──────────────────────────────────────────────────────────────
# Section 3: Post-filtering helpers (ported from tools.ts)
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
        'knowledge graph search (FalkorDB/Graphiti). Use memory_search for '
        'finding relevant past context, memory_read for reading specific files '
        'or conversation sessions by UUID, memory_write for persisting new '
        'knowledge, graph_search for direct graph queries, and get_status '
        'to check backend health.'
    ),
)

# Backends (initialized at startup)
flat_backend: Optional[FlatSearchBackend] = None
vector_backend: Optional[VectorSearchBackend] = None
graph_backend: Optional[GraphSearchBackend] = None


async def _graph_search_combined(query: str) -> dict:
    """Run graph node + fact search in parallel with error isolation."""
    if not graph_backend:
        return {'nodes': [], 'facts': []}
    try:
        nodes, facts = await asyncio.gather(
            graph_backend.search_nodes(query, 5),
            graph_backend.search_facts(query, 5),
            return_exceptions=True,
        )
        return {
            'nodes': nodes if isinstance(nodes, list) else [],
            'facts': facts if isinstance(facts, list) else [],
        }
    except Exception:
        return {'nodes': [], 'facts': []}


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
    """Search memory for relevant content using hybrid flat+graph retrieval.

    Combines keyword search over indexed memory files and conversation archives
    with knowledge graph search for entities and relationships. Graph entities
    expand the keyword query for better recall.

    Args:
        query: Search query text
        maxResults: Maximum results to return (default 10)
        minScore: Minimum relevance score 0-1 (default 0)
        after: Filter: only results after this date (YYYY-MM-DD)
        before: Filter: only results before this date (YYYY-MM-DD)
        project: Filter: only results from this project directory
        source: Filter: "curated" for memory files only, "conversations" for session history only, empty for both
    """
    if not flat_backend:
        return {'error': 'Flat search backend not available', 'results': []}

    has_filters = bool(after or before or project or source)
    fetch_limit = maxResults * (5 if has_filters else 3)

    # Phase 1: Start graph search (async) while running flat + vector search (sync)
    graph_task = asyncio.create_task(_graph_search_combined(query))

    # Flat keyword search — synchronous but fast (<50ms typically)
    flat_original = flat_backend.search_keyword(query, fetch_limit * 2)

    # Vector similarity search — synchronous, first call loads model (~2s), then fast
    vector_hits: list[dict] = []
    if vector_backend:
        try:
            vector_hits = vector_backend.search(query, fetch_limit * 2)
        except Exception as e:
            log.warning(f'Vector search failed: {e}')

    # Wait for graph results with 3s timeout
    graph_ctx: dict = {'nodes': [], 'facts': []}
    try:
        graph_ctx = await asyncio.wait_for(graph_task, timeout=3.0)
    except asyncio.TimeoutError:
        log.warning('Graph search timed out (3s)')
    except Exception as e:
        log.warning(f'Graph search error: {e}')

    # Phase 2: Merge keyword + vector via RRF, then expand with graph entities
    if vector_hits:
        merged = FlatSearchBackend.merge_rrf(flat_original, vector_hits)
    else:
        merged = flat_original

    # Graph entity expansion: run a second keyword search with entity names
    entity_names = [n['name'] for n in graph_ctx.get('nodes', [])]
    if entity_names:
        expanded_query = query + ' ' + ' '.join(entity_names)
        expanded_hits = flat_backend.search_keyword(expanded_query, fetch_limit * 2)
        if expanded_hits:
            merged = FlatSearchBackend.merge_rrf(merged, expanded_hits)
        # else: keep current merged as-is

    # Phase 3: Post-filter (ported from tools.ts handleMemorySearch)
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

        # Source filter
        if source == 'curated' and is_conv:
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

    result: dict[str, Any] = {'results': filtered}

    # Include graph context if available
    if graph_ctx.get('nodes') or graph_ctx.get('facts'):
        result['graph_context'] = {
            'entities': graph_ctx.get('nodes', []),
            'facts': graph_ctx.get('facts', []),
        }

    return result


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

    Writes to the flat file system and updates the FTS5 search index.
    If the graph backend is available, also ingests the content as a
    graph episode in the background.

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

    # Background graph ingestion
    if graph_backend:
        asyncio.create_task(_background_graph_ingest(target, content))

    return {'path': target, 'linesWritten': lines_written}


async def _background_graph_ingest(file_path: str, content: str) -> None:
    """Ingest written content into the knowledge graph in the background."""
    if not graph_backend:
        return
    try:
        name = f'memory-write: {file_path}'
        await graph_backend.add_episode(
            name, content, source_desc=f'Written to {file_path}',
        )
    except Exception as e:
        log.warning(f'Background graph ingest failed: {e}')


@mcp_app.tool()
async def graph_search(
    query: str,
    search_type: str = 'facts',
    max_results: int = 10,
) -> dict:
    """Search the knowledge graph directly for entities or facts.

    Use this for structural/relational queries that benefit from the
    knowledge graph (e.g., "what technologies does toast-analytics use?",
    "what decisions were made about RRF scoring?").

    Args:
        query: Search query
        search_type: "nodes" for entities, "facts" for relationships (default: facts)
        max_results: Maximum results (default 10)
    """
    if not graph_backend:
        return {'error': 'Graph backend not available'}

    if search_type == 'nodes':
        nodes = await graph_backend.search_nodes(query, max_results)
        return {'nodes': nodes}
    else:
        facts = await graph_backend.search_facts(query, max_results)
        return {'facts': facts}


@mcp_app.tool()
async def get_status() -> dict:
    """Check health status of both memory backends.

    Returns status, chunk/file counts for the flat backend, and
    entity/relationship counts for the graph backend.
    """
    result: dict[str, Any] = {}

    if flat_backend:
        result['flat'] = flat_backend.get_stats()
    else:
        result['flat'] = {'status': 'unavailable'}

    if vector_backend:
        result['vector'] = vector_backend.get_stats()
    else:
        result['vector'] = {'status': 'unavailable'}

    if graph_backend:
        try:
            result['graph'] = await asyncio.wait_for(
                graph_backend.health_check(), timeout=3.0,
            )
        except asyncio.TimeoutError:
            result['graph'] = {'status': 'timeout'}
        except Exception as e:
            result['graph'] = {'status': 'error', 'error': str(e)}
    else:
        result['graph'] = {'status': 'unavailable'}

    return result


# ──────────────────────────────────────────────────────────────
# Section 6: Main — startup, signals, shutdown
# ──────────────────────────────────────────────────────────────


async def run() -> None:
    global flat_backend, vector_backend, graph_backend

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

    # Register graph backend (lazy init on first query)
    if GRAPHITI_CONFIG.exists():
        graph_backend = GraphSearchBackend(GRAPHITI_CONFIG)
        log.info('Graph backend registered (lazy init on first query)')
    else:
        log.info(
            f'Graph config not found at {GRAPHITI_CONFIG} - graph disabled'
        )

    # Graceful shutdown
    def shutdown(sig=None, frame=None):
        log.info('Shutting down...')
        if flat_backend:
            flat_backend.close()
        if vector_backend:
            vector_backend.close()
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
