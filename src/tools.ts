import { readFileSync, writeFileSync, mkdirSync, existsSync, openSync, closeSync, unlinkSync, constants as fsConstants } from 'node:fs';
import { join, resolve, normalize, dirname } from 'node:path';
import type { Database as DatabaseType } from 'better-sqlite3';
import { z } from 'zod';
import { openDb, getFileSummary } from './db.js';
import { indexAll, isIndexStale } from './indexer.js';
import { search } from './search.js';
import { parseConversationExchanges } from './conversation-parser.js';

/** Prefix for conversation file paths in the DB */
const CONV_PREFIX = 'conversations/';

// --- Constants ---

const DB_PATH = join(
  process.env['HOME'] ?? process.env['USERPROFILE'] ?? '.',
  '.claude-memory',
  'index',
  'memory.db',
);

const MEMORY_DIR = join(
  process.env['HOME'] ?? process.env['USERPROFILE'] ?? '.',
  '.claude-memory',
);

const ARCHIVE_DIR = join(
  process.env['HOME'] ?? process.env['USERPROFILE'] ?? '.',
  '.claude',
  'projects',
);

// --- Zod Input Schemas (flat objects only, no anyOf/oneOf/allOf) ---

export const memorySearchInputSchema = {
  query: z.string().describe('Search query text'),
  maxResults: z.number().default(10).describe('Maximum results to return'),
  minScore: z.number().default(0).describe('Minimum relevance score (0-1)'),
  after: z.string().default('').describe('Filter: only results after this date (YYYY-MM-DD)'),
  before: z.string().default('').describe('Filter: only results before this date (YYYY-MM-DD)'),
  project: z.string().default('').describe('Filter: only results from this project directory'),
  source: z.string().default('').describe('Filter: "curated" for memory files only, "conversations" for session history only, empty for both'),
};

export const memoryReadInputSchema = {
  path: z.string().describe('Relative path within ~/.claude-memory/'),
  from: z.number().default(1).describe('Starting line number (1-based)'),
  lines: z.number().default(0).describe('Number of lines to return (0 = all)'),
};

export const memoryWriteInputSchema = {
  content: z.string().describe('Content to write'),
  file: z.string().default('').describe('Target file (MEMORY.md or memory/*.md, default: memory/YYYY-MM-DD.md)'),
  append: z.boolean().default(true).describe('Append to file (true) or overwrite (false)'),
};

// --- Lazy DB ---

let db: DatabaseType | null = null;

function getDb(): DatabaseType {
  if (!db) {
    db = openDb(DB_PATH);
  }
  return db;
}

/** Close the database connection for graceful shutdown */
export function closeDb(): void {
  if (db) {
    try {
      db.pragma('wal_checkpoint(TRUNCATE)');
      db.close();
    } catch {
      // DB may already be closed
    }
    db = null;
  }
}

// --- Reindex File Lock ---
// Multiple MCP server processes (one per Claude Code session/agent) share
// the same SQLite database. sqlite-vec's vec0 virtual table doesn't handle
// concurrent writes well. Use a lockfile so only one process reindexes at
// a time; others skip if the lock is held.

const LOCK_PATH = join(
  process.env['HOME'] ?? process.env['USERPROFILE'] ?? '.',
  '.claude-memory',
  'index',
  'reindex.lock',
);

function acquireReindexLock(): boolean {
  try {
    const fd = openSync(LOCK_PATH, fsConstants.O_CREAT | fsConstants.O_EXCL | fsConstants.O_WRONLY);
    writeFileSync(fd, `${process.pid}\n${Date.now()}\n`);
    closeSync(fd);
    return true;
  } catch {
    // Lock file exists — check if it's stale (older than 5 minutes)
    try {
      const content = readFileSync(LOCK_PATH, 'utf-8');
      const timestamp = parseInt(content.split('\n')[1] ?? '0', 10);
      if (Date.now() - timestamp > 5 * 60 * 1000) {
        // Stale lock, remove and retry
        unlinkSync(LOCK_PATH);
        return acquireReindexLock();
      }
    } catch {
      // Can't read lock file, assume it's held
    }
    return false;
  }
}

function releaseReindexLock(): void {
  try {
    unlinkSync(LOCK_PATH);
  } catch {
    // Lock file may already be removed
  }
}

// --- Path Validation ---

function validatePath(inputPath: string): string {
  const normalized = normalize(inputPath);
  if (normalized.includes('..')) {
    throw new Error('Path traversal not allowed: ' + inputPath);
  }
  const fullPath = resolve(MEMORY_DIR, normalized);
  if (!fullPath.startsWith(resolve(MEMORY_DIR))) {
    throw new Error('Path must be within ~/.claude-memory/: ' + inputPath);
  }
  return fullPath;
}

function validateWritePath(file: string): string {
  const normalized = normalize(file);
  if (normalized.includes('..')) {
    throw new Error('Path traversal not allowed: ' + file);
  }
  // Restrict to MEMORY.md or memory/*.md
  if (normalized === 'MEMORY.md' || normalized.startsWith('memory/') || normalized.startsWith('memory\\')) {
    if (!normalized.endsWith('.md')) {
      throw new Error('File must end with .md: ' + file);
    }
    return resolve(MEMORY_DIR, normalized);
  }
  throw new Error('File must be MEMORY.md or memory/*.md: ' + file);
}

// --- Tool Handlers ---

interface SearchResultEntry {
  path: string;
  startLine?: number;
  endLine?: number;
  score: number;
  snippet: string;
  // Conversation-only enrichment
  project?: string;
  date?: string;
  summary?: string;
}

/**
 * Parse rich metadata from a chunk title.
 * Exchange-based titles use format: "projectDir | date | Tools: X, Y"
 */
function parseChunkTitle(title: string): { project?: string; date?: string; tools?: string[] } {
  const segments = title.split(' | ').map((s) => s.trim());
  let project: string | undefined;
  let date: string | undefined;
  let tools: string[] | undefined;

  for (const seg of segments) {
    if (seg.startsWith('Tools: ')) {
      tools = seg.slice(7).split(', ').filter(Boolean);
    } else if (/^\d{4}-\d{2}-\d{2}$/.test(seg)) {
      date = seg;
    } else if (seg && !project) {
      project = seg;
    }
  }
  return { project, date, tools };
}

/** Extract a YYYY-MM-DD date from a curated memory file path (e.g. "memory/2026-02-05.md") */
function dateFromMemoryPath(filePath: string): string | undefined {
  const m = filePath.match(/(\d{4}-\d{2}-\d{2})/);
  return m ? m[1] : undefined;
}

/** Build a regex that strips the home-dir prefix from archive-style project names.
 *  E.g. for user "jdoe": "-Users-jdoe-my-project" → "my-project" */
const HOME_USER = (() => {
  const home = process.env['HOME'] ?? process.env['USERPROFILE'] ?? '';
  // /Users/jdoe → jdoe (dots become dashes in archive paths)
  const parts = home.split(/[/\\]/).filter(Boolean);
  const username = parts.length >= 2 ? parts[parts.length - 1].replace(/\./g, '-') : '';
  return username;
})();

/** Normalize a project string for comparison: strip leading path separators and home-dir prefixes */
function normalizeProject(raw: string): string {
  let s = raw;
  // Strip the full home-dir prefix: -Users-nathan-norman- (with dots converted to dashes)
  if (HOME_USER) {
    const prefix = new RegExp(`^-*Users-${HOME_USER.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}-`, 'i');
    s = s.replace(prefix, '');
  } else {
    // Fallback: strip generic -Users-<segment>- pattern
    s = s.replace(/^-*Users-[^/\\-]+-/i, '');
  }
  return s.replace(/^[-/\\]+/, '').toLowerCase();
}

/** Truncate text at the nearest sentence/paragraph boundary instead of mid-word */
function smartTruncate(text: string, maxLen: number): string {
  if (text.length <= maxLen) return text;
  const slice = text.substring(0, maxLen);
  // Try paragraph break first, then sentence break, then word break
  const lastPara = slice.lastIndexOf('\n\n');
  if (lastPara > maxLen * 0.6) return slice.substring(0, lastPara);
  const lastSentence = Math.max(slice.lastIndexOf('. '), slice.lastIndexOf('.\n'));
  if (lastSentence > maxLen * 0.6) return slice.substring(0, lastSentence + 1);
  const lastSpace = slice.lastIndexOf(' ');
  if (lastSpace > maxLen * 0.6) return slice.substring(0, lastSpace);
  return slice; // fallback to hard cut
}

export async function handleMemorySearch(args: {
  query: string;
  maxResults: number;
  minScore: number;
  after: string;
  before: string;
  project: string;
  source: string;
}): Promise<{ results: SearchResultEntry[] }> {
  const database = getDb();

  // Only reindex if files have changed (mtime-based staleness check).
  // Acquire a file lock so only one process reindexes at a time —
  // sqlite-vec's vec0 table corrupts under concurrent writes.
  if (isIndexStale(database, MEMORY_DIR, ARCHIVE_DIR)) {
    if (acquireReindexLock()) {
      try {
        await indexAll(database, MEMORY_DIR, ARCHIVE_DIR);
      } finally {
        releaseReindexLock();
      }
    }
    // If lock not acquired, skip reindex — another process is handling it
  }

  // Over-fetch to compensate for post-filtering (date, project, source, session dedup)
  const hasFilters = !!(args.after || args.before || args.project || args.source);
  const fetchLimit = hasFilters ? args.maxResults * 5 : args.maxResults * 3;

  // Run hybrid search
  const results = await search(database, {
    query: args.query,
    limit: fetchLimit,
    threshold: args.minScore,
    mode: 'hybrid',
  });

  // Cache summaries to avoid repeated DB lookups for chunks from the same file
  const summaryCache = new Map<string, string | null>();

  const normalizedProjectFilter = args.project ? normalizeProject(args.project) : '';

  const filtered: SearchResultEntry[] = [];
  // Session dedup: cap at 2 results per conversation file
  const sessionCounts = new Map<string, number>();

  for (const r of results) {
    if (filtered.length >= args.maxResults) break;

    const isConversation = r.chunk.filePath.startsWith(CONV_PREFIX);

    // Post-filter: source type
    if (args.source === 'curated' && isConversation) continue;
    if (args.source === 'conversations' && !isConversation) continue;

    let entryProject: string | undefined;
    let entryDate: string | undefined;

    if (isConversation) {
      const titleMeta = parseChunkTitle(r.chunk.title);
      entryProject = titleMeta.project;
      entryDate = titleMeta.date;
    } else {
      // Curated memory: extract date from file path
      entryDate = dateFromMemoryPath(r.chunk.filePath);
    }

    // Post-filter: date range
    if (args.after && (!entryDate || entryDate < args.after)) continue;
    if (args.before && (!entryDate || entryDate > args.before)) continue;

    // Post-filter: project (only applies to conversation results; curated memory passes through)
    if (normalizedProjectFilter && isConversation) {
      if (!entryProject || !normalizeProject(entryProject).includes(normalizedProjectFilter)) continue;
    }

    // Session dedup: max 2 results per conversation file
    if (isConversation) {
      const count = sessionCounts.get(r.chunk.filePath) ?? 0;
      if (count >= 2) continue;
      sessionCounts.set(r.chunk.filePath, count + 1);
    }

    // Build result entry
    const entry: SearchResultEntry = {
      path: isConversation
        ? r.chunk.filePath.replace(/^conversations\//, '').replace(/\.jsonl$/, '').split('/').pop() ?? r.chunk.filePath
        : r.chunk.filePath,
      score: Math.round(r.score * 1000) / 1000,
      snippet: smartTruncate(r.chunk.content, 800),
    };

    // Keep line numbers only for curated memory (useful for memory_read follow-ups)
    if (!isConversation) {
      entry.startLine = r.chunk.startLine;
      entry.endLine = r.chunk.endLine;
    }

    // Enrich conversation results with metadata
    if (isConversation) {
      if (entryProject) entry.project = normalizeProject(entryProject);
      if (entryDate) entry.date = entryDate;

      // Look up summary (cached per file), cap at 200 chars
      if (!summaryCache.has(r.chunk.filePath)) {
        summaryCache.set(r.chunk.filePath, getFileSummary(database, r.chunk.filePath));
      }
      const summary = summaryCache.get(r.chunk.filePath);
      if (summary) {
        entry.summary = summary.length > 200 ? summary.substring(0, 200) + '...' : summary;
      }
    }

    filtered.push(entry);
  }

  return { results: filtered };
}

/** UUID pattern for session IDs from search results */
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Read a conversation by session UUID, resolving the path via DB lookup */
function readConversationByUuid(
  database: DatabaseType,
  uuid: string,
  from: number,
  lines: number,
): { text: string; path: string; totalLines: number } {
  // Look up the file path in the DB
  const row = database.prepare(
    `SELECT file_path FROM files WHERE file_path LIKE ?`,
  ).get(`%${uuid}%`) as { file_path: string } | undefined;

  if (!row) {
    throw new Error('No conversation found for session UUID: ' + uuid);
  }

  // Resolve absolute path: conversations/projectDir/uuid.jsonl → archive path
  const relativePath = row.file_path.replace(/^conversations\//, '');
  const absolutePath = join(ARCHIVE_DIR, relativePath);

  if (!existsSync(absolutePath)) {
    throw new Error('Conversation file not found on disk: ' + absolutePath);
  }

  // Parse into structured exchanges
  const parsed = parseConversationExchanges(absolutePath);
  if (!parsed || parsed.exchanges.length === 0) {
    throw new Error('Could not parse conversation: ' + uuid);
  }

  // Format exchanges as readable text
  const formatted: string[] = [];
  if (parsed.metadata.sessionId) formatted.push(`Session: ${parsed.metadata.sessionId}`);
  if (parsed.metadata.cwd) formatted.push(`Project: ${parsed.metadata.cwd}`);
  if (parsed.metadata.timestamp) formatted.push(`Date: ${parsed.metadata.timestamp.slice(0, 10)}`);
  formatted.push('---');

  for (const ex of parsed.exchanges) {
    formatted.push(`User: ${ex.userMessage}`);
    if (ex.assistantMessage) {
      formatted.push(`Assistant: ${ex.assistantMessage}`);
    }
    formatted.push('---');
  }

  const allLines = formatted.join('\n').split('\n');
  const totalLines = allLines.length;

  // Apply pagination
  const startIdx = Math.max(0, from - 1);
  const sliced = lines > 0
    ? allLines.slice(startIdx, startIdx + lines)
    : allLines.slice(startIdx);

  return {
    text: sliced.join('\n'),
    path: uuid,
    totalLines,
  };
}

export async function handleMemoryRead(args: {
  path: string;
  from: number;
  lines: number;
}): Promise<{ text: string; path: string; totalLines: number }> {
  // If path is a session UUID, resolve via DB lookup
  if (UUID_RE.test(args.path)) {
    const database = getDb();
    return readConversationByUuid(database, args.path, args.from, args.lines);
  }

  const fullPath = validatePath(args.path);

  if (!existsSync(fullPath)) {
    throw new Error('File not found: ' + args.path);
  }

  const content = readFileSync(fullPath, 'utf-8');
  const allLines = content.split('\n');
  const totalLines = allLines.length;

  // Apply line range slicing (1-based from)
  const startIdx = Math.max(0, args.from - 1);
  const sliced = args.lines > 0
    ? allLines.slice(startIdx, startIdx + args.lines)
    : allLines.slice(startIdx);

  return {
    text: sliced.join('\n'),
    path: args.path,
    totalLines,
  };
}

export async function handleMemoryWrite(args: {
  content: string;
  file: string;
  append: boolean;
}): Promise<{ path: string; linesWritten: number }> {
  // Default file to memory/YYYY-MM-DD.md
  const targetFile = args.file || `memory/${new Date().toISOString().slice(0, 10)}.md`;
  const fullPath = validateWritePath(targetFile);

  // Ensure parent directory exists
  mkdirSync(dirname(fullPath), { recursive: true });

  // Write or append
  if (args.append && existsSync(fullPath)) {
    const existing = readFileSync(fullPath, 'utf-8');
    const separator = existing.endsWith('\n') ? '' : '\n';
    writeFileSync(fullPath, existing + separator + args.content, 'utf-8');
  } else {
    writeFileSync(fullPath, args.content, 'utf-8');
  }

  // Trigger reindex (always after write)
  const database = getDb();
  if (acquireReindexLock()) {
    try {
      await indexAll(database, MEMORY_DIR, ARCHIVE_DIR);
    } finally {
      releaseReindexLock();
    }
  }
  // If lock not acquired, another process will pick up the changes

  const linesWritten = args.content.split('\n').length;

  return {
    path: targetFile,
    linesWritten,
  };
}
