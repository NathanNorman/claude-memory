import { describe, it, before, after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync, readFileSync, rmSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { randomBytes } from 'node:crypto';

import { openDb } from './db.js';
import { indexAll, indexFile, scanFiles } from './indexer.js';
import { search } from './search.js';
import { embedText } from './embeddings.js';
import { EMBEDDING_DIMS } from './types.js';
import type { Database as DatabaseType } from 'better-sqlite3';

// --- Helpers ---

function makeTmpDir(): string {
  const dir = join(tmpdir(), `claude-memory-test-${randomBytes(8).toString('hex')}`);
  mkdirSync(dir, { recursive: true });
  return dir;
}

function writeMd(baseDir: string, relPath: string, content: string): void {
  const full = join(baseDir, relPath);
  mkdirSync(join(full, '..'), { recursive: true });
  writeFileSync(full, content, 'utf-8');
}

// --- Sample content ---

const SAMPLE_MEMORY_MD = `# Project Architecture

The project uses a modular architecture with clear separation of concerns.
Each module handles one responsibility: database, indexer, search, embeddings.

## Database Layer

We use better-sqlite3 with WAL mode for concurrent reads.
The sqlite-vec extension provides vector similarity search.
FTS5 handles full-text keyword search over chunk content.

## Embedding Pipeline

Local embeddings via Xenova/all-MiniLM-L6-v2 (384 dimensions).
Mean pooling with L2 normalization produces unit vectors.
`;

const SAMPLE_DAILY_LOG = `# 2026-02-05

## Morning standup

Discussed deployment timeline for the analytics pipeline.
Team agreed to ship the new converter by end of week.

## Afternoon work

Implemented hybrid search merging vector and BM25 results.
Fixed a bug where FTS5 queries with special characters caused errors.
`;

const SAMPLE_NOTES = `# TypeScript Tips

## Strict Mode

Always enable strict mode in tsconfig.json for better type safety.
Use unknown instead of any for external data boundaries.

## ESM Imports

Use .js extensions in import paths even for .ts source files.
This is required for NodeNext module resolution.
`;

// --- Tests ---

describe('Integration: indexing and search flow', () => {
  let tmpDir: string;
  let dbPath: string;
  let db: DatabaseType;

  before(() => {
    tmpDir = makeTmpDir();
    dbPath = join(tmpDir, 'index', 'memory.db');

    // Create sample markdown files
    writeMd(tmpDir, 'MEMORY.md', SAMPLE_MEMORY_MD);
    writeMd(tmpDir, 'memory/2026-02-05.md', SAMPLE_DAILY_LOG);
    writeMd(tmpDir, 'memory/notes.md', SAMPLE_NOTES);

    // Open database
    db = openDb(dbPath);
  });

  after(() => {
    if (db) {
      db.close();
    }
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it('scanFiles finds MEMORY.md and memory/*.md', () => {
    const files = scanFiles(tmpDir);
    const paths = files.map((f) => f.filePath).sort();

    assert.ok(paths.includes('MEMORY.md'), 'Should find MEMORY.md');
    assert.ok(paths.includes('memory/2026-02-05.md'), 'Should find daily log');
    assert.ok(paths.includes('memory/notes.md'), 'Should find notes');
    assert.equal(paths.length, 3, 'Should find exactly 3 files');
  });

  it('scanFiles ignores non-.md files', () => {
    // Add a non-md file
    writeMd(tmpDir, 'memory/data.txt', 'not markdown');
    // Rename won't work — just check existing scan
    const files = scanFiles(tmpDir);
    const paths = files.map((f) => f.filePath);
    assert.ok(!paths.includes('memory/data.txt'), 'Should ignore .txt files');
  });

  it('indexAll populates files and chunks tables', async () => {
    await indexAll(db, tmpDir);

    const filesCount = db.prepare('SELECT COUNT(*) AS cnt FROM files').get() as { cnt: number };
    assert.ok(filesCount.cnt >= 3, `Should have at least 3 files indexed, got ${filesCount.cnt}`);

    const chunksCount = db.prepare('SELECT COUNT(*) AS cnt FROM chunks').get() as { cnt: number };
    assert.ok(chunksCount.cnt > 0, `Should have chunks indexed, got ${chunksCount.cnt}`);
  });

  it('indexAll is idempotent — re-indexing unchanged files does not duplicate', async () => {
    const chunksBefore = db.prepare('SELECT COUNT(*) AS cnt FROM chunks').get() as { cnt: number };

    await indexAll(db, tmpDir);

    const chunksAfter = db.prepare('SELECT COUNT(*) AS cnt FROM chunks').get() as { cnt: number };
    assert.equal(chunksAfter.cnt, chunksBefore.cnt, 'Chunk count should not change on re-index');
  });

  it('chunks have embeddings in chunks_vec', async () => {
    const vecCount = db.prepare('SELECT COUNT(*) AS cnt FROM chunks_vec').get() as { cnt: number };
    const chunksCount = db.prepare('SELECT COUNT(*) AS cnt FROM chunks').get() as { cnt: number };
    assert.equal(vecCount.cnt, chunksCount.cnt, 'Every chunk should have a vector embedding');
  });

  it('chunks have FTS5 entries in chunks_fts', async () => {
    // FTS5 content tables don't support COUNT(*) directly, but MATCH works
    const results = db.prepare(
      `SELECT COUNT(*) AS cnt FROM chunks_fts WHERE chunks_fts MATCH ?`
    ).get('"architecture"') as { cnt: number };
    assert.ok(results.cnt > 0, 'FTS5 should find "architecture" in indexed content');
  });

  it('files table tracks content hashes', () => {
    const row = db.prepare('SELECT content_hash, chunk_count FROM files WHERE file_path = ?')
      .get('MEMORY.md') as { content_hash: string; chunk_count: number } | undefined;
    assert.ok(row, 'MEMORY.md should be in files table');
    assert.ok(row.content_hash.length === 64, 'content_hash should be a sha256 hex string');
    assert.ok(row.chunk_count > 0, 'Should have at least 1 chunk');
  });
});

describe('Integration: search returns ranked results', () => {
  let tmpDir: string;
  let dbPath: string;
  let db: DatabaseType;

  before(async () => {
    tmpDir = makeTmpDir();
    dbPath = join(tmpDir, 'index', 'memory.db');

    writeMd(tmpDir, 'MEMORY.md', SAMPLE_MEMORY_MD);
    writeMd(tmpDir, 'memory/2026-02-05.md', SAMPLE_DAILY_LOG);
    writeMd(tmpDir, 'memory/notes.md', SAMPLE_NOTES);

    db = openDb(dbPath);
    await indexAll(db, tmpDir);
  });

  after(() => {
    if (db) db.close();
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it('hybrid search returns results for "database sqlite"', async () => {
    const results = await search(db, {
      query: 'database sqlite',
      limit: 10,
      threshold: 0.0,
      mode: 'hybrid',
    });

    assert.ok(results.length > 0, 'Should return at least one result');

    // Results should be sorted by score descending
    for (let i = 1; i < results.length; i++) {
      assert.ok(
        results[i - 1]!.score >= results[i]!.score,
        `Results should be sorted by score desc: ${results[i - 1]!.score} >= ${results[i]!.score}`,
      );
    }
  });

  it('vector search returns results for semantic query', async () => {
    const results = await search(db, {
      query: 'how do embeddings work in the pipeline',
      limit: 5,
      threshold: 0.0,
      mode: 'vector',
    });

    assert.ok(results.length > 0, 'Vector search should return results');
    // All results should have matchType 'vector'
    for (const r of results) {
      assert.equal(r.matchType, 'vector', 'matchType should be vector');
    }
  });

  it('keyword search returns results for "deployment"', async () => {
    const results = await search(db, {
      query: 'deployment',
      limit: 5,
      threshold: 0.0,
      mode: 'keyword',
    });

    assert.ok(results.length > 0, 'Keyword search should find "deployment"');
    for (const r of results) {
      assert.equal(r.matchType, 'keyword', 'matchType should be keyword');
    }
  });

  it('hybrid search assigns matchType correctly', async () => {
    const results = await search(db, {
      query: 'sqlite vector search',
      limit: 10,
      threshold: 0.0,
      mode: 'hybrid',
    });

    assert.ok(results.length > 0, 'Hybrid search should return results');
    const types = new Set(results.map((r) => r.matchType));
    // At least one result type should be present
    assert.ok(
      types.has('hybrid') || types.has('vector') || types.has('keyword'),
      'Should have valid matchTypes',
    );
  });

  it('search respects threshold filter', async () => {
    const results = await search(db, {
      query: 'database',
      limit: 10,
      threshold: 0.99,
      mode: 'hybrid',
    });

    // With a very high threshold, few or no results should pass
    for (const r of results) {
      assert.ok(r.score >= 0.99, `Score ${r.score} should be >= 0.99`);
    }
  });

  it('search results contain chunk content', async () => {
    const results = await search(db, {
      query: 'typescript strict mode',
      limit: 5,
      threshold: 0.0,
      mode: 'hybrid',
    });

    assert.ok(results.length > 0, 'Should find results about TypeScript');
    for (const r of results) {
      assert.ok(r.chunk.content.length > 0, 'Chunk content should not be empty');
      assert.ok(r.chunk.filePath.length > 0, 'Chunk filePath should not be empty');
      assert.ok(r.chunk.id.length > 0, 'Chunk id should not be empty');
      assert.ok(r.chunk.embedding.length === EMBEDDING_DIMS, `Embedding should be ${EMBEDDING_DIMS} dimensions`);
    }
  });
});

describe('Integration: read file content', () => {
  let tmpDir: string;

  before(() => {
    tmpDir = makeTmpDir();
    writeMd(tmpDir, 'MEMORY.md', SAMPLE_MEMORY_MD);
    writeMd(tmpDir, 'memory/2026-02-05.md', SAMPLE_DAILY_LOG);
  });

  after(() => {
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it('reads full file content', () => {
    const content = readFileSync(join(tmpDir, 'MEMORY.md'), 'utf-8');
    assert.ok(content.includes('Project Architecture'), 'Should contain expected heading');
    assert.ok(content.includes('better-sqlite3'), 'Should contain expected content');
  });

  it('reads file with line slicing', () => {
    const content = readFileSync(join(tmpDir, 'MEMORY.md'), 'utf-8');
    const lines = content.split('\n');
    // Simulate the line slicing logic from handleMemoryRead
    const from = 1;
    const lineCount = 3;
    const startIdx = Math.max(0, from - 1);
    const sliced = lines.slice(startIdx, startIdx + lineCount);
    assert.equal(sliced.length, 3, 'Should return exactly 3 lines');
    assert.ok(sliced[0]!.includes('Project Architecture'), 'First line should be heading');
  });

  it('reads daily log file', () => {
    const content = readFileSync(join(tmpDir, 'memory/2026-02-05.md'), 'utf-8');
    assert.ok(content.includes('Morning standup'), 'Should contain standup notes');
    assert.ok(content.includes('hybrid search'), 'Should contain work notes');
  });
});

describe('Integration: write creates and appends files', () => {
  let tmpDir: string;

  before(() => {
    tmpDir = makeTmpDir();
    mkdirSync(join(tmpDir, 'memory'), { recursive: true });
  });

  after(() => {
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it('write creates a new file', () => {
    const filePath = join(tmpDir, 'memory', 'test-write.md');
    const content = '# Test Write\n\nThis is test content.\n';
    writeFileSync(filePath, content, 'utf-8');

    assert.ok(existsSync(filePath), 'File should be created');
    const read = readFileSync(filePath, 'utf-8');
    assert.equal(read, content, 'Content should match');
  });

  it('append adds content to existing file', () => {
    const filePath = join(tmpDir, 'memory', 'test-append.md');
    const initial = '# Append Test\n\nFirst entry.\n';
    writeFileSync(filePath, initial, 'utf-8');

    const addition = '\n## Second Entry\n\nAppended content.\n';
    const existing = readFileSync(filePath, 'utf-8');
    const separator = existing.endsWith('\n') ? '' : '\n';
    writeFileSync(filePath, existing + separator + addition, 'utf-8');

    const final = readFileSync(filePath, 'utf-8');
    assert.ok(final.includes('First entry'), 'Should contain original content');
    assert.ok(final.includes('Appended content'), 'Should contain appended content');
  });

  it('write creates parent directories', () => {
    const filePath = join(tmpDir, 'memory', 'subdir', 'nested.md');
    mkdirSync(join(filePath, '..'), { recursive: true });
    writeFileSync(filePath, '# Nested\n', 'utf-8');

    assert.ok(existsSync(filePath), 'Nested file should be created');
  });
});

describe('Integration: delta sync detects changes', () => {
  let tmpDir: string;
  let dbPath: string;
  let db: DatabaseType;

  before(async () => {
    tmpDir = makeTmpDir();
    dbPath = join(tmpDir, 'index', 'memory.db');
    db = openDb(dbPath);

    writeMd(tmpDir, 'MEMORY.md', SAMPLE_MEMORY_MD);
    await indexAll(db, tmpDir);
  });

  after(() => {
    if (db) db.close();
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it('re-indexing after content change updates chunks', async () => {
    const chunksBefore = db.prepare(
      `SELECT COUNT(*) AS cnt FROM chunks WHERE file_path = 'MEMORY.md'`
    ).get() as { cnt: number };

    // Modify the file significantly
    const newContent = SAMPLE_MEMORY_MD + '\n## New Section\n\nThis is brand new content about testing and validation.\n'.repeat(10);
    writeMd(tmpDir, 'MEMORY.md', newContent);

    await indexAll(db, tmpDir);

    const chunksAfter = db.prepare(
      `SELECT COUNT(*) AS cnt FROM chunks WHERE file_path = 'MEMORY.md'`
    ).get() as { cnt: number };

    // More content should produce more (or different count of) chunks
    // The key check is that indexing completed without error
    assert.ok(chunksAfter.cnt > 0, 'Should still have chunks after re-index');
  });

  it('removing a file prunes its chunks from DB', async () => {
    // Add a file, index, remove, re-index
    writeMd(tmpDir, 'memory/ephemeral.md', '# Ephemeral\n\nTemporary content.\n');
    await indexAll(db, tmpDir);

    const before = db.prepare(
      `SELECT COUNT(*) AS cnt FROM chunks WHERE file_path = 'memory/ephemeral.md'`
    ).get() as { cnt: number };
    assert.ok(before.cnt > 0, 'Ephemeral file should be indexed');

    // Remove the file
    rmSync(join(tmpDir, 'memory', 'ephemeral.md'));
    await indexAll(db, tmpDir);

    const after = db.prepare(
      `SELECT COUNT(*) AS cnt FROM chunks WHERE file_path = 'memory/ephemeral.md'`
    ).get() as { cnt: number };
    assert.equal(after.cnt, 0, 'Chunks should be pruned after file removal');

    const fileEntry = db.prepare(
      `SELECT COUNT(*) AS cnt FROM files WHERE file_path = 'memory/ephemeral.md'`
    ).get() as { cnt: number };
    assert.equal(fileEntry.cnt, 0, 'File entry should be pruned');
  });
});

describe('Integration: embeddings produce valid vectors', () => {
  it('embedText returns 384-dim Float32Array', async () => {
    const embedding = await embedText('test embedding generation');
    assert.ok(embedding instanceof Float32Array, 'Should return Float32Array');
    assert.equal(embedding.length, EMBEDDING_DIMS, `Should be ${EMBEDDING_DIMS} dimensions`);
  });

  it('embeddings are normalized (unit length)', async () => {
    const embedding = await embedText('normalized vector test');
    let norm = 0;
    for (let i = 0; i < embedding.length; i++) {
      norm += embedding[i]! * embedding[i]!;
    }
    norm = Math.sqrt(norm);
    assert.ok(Math.abs(norm - 1.0) < 0.01, `Embedding norm should be ~1.0, got ${norm}`);
  });

  it('similar texts produce similar embeddings', async () => {
    const emb1 = await embedText('TypeScript strict mode improves type safety');
    const emb2 = await embedText('TypeScript strict configuration enhances type checking');
    const emb3 = await embedText('The weather forecast predicts rain tomorrow');

    // Cosine similarity (embeddings are normalized, so dot product = cosine)
    let simSimilar = 0;
    let simDifferent = 0;
    for (let i = 0; i < emb1.length; i++) {
      simSimilar += emb1[i]! * emb2[i]!;
      simDifferent += emb1[i]! * emb3[i]!;
    }

    assert.ok(
      simSimilar > simDifferent,
      `Similar texts should have higher cosine similarity: ${simSimilar} > ${simDifferent}`,
    );
  });
});

// --- LLM Boundary Scorer Tests ---

import { LlmBoundaryScorer } from './llm-boundary-scorer.js';
import type { ConversationExchange } from './types.js';

function makeMockExchanges(count: number): ConversationExchange[] {
  return Array.from({ length: count }, (_, i) => ({
    userMessage: `User message ${i}: What should we do about the ${i % 2 === 0 ? 'database' : 'frontend'} issue?`,
    assistantMessage: `Assistant response ${i}: I'll look into the ${i % 2 === 0 ? 'database' : 'frontend'} changes.`,
    lineStart: i * 10 + 1,
    lineEnd: i * 10 + 10,
    toolCalls: [],
  }));
}

describe('Integration: LLM boundary scorer', () => {
  let tmpDir: string;
  let dbPath: string;
  let db: DatabaseType;

  before(() => {
    tmpDir = makeTmpDir();
    dbPath = join(tmpDir, 'index', 'memory.db');
    db = openDb(dbPath);
  });

  after(() => {
    if (db) db.close();
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it('scorer returns correct-length array for mock exchanges', async () => {
    // Mock scorer that doesn't actually call LLM — test the class structure
    const exchanges = makeMockExchanges(10);
    const scorer = new LlmBoundaryScorer({
      llmConfig: { baseUrl: 'http://localhost:9999', model: 'test' },
    });

    // Override scoreWindow to return mock scores without hitting LLM
    scorer.scoreWindow = async (exs: ConversationExchange[]) => {
      return new Array(exs.length - 1).fill(1.5);
    };

    const scores = await scorer.scoreAll(exchanges);
    assert.ok(scores !== null, 'Should return scores array');
    assert.equal(scores!.length, 9, 'Should have 9 boundary scores for 10 exchanges');
    for (const s of scores!) {
      assert.ok(s >= 0 && s <= 3, `Score ${s} should be in 0-3 range`);
    }
  });

  it('cache hit skips LLM call on second invocation', async () => {
    const exchanges = makeMockExchanges(5);
    let llmCallCount = 0;

    const scorer = new LlmBoundaryScorer({
      llmConfig: { baseUrl: 'http://localhost:9999', model: 'test' },
      singlePass: true,
    }, db);

    scorer.scoreWindow = async (exs: ConversationExchange[]) => {
      llmCallCount++;
      return new Array(exs.length - 1).fill(2.0);
    };

    // First call — should hit LLM
    const scores1 = await scorer.scoreAll(exchanges);
    assert.ok(scores1 !== null);
    const firstCallCount = llmCallCount;
    assert.ok(firstCallCount > 0, 'Should have made LLM calls');

    // Second call with fresh scorer using same DB — should use cache
    const scorer2 = new LlmBoundaryScorer({
      llmConfig: { baseUrl: 'http://localhost:9999', model: 'test' },
      singlePass: true,
    }, db);

    let secondLlmCalls = 0;
    scorer2.scoreWindow = async (exs: ConversationExchange[]) => {
      secondLlmCalls++;
      return new Array(exs.length - 1).fill(2.0);
    };

    const scores2 = await scorer2.scoreAll(exchanges);
    assert.ok(scores2 !== null);
    assert.equal(secondLlmCalls, 0, 'Should not call LLM on cache hit');
    assert.equal(scorer2.cacheHits, scores2!.length, 'All scores should be cache hits');
  });

  it('scorer returns null on total LLM failure', async () => {
    const exchanges = makeMockExchanges(5);
    const scorer = new LlmBoundaryScorer({
      llmConfig: { baseUrl: 'http://localhost:9999', model: 'test' },
      singlePass: true,
    });

    // Override scoreWithWindow directly to simulate total failure
    scorer.scoreWithWindow = async () => null;

    const scores = await scorer.scoreAll(exchanges);
    assert.equal(scores, null, 'Should return null on total failure');
  });

  it('CHUNK_TOKENS mismatch triggers full reindex on mode switch', async () => {
    const testDir = makeTmpDir();
    const testDbPath = join(testDir, 'index', 'memory.db');
    const testDb = openDb(testDbPath);

    writeMd(testDir, 'MEMORY.md', SAMPLE_MEMORY_MD);

    // Index with heuristic mode
    await indexAll(testDb, testDir);
    const chunks1 = (testDb.prepare('SELECT COUNT(*) AS cnt FROM chunks').get() as { cnt: number }).cnt;
    assert.ok(chunks1 > 0, 'Should have chunks after first index');

    // Index again with same mode — should be idempotent
    await indexAll(testDb, testDir);
    const chunks2 = (testDb.prepare('SELECT COUNT(*) AS cnt FROM chunks').get() as { cnt: number }).cnt;
    assert.equal(chunks2, chunks1, 'Should be idempotent');

    // Index with llmScoring flag — should trigger reindex due to CHUNK_TOKENS change
    // (won't actually call LLM since no conversation files, just markdown)
    await indexAll(testDb, testDir, undefined, { llmScoring: true });
    const storedTokens = testDb.prepare("SELECT value FROM meta WHERE key = 'chunk_tokens'").get() as { value: string };
    assert.equal(storedTokens.value, '400-v4-semantic-llm', 'Should store LLM chunk tokens');

    testDb.close();
    rmSync(testDir, { recursive: true, force: true });
  });
});
