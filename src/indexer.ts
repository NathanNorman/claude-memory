import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs';
import { join, resolve } from 'node:path';
import type { Database as DatabaseType } from 'better-sqlite3';
import {
  insertChunksTransaction,
  deleteChunksByFile,
  upsertFile,
  getFile,
  getAllFiles,
  getCachedEmbedding,
  setCachedEmbedding,
  getMeta,
  setMeta,
} from './db.js';
import { chunkMarkdown } from './chunker.js';
import { chunkMarkdownSemantic } from './semantic-markdown-chunker.js';
import { chunkExchangesSemantic } from './semantic-chunker.js';
import { embedText } from './embeddings.js';
import { hashText, EMBEDDING_MODEL } from './types.js';
import type { MemoryChunk } from './types.js';
import { parseConversationJsonl, parseConversationExchanges, MAX_FILE_BYTES } from './conversation-parser.js';
import { scoreAllBoundaries, segmentVarianceDp } from './semantic-chunker.js';
import { LlmBoundaryScorer } from './llm-boundary-scorer.js';
import { evictStaleBoundaryScores } from './db.js';

// --- Meta Keys ---

const META_MODEL = 'embedding_model';
const META_CHUNK_TOKENS = 'chunk_tokens';
const CHUNK_TOKENS_HEURISTIC = '400-v4-semantic-md';
const CHUNK_TOKENS_LLM = '400-v4-semantic-llm';

function getChunkTokens(llmScoring: boolean): string {
  return llmScoring ? CHUNK_TOKENS_LLM : CHUNK_TOKENS_HEURISTIC;
}

/** Prefix for conversation file paths in the DB */
const CONV_PREFIX = 'conversations/';

// --- File Scanning ---

interface ScannedFile {
  filePath: string;
  content: string;
  mtimeMs: number;
}

/** Lightweight entry for conversation files (content loaded lazily) */
interface ScannedConversationFile {
  /** DB-relative path: conversations/<projectDir>/<filename> */
  filePath: string;
  /** Absolute path on disk */
  absolutePath: string;
  mtimeMs: number;
}

/**
 * Recursively scan memoryDir for MEMORY.md and memory/*.md files.
 * Returns relative paths from memoryDir with content and mtime.
 */
export function scanFiles(memoryDir: string): ScannedFile[] {
  const resolved = resolve(memoryDir);
  const results: ScannedFile[] = [];

  // Check for MEMORY.md at root
  const memoryMdPath = join(resolved, 'MEMORY.md');
  if (existsSync(memoryMdPath)) {
    const stat = statSync(memoryMdPath);
    if (stat.isFile()) {
      results.push({
        filePath: 'MEMORY.md',
        content: readFileSync(memoryMdPath, 'utf-8'),
        mtimeMs: stat.mtimeMs,
      });
    }
  }

  // Scan memory/ subdirectory for *.md files
  const memorySubdir = join(resolved, 'memory');
  if (existsSync(memorySubdir) && statSync(memorySubdir).isDirectory()) {
    scanDir(resolved, memorySubdir, 'memory', results);
  }

  return results;
}

function scanDir(rootDir: string, dirPath: string, relativeTo: string, results: ScannedFile[]): void {
  const entries = readdirSync(dirPath);
  for (const entry of entries) {
    const fullPath = join(dirPath, entry);
    const relPath = join(relativeTo, entry);
    const stat = statSync(fullPath);
    if (stat.isDirectory()) {
      scanDir(rootDir, fullPath, relPath, results);
    } else if (stat.isFile() && entry.endsWith('.md')) {
      results.push({
        filePath: relPath,
        content: readFileSync(fullPath, 'utf-8'),
        mtimeMs: stat.mtimeMs,
      });
    }
  }
}

// --- Conversation Scanning ---

/**
 * Scan the conversation archive directory for non-agent JSONL files.
 * Returns lightweight entries with paths and mtimes (content loaded lazily).
 */
export function scanConversations(archiveDir: string): ScannedConversationFile[] {
  const resolved = resolve(archiveDir);
  if (!existsSync(resolved)) return [];

  const results: ScannedConversationFile[] = [];
  let dirs: string[];
  try {
    dirs = readdirSync(resolved);
  } catch {
    return [];
  }

  for (const projectDir of dirs) {
    const projectPath = join(resolved, projectDir);
    let dirStat;
    try {
      dirStat = statSync(projectPath);
    } catch {
      continue;
    }
    if (!dirStat.isDirectory()) continue;

    let files: string[];
    try {
      files = readdirSync(projectPath);
    } catch {
      continue;
    }

    for (const file of files) {
      // Skip agent files, non-JSONL files
      if (!file.endsWith('.jsonl')) continue;
      if (file.startsWith('agent-')) continue;

      const fullPath = join(projectPath, file);
      let fileStat;
      try {
        fileStat = statSync(fullPath);
      } catch {
        continue;
      }
      if (!fileStat.isFile()) continue;
      if (fileStat.size === 0 || fileStat.size > MAX_FILE_BYTES) continue;

      results.push({
        filePath: `${CONV_PREFIX}${projectDir}/${file}`,
        absolutePath: fullPath,
        mtimeMs: fileStat.mtimeMs,
      });
    }
  }

  return results;
}

// --- Staleness Check ---

/**
 * Quick check whether any memory files have changed since last index.
 * Returns true if reindexing is needed, false if everything is fresh.
 */
export function isIndexStale(db: DatabaseType, memoryDir: string, archiveDir?: string): boolean {
  const resolved = resolve(memoryDir);

  // Check model/config hasn't changed
  const storedModel = getMeta(db, META_MODEL);
  if (storedModel !== EMBEDDING_MODEL) return true;
  const storedTokens = getMeta(db, META_CHUNK_TOKENS);
  if (storedTokens !== CHUNK_TOKENS_HEURISTIC && storedTokens !== CHUNK_TOKENS_LLM) return true;

  // Quick mtime scan — compare file mtimes against last_indexed
  const dbFiles = getAllFiles(db);
  const dbMap = new Map(dbFiles.map((f) => [f.filePath, f]));

  // Check MEMORY.md
  const memoryMdPath = join(resolved, 'MEMORY.md');
  if (existsSync(memoryMdPath)) {
    const stat = statSync(memoryMdPath);
    if (stat.isFile()) {
      const entry = dbMap.get('MEMORY.md');
      if (!entry || stat.mtimeMs > entry.lastIndexed) return true;
      dbMap.delete('MEMORY.md');
    }
  } else if (dbMap.has('MEMORY.md')) {
    return true; // file was deleted
  }

  // Check memory/ directory
  const memorySubdir = join(resolved, 'memory');
  if (existsSync(memorySubdir) && statSync(memorySubdir).isDirectory()) {
    if (isDirStale(memorySubdir, 'memory', dbMap)) return true;
  }

  // Check conversation archives if provided
  if (archiveDir) {
    const convFiles = scanConversations(archiveDir);
    const convPaths = new Set(convFiles.map((f) => f.filePath));

    for (const conv of convFiles) {
      const entry = dbMap.get(conv.filePath);
      if (!entry || conv.mtimeMs > entry.lastIndexed) return true;
      dbMap.delete(conv.filePath);
    }

    // Check for deleted conversation files still in DB
    for (const [path] of dbMap) {
      if (path.startsWith(CONV_PREFIX) && !convPaths.has(path)) return true;
    }

    // Remove conversation paths from dbMap so they don't trigger the
    // "remaining entries = stale" check below
    for (const [path] of dbMap) {
      if (path.startsWith(CONV_PREFIX)) dbMap.delete(path);
    }
  } else {
    // No archiveDir: ignore conversation entries in dbMap
    for (const [path] of dbMap) {
      if (path.startsWith(CONV_PREFIX)) dbMap.delete(path);
    }
  }

  // Any remaining DB entries are stale (files removed from disk)
  if (dbMap.size > 0) return true;

  return false;
}

function isDirStale(dirPath: string, relativeTo: string, dbMap: Map<string, { lastIndexed: number }>): boolean {
  const entries = readdirSync(dirPath);
  for (const entry of entries) {
    const fullPath = join(dirPath, entry);
    const relPath = join(relativeTo, entry);
    const stat = statSync(fullPath);
    if (stat.isDirectory()) {
      if (isDirStale(fullPath, relPath, dbMap)) return true;
    } else if (stat.isFile() && entry.endsWith('.md')) {
      const dbEntry = dbMap.get(relPath);
      if (!dbEntry || stat.mtimeMs > dbEntry.lastIndexed) return true;
      dbMap.delete(relPath);
    }
  }
  return false;
}

// --- Title Extraction ---

function extractTitle(content: string, filePath: string): string {
  const match = content.match(/^#+\s+(.+)$/m);
  if (match?.[1]) {
    return match[1].trim();
  }
  const parts = filePath.split('/');
  const filename = parts[parts.length - 1] ?? filePath;
  return filename.replace(/\.md$/, '').replace(/\.jsonl$/, '');
}

// --- Cache-Aware Embedding ---

/**
 * Embed chunks using cache: check embedding_cache for each chunk hash,
 * only generate embeddings for uncached chunks, store new ones in cache.
 */
async function embedChunksWithCache(
  db: DatabaseType,
  chunkTexts: string[],
  chunkHashes: string[],
): Promise<Float32Array[]> {
  const results: Float32Array[] = new Array(chunkTexts.length);
  const uncachedIndices: number[] = [];

  // Check cache for each chunk
  for (let i = 0; i < chunkHashes.length; i++) {
    const cached = getCachedEmbedding(db, chunkHashes[i]!);
    if (cached) {
      results[i] = cached;
    } else {
      uncachedIndices.push(i);
    }
  }

  // Generate embeddings for uncached chunks
  for (const idx of uncachedIndices) {
    const embedding = await embedText(chunkTexts[idx]!);
    results[idx] = embedding;
    setCachedEmbedding(db, chunkHashes[idx]!, embedding);
  }

  return results;
}

// --- Single File Indexing ---

export async function indexFile(
  db: DatabaseType,
  filePath: string,
  content: string,
): Promise<void> {
  const contentHash = hashText(content);

  // Check if file already indexed with same hash — skip if unchanged
  const existing = getFile(db, filePath);
  if (existing && existing.contentHash === contentHash) {
    return;
  }

  // Delete old chunks for this file
  deleteChunksByFile(db, filePath);

  // Chunk the content using semantic markdown chunker
  const rawChunks = chunkMarkdownSemantic(content);
  if (rawChunks.length === 0) {
    upsertFile(db, {
      filePath,
      contentHash,
      lastIndexed: Date.now(),
      chunkCount: 0,
    });
    return;
  }

  // Embed with cache awareness
  const texts = rawChunks.map((c) => c.text);
  const hashes = rawChunks.map((c) => c.hash);
  const embeddings = await embedChunksWithCache(db, texts, hashes);

  // Build MemoryChunk objects
  const chunks: MemoryChunk[] = rawChunks.map((raw, idx) => ({
    id: hashText(filePath + ':' + String(idx)),
    filePath,
    chunkIndex: idx,
    startLine: raw.startLine,
    endLine: raw.endLine,
    title: extractTitle(raw.text, filePath),
    content: raw.text,
    embedding: embeddings[idx]!,
    hash: raw.hash,
    updatedAt: Date.now(),
  }));

  // Insert all chunks in a transaction
  insertChunksTransaction(db, chunks);

  // Upsert file entry
  upsertFile(db, {
    filePath,
    contentHash,
    lastIndexed: Date.now(),
    chunkCount: chunks.length,
  });
}

// --- Conversation Chunking with Pre-computed Scores ---

import type { ConversationExchange, ExchangeChunk } from './types.js';

/**
 * Chunk exchanges using pre-computed boundary scores (from LLM scorer).
 * Runs DP segmentation then builds ExchangeChunk objects.
 */
function chunkExchangesWithScores(
  exchanges: ConversationExchange[],
  scores: number[],
): ExchangeChunk[] {
  if (exchanges.length === 0) return [];

  const segments = segmentVarianceDp(exchanges, scores, {
    minChunkTokens: 150,
    maxChunkTokens: 1600,
    varianceWeight: 0.3,
  });

  const chunks: ExchangeChunk[] = [];
  for (const [start, end] of segments) {
    const segExchanges = exchanges.slice(start, end + 1);
    const toolSet = new Set<string>();
    for (const ex of segExchanges) {
      for (const tc of ex.toolCalls) {
        toolSet.add(tc.toolName);
      }
    }
    const toolNames = Array.from(toolSet).sort();

    const textParts = segExchanges.map((ex) => {
      let text = `User: ${ex.userMessage}`;
      if (ex.assistantMessage) text += `\n\nAssistant: ${ex.assistantMessage}`;
      return text;
    });
    let text = textParts.join('\n\n---\n\n');
    if (toolNames.length > 0) {
      text += `\n\nTools: ${toolNames.join(', ')}`;
    }

    chunks.push({
      exchanges: segExchanges,
      startLine: segExchanges[0]!.lineStart,
      endLine: segExchanges[segExchanges.length - 1]!.lineEnd,
      text,
      toolNames,
      hash: hashText(text),
    });
  }
  return chunks;
}

// --- Conversation File Indexing ---

/**
 * Try to load a cached summary file for a conversation.
 * Episodic-memory generates these as <session-id>-summary.txt alongside the .jsonl.
 */
function loadConversationSummary(absolutePath: string): string | null {
  const summaryPath = absolutePath.replace(/\.jsonl$/, '-summary.txt');
  if (!existsSync(summaryPath)) return null;
  try {
    const text = readFileSync(summaryPath, 'utf-8').trim();
    return text.length > 0 ? text : null;
  } catch {
    return null;
  }
}

/**
 * Index a single conversation JSONL file using exchange-aware chunking.
 * Uses mtime check first, then content hash to avoid re-parsing unchanged files.
 */
async function indexConversationFile(
  db: DatabaseType,
  conv: ScannedConversationFile,
  options?: { llmScoring?: boolean; llmScorer?: LlmBoundaryScorer },
): Promise<void> {
  // Quick mtime check — skip if file hasn't been modified since last index
  const existing = getFile(db, conv.filePath);
  if (existing && conv.mtimeMs <= existing.lastIndexed) {
    return;
  }

  // Extract the project directory name from the filePath
  // filePath = conversations/<projectDir>/<filename>
  const parts = conv.filePath.split('/');
  const projectDir = parts[1] ?? 'unknown';

  // Parse into structured exchanges
  const parsed = parseConversationExchanges(conv.absolutePath);
  if (!parsed || parsed.exchanges.length === 0) {
    // Fall back to flat parsing for edge cases (e.g. summary-only files)
    const text = parseConversationJsonl(conv.absolutePath, projectDir);
    if (!text) {
      upsertFile(db, {
        filePath: conv.filePath,
        contentHash: 'empty',
        lastIndexed: Date.now(),
        chunkCount: 0,
      });
      return;
    }
    // Use old path for fallback
    const contentHash = hashText(text);
    if (existing && existing.contentHash === contentHash) {
      upsertFile(db, { ...existing, lastIndexed: Date.now() });
      return;
    }
    deleteChunksByFile(db, conv.filePath);
    const rawChunks = chunkMarkdown(text);
    if (rawChunks.length === 0) {
      upsertFile(db, { filePath: conv.filePath, contentHash, lastIndexed: Date.now(), chunkCount: 0 });
      return;
    }
    const texts = rawChunks.map((c) => c.text);
    const hashes = rawChunks.map((c) => c.hash);
    const embeddings = await embedChunksWithCache(db, texts, hashes);
    const chunks: MemoryChunk[] = rawChunks.map((raw, idx) => ({
      id: hashText(conv.filePath + ':' + String(idx)),
      filePath: conv.filePath,
      chunkIndex: idx,
      startLine: raw.startLine,
      endLine: raw.endLine,
      title: extractTitle(raw.text, conv.filePath),
      content: raw.text,
      embedding: embeddings[idx]!,
      hash: raw.hash,
      updatedAt: Date.now(),
    }));
    insertChunksTransaction(db, chunks);
    const summary = loadConversationSummary(conv.absolutePath);
    upsertFile(db, { filePath: conv.filePath, contentHash, lastIndexed: Date.now(), chunkCount: chunks.length, summary });
    return;
  }

  // Build a stable content hash from all exchange text
  const allExchangeText = parsed.exchanges.map(
    (ex) => `${ex.userMessage}\n${ex.assistantMessage}`,
  ).join('\n');
  const contentHash = hashText(allExchangeText);
  if (existing && existing.contentHash === contentHash) {
    upsertFile(db, { ...existing, lastIndexed: Date.now() });
    return;
  }

  // Delete old chunks
  deleteChunksByFile(db, conv.filePath);

  // Chunk exchanges using semantic boundary detection + DP segmentation
  let exchangeChunks;
  if (options?.llmScoring && options.llmScorer && parsed.exchanges.length > 1) {
    // LLM scoring path: score boundaries with LLM, then DP segment
    const llmScores = await options.llmScorer.scoreAll(parsed.exchanges);
    if (llmScores !== null) {
      exchangeChunks = chunkExchangesWithScores(parsed.exchanges, llmScores);
    } else {
      process.stderr.write(`[claude-memory] LLM scoring failed for ${conv.filePath}, falling back to heuristic\n`);
      exchangeChunks = chunkExchangesSemantic(parsed.exchanges);
    }
  } else {
    exchangeChunks = chunkExchangesSemantic(parsed.exchanges);
  }
  if (exchangeChunks.length === 0) {
    upsertFile(db, {
      filePath: conv.filePath,
      contentHash,
      lastIndexed: Date.now(),
      chunkCount: 0,
    });
    return;
  }

  // Build rich title: "projectDir | date | Tools: X, Y"
  const date = parsed.metadata.timestamp?.slice(0, 10) ?? '';
  function buildChunkTitle(toolNames: string[]): string {
    const parts: string[] = [projectDir];
    if (date) parts.push(date);
    if (toolNames.length > 0) parts.push(`Tools: ${toolNames.join(', ')}`);
    return parts.join(' | ');
  }

  // Embed with cache awareness
  const texts = exchangeChunks.map((c) => c.text);
  const hashes = exchangeChunks.map((c) => c.hash);
  const embeddings = await embedChunksWithCache(db, texts, hashes);

  const chunks: MemoryChunk[] = exchangeChunks.map((ec, idx) => ({
    id: hashText(conv.filePath + ':' + String(idx)),
    filePath: conv.filePath,
    chunkIndex: idx,
    startLine: ec.startLine,
    endLine: ec.endLine,
    title: buildChunkTitle(ec.toolNames),
    content: ec.text,
    embedding: embeddings[idx]!,
    hash: ec.hash,
    updatedAt: Date.now(),
  }));

  insertChunksTransaction(db, chunks);

  // Load cached summary from episodic-memory
  const summary = loadConversationSummary(conv.absolutePath);

  upsertFile(db, {
    filePath: conv.filePath,
    contentHash,
    lastIndexed: Date.now(),
    chunkCount: chunks.length,
    summary,
  });
}

// --- Full Index ---

export async function indexAll(
  db: DatabaseType,
  memoryDir: string,
  archiveDir?: string,
  options?: { llmScoring?: boolean; llmScorer?: LlmBoundaryScorer },
): Promise<{ files: number; chunks: number }> {
  const chunkTokens = getChunkTokens(options?.llmScoring ?? false);

  // Check if model or chunking config changed — force full reindex
  const storedModel = getMeta(db, META_MODEL);
  const storedTokens = getMeta(db, META_CHUNK_TOKENS);
  if (storedModel !== EMBEDDING_MODEL || storedTokens !== chunkTokens) {
    // Config changed: wipe all indexed data and reindex from scratch
    db.exec('DELETE FROM chunks_fts');
    db.exec('DELETE FROM chunks_vec');
    db.exec('DELETE FROM chunks');
    db.exec('DELETE FROM files');
    setMeta(db, META_MODEL, EMBEDDING_MODEL);
    setMeta(db, META_CHUNK_TOKENS, chunkTokens);
  }

  // --- Memory files ---
  const scanned = scanFiles(memoryDir);
  const allPaths = new Set(scanned.map((f) => f.filePath));

  for (const file of scanned) {
    await indexFile(db, file.filePath, file.content);
  }

  // --- Conversation archives ---
  if (archiveDir) {
    const convFiles = scanConversations(archiveDir);
    for (const conv of convFiles) {
      allPaths.add(conv.filePath);
    }

    let indexed = 0;
    for (const conv of convFiles) {
      await indexConversationFile(db, conv, {
        llmScoring: options?.llmScoring,
        llmScorer: options?.llmScorer,
      });
      indexed++;
      if (indexed % 100 === 0) {
        process.stderr.write(`[claude-memory] Indexed ${indexed}/${convFiles.length} conversations\n`);
      }
    }
    if (convFiles.length > 0 && convFiles.length % 100 !== 0) {
      process.stderr.write(`[claude-memory] Indexed ${convFiles.length}/${convFiles.length} conversations\n`);
    }
  }

  // --- Prune stale MEMORY files only ---
  // NEVER prune conversation chunks — Claude Code deletes old .jsonl files,
  // but the index is the only surviving copy of that knowledge.
  // Only prune memory/*.md and MEMORY.md files that were intentionally removed.
  const dbFiles = getAllFiles(db);
  for (const dbFile of dbFiles) {
    if (!allPaths.has(dbFile.filePath)) {
      if (!dbFile.filePath.startsWith(CONV_PREFIX)) {
        deleteChunksByFile(db, dbFile.filePath);
        db.prepare('DELETE FROM files WHERE file_path = ?').run(dbFile.filePath);
      }
    }
  }

  // Evict stale boundary score cache entries
  evictStaleBoundaryScores(db);

  // Return stats
  const filesCount = (db.prepare('SELECT COUNT(*) AS cnt FROM files').get() as { cnt: number }).cnt;
  const chunksCount = (db.prepare('SELECT COUNT(*) AS cnt FROM chunks').get() as { cnt: number }).cnt;
  return { files: filesCount, chunks: chunksCount };
}
