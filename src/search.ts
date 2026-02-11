import type { Database as DatabaseType } from 'better-sqlite3';
import type { SearchResult, MemoryChunk } from './types.js';
import { buildFtsQuery, bm25RankToScore, mergeHybridResults } from './hybrid.js';
import { embedText } from './embeddings.js';

// --- Embedding Serialization (matches db.ts pattern) ---

function serializeEmbedding(embedding: Float32Array): Buffer {
  return Buffer.from(embedding.buffer, embedding.byteOffset, embedding.byteLength);
}

function deserializeEmbedding(blob: Buffer): Float32Array {
  return new Float32Array(new Uint8Array(blob).buffer);
}

// --- Vector Search ---

export function searchVector(
  db: DatabaseType,
  queryEmbedding: Float32Array,
  limit: number,
): SearchResult[] {
  if (queryEmbedding.length === 0 || limit <= 0) {
    return [];
  }

  const vecRows = db
    .prepare(
      `SELECT rowid, distance FROM chunks_vec WHERE embedding MATCH ? ORDER BY distance LIMIT ?`,
    )
    .all(serializeEmbedding(queryEmbedding), limit) as Array<{
    rowid: number;
    distance: number;
  }>;

  const results: SearchResult[] = [];

  for (const vecRow of vecRows) {
    const chunkRow = db
      .prepare(
        `SELECT id, file_path, chunk_index, start_line, end_line, title, content, embedding, hash, updated_at FROM chunks WHERE rowid = ?`,
      )
      .get(vecRow.rowid) as
      | {
          id: string;
          file_path: string;
          chunk_index: number;
          start_line: number;
          end_line: number;
          title: string;
          content: string;
          embedding: Buffer;
          hash: string;
          updated_at: number;
        }
      | undefined;

    if (!chunkRow) continue;

    const chunk: MemoryChunk = {
      id: chunkRow.id,
      filePath: chunkRow.file_path,
      chunkIndex: chunkRow.chunk_index,
      startLine: chunkRow.start_line,
      endLine: chunkRow.end_line,
      title: chunkRow.title,
      content: chunkRow.content,
      embedding: deserializeEmbedding(chunkRow.embedding),
      hash: chunkRow.hash,
      updatedAt: chunkRow.updated_at,
    };

    results.push({
      chunk,
      score: 1 - vecRow.distance,
      matchType: 'vector',
    });
  }

  return results;
}

// --- Keyword Search ---

export function searchKeyword(
  db: DatabaseType,
  query: string,
  limit: number,
): SearchResult[] {
  if (limit <= 0) {
    return [];
  }

  const ftsQuery = buildFtsQuery(query);
  if (!ftsQuery) {
    return [];
  }

  const ftsRows = db
    .prepare(
      `SELECT rowid, rank FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?`,
    )
    .all(ftsQuery, limit) as Array<{
    rowid: number;
    rank: number;
  }>;

  const results: SearchResult[] = [];

  for (const ftsRow of ftsRows) {
    const chunkRow = db
      .prepare(
        `SELECT id, file_path, chunk_index, start_line, end_line, title, content, embedding, hash, updated_at FROM chunks WHERE rowid = ?`,
      )
      .get(ftsRow.rowid) as
      | {
          id: string;
          file_path: string;
          chunk_index: number;
          start_line: number;
          end_line: number;
          title: string;
          content: string;
          embedding: Buffer;
          hash: string;
          updated_at: number;
        }
      | undefined;

    if (!chunkRow) continue;

    const chunk: MemoryChunk = {
      id: chunkRow.id,
      filePath: chunkRow.file_path,
      chunkIndex: chunkRow.chunk_index,
      startLine: chunkRow.start_line,
      endLine: chunkRow.end_line,
      title: chunkRow.title,
      content: chunkRow.content,
      embedding: deserializeEmbedding(chunkRow.embedding),
      hash: chunkRow.hash,
      updatedAt: chunkRow.updated_at,
    };

    results.push({
      chunk,
      score: bm25RankToScore(ftsRow.rank),
      matchType: 'keyword',
    });
  }

  return results;
}

// --- Hybrid Search ---

function log(msg: string): void {
  process.stderr.write(`[claude-memory] ${msg}\n`);
}

export function searchHybrid(
  db: DatabaseType,
  queryEmbedding: Float32Array,
  query: string,
  limit: number,
  threshold: number,
): SearchResult[] {
  // Fetch limit*4 candidates from each backend for better hybrid recall
  const candidateCount = limit * 4;

  // Both vec0 and FTS5 can fail when concurrent processes corrupt their
  // internal data structures. Catch errors from each independently so
  // search degrades gracefully instead of failing entirely.
  let vectorHits: SearchResult[];
  try {
    vectorHits = searchVector(db, queryEmbedding, candidateCount);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    log(`Vector search failed (falling back to keyword-only): ${msg}`);
    vectorHits = [];
  }

  let keywordHits: SearchResult[];
  try {
    keywordHits = searchKeyword(db, query, candidateCount);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    log(`Keyword search failed (falling back to vector-only): ${msg}`);
    keywordHits = [];
  }
  const merged = mergeHybridResults(vectorHits, keywordHits);
  let results = merged.filter((r) => r.score >= threshold).slice(0, limit);

  // Keyword fallback: if we have fewer results than requested, pad with
  // remaining keyword hits that weren't already included in the RRF merge.
  // This catches edge cases where both systems return sparse results.
  if (results.length < limit && keywordHits.length > 0) {
    const includedIds = new Set(results.map((r) => r.chunk.id));
    const minRrfScore = results.length > 0
      ? results[results.length - 1].score / 2
      : 0.001;

    // Only pad if fallback score meets threshold
    if (minRrfScore >= threshold) {
      for (const kw of keywordHits) {
        if (results.length >= limit) break;
        if (includedIds.has(kw.chunk.id)) continue;
        results.push({
          chunk: kw.chunk,
          score: minRrfScore,
          matchType: 'keyword',
        });
        includedIds.add(kw.chunk.id);
      }
    }
  }

  return results;
}

// --- Main Export ---

export async function search(
  db: DatabaseType,
  opts: {
    query: string;
    embedding?: Float32Array;
    limit: number;
    threshold: number;
    mode: 'vector' | 'keyword' | 'hybrid';
  },
): Promise<SearchResult[]> {
  switch (opts.mode) {
    case 'vector': {
      const emb = opts.embedding ?? (await embedText(opts.query));
      return searchVector(db, emb, opts.limit);
    }
    case 'keyword': {
      return searchKeyword(db, opts.query, opts.limit);
    }
    case 'hybrid': {
      const emb = opts.embedding ?? (await embedText(opts.query));
      return searchHybrid(db, emb, opts.query, opts.limit, opts.threshold);
    }
  }
}
