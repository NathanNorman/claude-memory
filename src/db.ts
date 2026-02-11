import Database from 'better-sqlite3';
import type { Database as DatabaseType } from 'better-sqlite3';
import * as sqliteVec from 'sqlite-vec';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import type { MemoryChunk, FileEntry } from './types.js';
import { EMBEDDING_MODEL, EMBEDDING_PROVIDER } from './types.js';

// --- Database Initialization ---

export function openDb(dbPath: string): DatabaseType {
  // Ensure parent directory exists
  mkdirSync(dirname(dbPath), { recursive: true });

  const db = new Database(dbPath);
  db.defaultSafeIntegers(false); // Return numbers instead of BigInt for integer columns

  // Load sqlite-vec extension
  sqliteVec.load(db);

  // Enable WAL mode for better concurrency
  db.pragma('journal_mode = WAL');
  // Wait up to 5s for locks instead of failing immediately
  db.pragma('busy_timeout = 5000');

  // Create tables
  db.exec(`
    CREATE TABLE IF NOT EXISTS chunks (
      id TEXT PRIMARY KEY,
      file_path TEXT NOT NULL,
      chunk_index INTEGER NOT NULL,
      start_line INTEGER NOT NULL DEFAULT 0,
      end_line INTEGER NOT NULL DEFAULT 0,
      title TEXT NOT NULL,
      content TEXT NOT NULL,
      embedding BLOB,
      hash TEXT NOT NULL,
      updated_at INTEGER NOT NULL
    )
  `);

  db.exec(`
    CREATE TABLE IF NOT EXISTS files (
      file_path TEXT PRIMARY KEY,
      content_hash TEXT NOT NULL,
      last_indexed INTEGER NOT NULL,
      chunk_count INTEGER NOT NULL
    )
  `);

  // FTS5 virtual table for full-text search on content and title
  db.exec(`
    CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
      content,
      title,
      content=chunks,
      content_rowid=rowid
    )
  `);

  // Vec0 virtual table for vector similarity search
  db.exec(`
    CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
      embedding float[384]
    )
  `);

  // Meta table for tracking model/config changes
  db.exec(`
    CREATE TABLE IF NOT EXISTS meta (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL
    )
  `);

  // Embedding cache: skip re-embedding unchanged chunks
  db.exec(`
    CREATE TABLE IF NOT EXISTS embedding_cache (
      provider TEXT NOT NULL,
      model TEXT NOT NULL,
      hash TEXT NOT NULL,
      embedding BLOB NOT NULL,
      dims INTEGER NOT NULL,
      updated_at INTEGER NOT NULL,
      PRIMARY KEY (provider, model, hash)
    )
  `);
  db.exec(`CREATE INDEX IF NOT EXISTS idx_embedding_cache_updated_at ON embedding_cache(updated_at)`);

  // Migrate: add summary column to files table (idempotent)
  try { db.exec(`ALTER TABLE files ADD COLUMN summary TEXT`); } catch { /* already exists */ }

  // Indexes
  db.exec(`CREATE INDEX IF NOT EXISTS idx_chunks_file_path ON chunks(file_path)`);
  db.exec(`CREATE INDEX IF NOT EXISTS idx_chunks_hash ON chunks(hash)`);

  return db;
}

// --- Embedding Serialization ---

function serializeEmbedding(embedding: Float32Array): Buffer {
  return Buffer.from(embedding.buffer, embedding.byteOffset, embedding.byteLength);
}

// --- Chunk Operations ---

export function insertChunk(db: DatabaseType, chunk: MemoryChunk): void {
  // Insert into main chunks table
  const insertMain = db.prepare(`
    INSERT OR REPLACE INTO chunks (id, file_path, chunk_index, start_line, end_line, title, content, embedding, hash, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `);

  insertMain.run(
    chunk.id,
    chunk.filePath,
    chunk.chunkIndex,
    chunk.startLine,
    chunk.endLine,
    chunk.title,
    chunk.content,
    serializeEmbedding(chunk.embedding),
    chunk.hash,
    chunk.updatedAt,
  );

  // Get the rowid of the inserted chunk for FTS5 and vec0
  const row = db.prepare(`SELECT rowid FROM chunks WHERE id = ?`).get(chunk.id) as { rowid: number } | undefined;
  if (!row) return;
  const rowid = row.rowid;

  // Sync FTS5: delete old entry then insert new
  try {
    db.prepare(`INSERT OR REPLACE INTO chunks_fts(rowid, content, title) VALUES (?, ?, ?)`).run(
      rowid,
      chunk.content,
      chunk.title,
    );
  } catch {
    // FTS5 can be corrupted by concurrent access; safe to skip
  }

  // Sync vec0: delete old entry then insert new (sqlite-vec requires BigInt for rowid)
  try {
    db.prepare(`DELETE FROM chunks_vec WHERE rowid = ?`).run(BigInt(rowid));
  } catch {
    // sqlite-vec can fail on corrupted blob data; safe to skip
  }
  db.prepare(`INSERT INTO chunks_vec (rowid, embedding) VALUES (?, ?)`).run(
    BigInt(rowid),
    serializeEmbedding(chunk.embedding),
  );
}

export function deleteChunksByFile(db: DatabaseType, filePath: string): void {
  // Get rowids before deleting for FTS5/vec0 cleanup
  const rows = db.prepare(`SELECT rowid FROM chunks WHERE file_path = ?`).all(filePath) as { rowid: number }[];

  for (const row of rows) {
    try {
      db.prepare(`DELETE FROM chunks_fts WHERE rowid = ?`).run(row.rowid);
    } catch {
      // FTS5 can be corrupted by concurrent access; safe to skip
    }
    try {
      db.prepare(`DELETE FROM chunks_vec WHERE rowid = ?`).run(BigInt(row.rowid));
    } catch {
      // sqlite-vec can fail on corrupted blob data; safe to skip
    }
  }

  db.prepare(`DELETE FROM chunks WHERE file_path = ?`).run(filePath);
}

// --- File Operations ---

export function upsertFile(db: DatabaseType, entry: FileEntry): void {
  db.prepare(`
    INSERT OR REPLACE INTO files (file_path, content_hash, last_indexed, chunk_count, summary)
    VALUES (?, ?, ?, ?, ?)
  `).run(entry.filePath, entry.contentHash, entry.lastIndexed, entry.chunkCount, entry.summary ?? null);
}

export function getFile(db: DatabaseType, filePath: string): FileEntry | null {
  const row = db.prepare(`SELECT file_path, content_hash, last_indexed, chunk_count, summary FROM files WHERE file_path = ?`)
    .get(filePath) as { file_path: string; content_hash: string; last_indexed: number; chunk_count: number; summary: string | null } | undefined;

  if (!row) return null;

  return {
    filePath: row.file_path,
    contentHash: row.content_hash,
    lastIndexed: row.last_indexed,
    chunkCount: row.chunk_count,
    summary: row.summary,
  };
}

export function getAllFiles(db: DatabaseType): FileEntry[] {
  const rows = db.prepare(`SELECT file_path, content_hash, last_indexed, chunk_count, summary FROM files`)
    .all() as { file_path: string; content_hash: string; last_indexed: number; chunk_count: number; summary: string | null }[];

  return rows.map((row) => ({
    filePath: row.file_path,
    contentHash: row.content_hash,
    lastIndexed: row.last_indexed,
    chunkCount: row.chunk_count,
    summary: row.summary,
  }));
}

export function getFileSummary(db: DatabaseType, filePath: string): string | null {
  const row = db.prepare(`SELECT summary FROM files WHERE file_path = ?`)
    .get(filePath) as { summary: string | null } | undefined;
  return row?.summary ?? null;
}

// --- Transaction Helper ---

export function insertChunksTransaction(db: DatabaseType, chunks: MemoryChunk[]): void {
  const txn = db.transaction((items: MemoryChunk[]) => {
    for (const chunk of items) {
      insertChunk(db, chunk);
    }
  });
  txn(chunks);
}

// --- Embedding Cache ---

export function getCachedEmbedding(db: DatabaseType, hash: string): Float32Array | null {
  const row = db.prepare(
    `SELECT embedding, dims FROM embedding_cache WHERE provider = ? AND model = ? AND hash = ?`,
  ).get(EMBEDDING_PROVIDER, EMBEDDING_MODEL, hash) as { embedding: Buffer; dims: number } | undefined;

  if (!row) return null;
  return new Float32Array(new Uint8Array(row.embedding).buffer);
}

export function setCachedEmbedding(db: DatabaseType, hash: string, embedding: Float32Array): void {
  db.prepare(
    `INSERT OR REPLACE INTO embedding_cache (provider, model, hash, embedding, dims, updated_at) VALUES (?, ?, ?, ?, ?, ?)`,
  ).run(
    EMBEDDING_PROVIDER,
    EMBEDDING_MODEL,
    hash,
    serializeEmbedding(embedding),
    embedding.length,
    Date.now(),
  );
}

// --- Meta ---

export function getMeta(db: DatabaseType, key: string): string | null {
  const row = db.prepare(`SELECT value FROM meta WHERE key = ?`).get(key) as { value: string } | undefined;
  return row?.value ?? null;
}

export function setMeta(db: DatabaseType, key: string, value: string): void {
  db.prepare(`INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)`).run(key, value);
}
