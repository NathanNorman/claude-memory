#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""Shared test fixtures for TKE Wave 3 test suite.

Provides:
- create_test_db() — in-memory SQLite with all dep/community/job tables
- mock_embedding_model() — fake model with deterministic hash-based vectors
"""

import hashlib
import sqlite3
import time

import numpy as np


def create_test_db(
    num_clusters: int = 3,
    nodes_per_cluster: int = 17,
    intra_edges_per_cluster: int = 27,
    bridge_edges: int = 6,
    symbols_per_cluster: int = 10,
) -> sqlite3.Connection:
    """Create an in-memory SQLite DB with production schema and synthetic graph data.

    Default: 3 clusters, ~51 nodes, ~87 edges, ~30 symbols.
    """
    conn = sqlite3.connect(':memory:')
    conn.execute('PRAGMA journal_mode = WAL')
    conn.row_factory = sqlite3.Row

    # --- Core tables (from FlatSearchBackend.__init__) ---
    conn.execute('''
        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            start_line INTEGER DEFAULT 0,
            end_line INTEGER DEFAULT 0,
            title TEXT DEFAULT '',
            content TEXT DEFAULT '',
            embedding BLOB,
            embedding_binary BLOB,
            hash TEXT DEFAULT '',
            updated_at INTEGER DEFAULT 0
        )
    ''')
    conn.execute('''
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
        USING fts5(content, title, content='chunks', content_rowid='rowid')
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS files (
            file_path TEXT PRIMARY KEY,
            content_hash TEXT,
            last_indexed TEXT,
            chunk_count INTEGER DEFAULT 0,
            summary TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')

    # --- Multi-signal retrieval tables ---
    # event_date column on chunks (added via ALTER in production)
    # Already included in CREATE TABLE above if we add it there; for test
    # compatibility, add via ALTER which is idempotent in test setup
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

    # --- Entity relationships table ---
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

    # --- Dependency tables (from _ensure_dep_tables) ---
    conn.execute('''
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codebase TEXT NOT NULL,
            source_file TEXT NOT NULL,
            target_file TEXT,
            edge_type TEXT NOT NULL,
            metadata TEXT,
            confidence REAL,
            updated_at INTEGER NOT NULL
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_file, codebase)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_file, codebase)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_edges_target_type ON edges(target_file, edge_type, codebase)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_edges_source_type ON edges(source_file, edge_type, codebase)')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS symbols (
            id TEXT PRIMARY KEY,
            codebase TEXT NOT NULL,
            file_path TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            metadata TEXT,
            updated_at INTEGER NOT NULL
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name, codebase)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_path, codebase)')

    # --- Community tables ---
    conn.execute('''
        CREATE TABLE IF NOT EXISTS communities (
            codebase TEXT NOT NULL,
            file_path TEXT NOT NULL,
            community_id INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (codebase, file_path)
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_communities_codebase_community ON communities(codebase, community_id)')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS community_meta (
            codebase TEXT PRIMARY KEY,
            edge_count INTEGER NOT NULL,
            community_count INTEGER NOT NULL,
            computed_at INTEGER NOT NULL
        )
    ''')

    # --- Job queue table ---
    conn.execute('''
        CREATE TABLE IF NOT EXISTS index_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_name TEXT NOT NULL,
            clone_url TEXT NOT NULL,
            before_sha TEXT NOT NULL,
            after_sha TEXT NOT NULL,
            ref TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            error TEXT,
            created_at REAL NOT NULL,
            started_at REAL,
            completed_at REAL,
            timing TEXT
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON index_jobs(status, created_at)')

    # --- Codebase meta ---
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

    # --- Populate synthetic graph data ---
    now_ts = int(time.time() * 1000)
    codebase = 'test-repo'
    edge_types = ['calls', 'import', 'extends']

    all_nodes: list[str] = []
    for c in range(num_clusters):
        cluster_nodes = [f'cluster{c}/file{i}.java' for i in range(nodes_per_cluster)]
        all_nodes.extend(cluster_nodes)

        # Intra-cluster edges (star + chain pattern for predictable structure)
        hub = cluster_nodes[0]
        edge_count = 0
        for i in range(1, len(cluster_nodes)):
            if edge_count >= intra_edges_per_cluster:
                break
            # Hub -> spoke
            conn.execute(
                'INSERT INTO edges (codebase, source_file, target_file, edge_type, metadata, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (codebase, hub, cluster_nodes[i], edge_types[i % len(edge_types)], None, now_ts),
            )
            edge_count += 1
            # Chain: spoke -> next spoke
            if i + 1 < len(cluster_nodes) and edge_count < intra_edges_per_cluster:
                conn.execute(
                    'INSERT INTO edges (codebase, source_file, target_file, edge_type, metadata, updated_at) '
                    'VALUES (?, ?, ?, ?, ?, ?)',
                    (codebase, cluster_nodes[i], cluster_nodes[i + 1], 'calls', None, now_ts),
                )
                edge_count += 1

        # Symbols for this cluster
        for s in range(min(symbols_per_cluster, nodes_per_cluster)):
            sym_id = f'{codebase}:{cluster_nodes[s]}:Sym{s}'
            conn.execute(
                'INSERT INTO symbols (id, codebase, file_path, name, kind, start_line, end_line, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (sym_id, codebase, cluster_nodes[s], f'Symbol{c}_{s}', 'class', 1, 50, now_ts),
            )

    # Bridge edges between clusters
    for b in range(bridge_edges):
        src_cluster = b % num_clusters
        tgt_cluster = (b + 1) % num_clusters
        src_node = all_nodes[src_cluster * nodes_per_cluster + 1]
        tgt_node = all_nodes[tgt_cluster * nodes_per_cluster + 2]
        conn.execute(
            'INSERT INTO edges (codebase, source_file, target_file, edge_type, metadata, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (codebase, src_node, tgt_node, 'import', None, now_ts),
        )

    # Codebase meta entries for content hash lookups
    for node in all_nodes:
        content_hash = hashlib.sha256(node.encode()).hexdigest()[:16]
        conn.execute(
            'INSERT INTO codebase_meta (codebase, file_path, content_hash, indexed_at) '
            'VALUES (?, ?, ?, ?)',
            (codebase, node, content_hash, '2025-01-01T00:00:00'),
        )

    conn.commit()
    return conn


class MockEmbeddingModel:
    """Fake embedding model that returns deterministic hash-based vectors."""

    def __init__(self, dims: int = 384):
        self.dims = dims
        self._calls: list[list[str]] = []

    def encode(
        self,
        texts: list[str] | str,
        normalize_embeddings: bool = True,
        batch_size: int = 32,
    ) -> np.ndarray:
        if isinstance(texts, str):
            texts = [texts]
        self._calls.append(list(texts))
        embeddings = []
        for text in texts:
            # Deterministic vector from text hash
            h = hashlib.sha256(text.encode()).digest()
            # Expand hash to fill dims
            repeated = (h * ((self.dims * 4 // len(h)) + 1))[:self.dims * 4]
            vec = np.frombuffer(repeated[:self.dims * 4], dtype=np.float32).copy()[:self.dims]
            if normalize_embeddings:
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec = vec / norm
            embeddings.append(vec)
        return np.array(embeddings, dtype=np.float32)

    @property
    def call_texts(self) -> list[list[str]]:
        """All texts passed to encode(), grouped by call."""
        return self._calls


def mock_embedding_model(dims: int = 384) -> MockEmbeddingModel:
    """Create a mock embedding model with deterministic hash-based vectors."""
    return MockEmbeddingModel(dims=dims)
