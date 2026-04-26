#!/usr/bin/env node
/**
 * Database doctor: diagnoses and repairs claude-memory SQLite issues.
 * Usage: node dist/doctor-cli.js [--fix]
 */
import { join } from 'node:path';
import { homedir } from 'node:os';
import { existsSync, statSync, readFileSync, unlinkSync } from 'node:fs';
import { execSync } from 'node:child_process';
import { openDb } from './db.js';
import type { Database as DatabaseType } from 'better-sqlite3';

const MEMORY_DIR = join(homedir(), '.claude-memory');
const DB_PATH = join(MEMORY_DIR, 'index', 'memory.db');
const WAL_PATH = DB_PATH + '-wal';
const LOCK_PATH = join(MEMORY_DIR, 'index', 'reindex.lock');

const fix = process.argv.includes('--fix');

interface Issue {
  label: string;
  fix?: (db: DatabaseType) => void;
}

const issues: Issue[] = [];
let db: DatabaseType;

function ok(msg: string): void {
  console.log(`OK:   ${msg}`);
}

function fail(msg: string, fixFn?: (db: DatabaseType) => void): void {
  console.log(`FAIL: ${msg}`);
  issues.push({ label: msg, fix: fixFn });
}

function warn(msg: string): void {
  console.log(`WARN: ${msg}`);
}

function count(db: DatabaseType, table: string): number {
  const row = db.prepare(`SELECT COUNT(*) as c FROM ${table}`).get() as { c: number };
  return row.c;
}

// --- Repair functions ---

function rebuildFts(db: DatabaseType): void {
  console.log('       Rebuilding chunks_fts...');
  db.exec('DROP TABLE IF EXISTS chunks_fts');
  db.exec(`
    CREATE VIRTUAL TABLE chunks_fts USING fts5(
      content,
      title,
      content=chunks,
      content_rowid=rowid
    )
  `);
  const rows = db.prepare('SELECT rowid, content, title FROM chunks').all() as Array<{
    rowid: number;
    content: string;
    title: string;
  }>;
  const insert = db.prepare('INSERT INTO chunks_fts(rowid, content, title) VALUES (?, ?, ?)');
  const txn = db.transaction((items: typeof rows) => {
    for (const row of items) {
      insert.run(row.rowid, row.content, row.title);
    }
  });
  txn(rows);
  console.log(`       Inserted ${rows.length} FTS entries`);
}

function rebuildVec(db: DatabaseType): void {
  console.log('       Rebuilding chunks_vec...');
  db.exec('DROP TABLE IF EXISTS chunks_vec');
  db.exec('CREATE VIRTUAL TABLE chunks_vec USING vec0(embedding float[768])');
  const rows = db.prepare('SELECT rowid, embedding FROM chunks WHERE embedding IS NOT NULL').all() as Array<{
    rowid: number;
    embedding: Buffer;
  }>;
  const insert = db.prepare('INSERT INTO chunks_vec (rowid, embedding) VALUES (?, ?)');
  const txn = db.transaction((items: typeof rows) => {
    let inserted = 0;
    let skipped = 0;
    for (const row of items) {
      if (!row.embedding || row.embedding.length === 0) {
        skipped++;
        continue;
      }
      try {
        insert.run(BigInt(row.rowid), row.embedding);
        inserted++;
      } catch {
        skipped++;
      }
    }
    return { inserted, skipped };
  });
  const result = txn(rows);
  console.log(`       Inserted ${result.inserted} vectors, skipped ${result.skipped}`);
}

// --- Main ---

function main(): void {
  console.log(`Database: ${DB_PATH}`);
  console.log(`Mode:     ${fix ? '--fix (will repair)' : 'diagnose only'}`);
  console.log('');

  // 1. Open database
  try {
    db = openDb(DB_PATH);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.log(`FAIL: Cannot open database: ${msg}`);
    process.exit(1);
  }

  // 2. Row counts
  const chunksCount = count(db, 'chunks');
  const filesCount = count(db, 'files');
  ok(`chunks table: ${chunksCount} rows`);
  ok(`files table: ${filesCount} rows`);

  // 3. chunks_vec count + MATCH test
  let vecCount: number;
  try {
    vecCount = count(db, 'chunks_vec');
    if (vecCount === chunksCount) {
      ok(`chunks_vec: ${vecCount} rows (matches chunks)`);
    } else {
      fail(
        `chunks_vec: ${vecCount} rows (expected ${chunksCount})`,
        rebuildVec,
      );
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    fail(`chunks_vec count failed: ${msg}`, rebuildVec);
    vecCount = -1;
  }

  // Vec MATCH test
  if (vecCount >= 0) {
    try {
      // Create a zero vector for a simple MATCH test
      const zeroVec = new Float32Array(384);
      const buf = Buffer.from(zeroVec.buffer, zeroVec.byteOffset, zeroVec.byteLength);
      db.prepare('SELECT rowid, distance FROM chunks_vec WHERE embedding MATCH ? LIMIT 1').get(buf);
      ok('chunks_vec MATCH: working');
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      fail(`chunks_vec MATCH failed: ${msg}`, rebuildVec);
    }
  }

  // 4. chunks_fts count + MATCH test + integrity check + docsize check
  let ftsCount: number;
  try {
    ftsCount = count(db, 'chunks_fts');
    if (ftsCount === chunksCount) {
      ok(`chunks_fts: ${ftsCount} rows (matches chunks)`);
    } else {
      fail(
        `chunks_fts: ${ftsCount} rows (expected ${chunksCount})`,
        rebuildFts,
      );
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    fail(`chunks_fts count failed: ${msg}`, rebuildFts);
    ftsCount = -1;
  }

  // FTS MATCH test
  if (ftsCount >= 0) {
    try {
      db.prepare('SELECT rowid, rank FROM chunks_fts WHERE chunks_fts MATCH ? LIMIT 1').get('test');
      ok('chunks_fts MATCH: working');
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      fail(`chunks_fts MATCH failed: ${msg}`, rebuildFts);
    }
  }

  // FTS integrity check
  try {
    db.prepare("INSERT INTO chunks_fts(chunks_fts, rank) VALUES('integrity-check', 1)").run();
    ok('chunks_fts integrity check: passed');
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    fail(`chunks_fts integrity check: ${msg}`, rebuildFts);
  }

  // FTS docsize check (key corruption indicator)
  try {
    const docsizeCount = count(db, 'chunks_fts_docsize');
    if (docsizeCount === chunksCount) {
      ok(`chunks_fts_docsize: ${docsizeCount} rows (matches chunks)`);
    } else {
      fail(
        `chunks_fts_docsize: ${docsizeCount} rows (expected ${chunksCount}) — FTS5 corrupted`,
        rebuildFts,
      );
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    fail(`chunks_fts_docsize check failed: ${msg}`, rebuildFts);
  }

  // 5. WAL file size
  if (existsSync(WAL_PATH)) {
    const walSize = statSync(WAL_PATH).size;
    if (walSize > 10 * 1024 * 1024) {
      warn(`WAL size: ${(walSize / 1024 / 1024).toFixed(1)} MB (large — consider checkpoint)`);
    } else if (walSize === 0) {
      ok('WAL size: 0 bytes (checkpointed)');
    } else {
      ok(`WAL size: ${(walSize / 1024).toFixed(1)} KB`);
    }
  } else {
    ok('WAL file: not present');
  }

  // 6. Stale MCP server processes
  try {
    const output = execSync('pgrep -f "claude-memory/dist/server.js" 2>/dev/null || true', {
      encoding: 'utf-8',
    }).trim();
    const pids = output.split('\n').filter(Boolean);
    if (pids.length === 0) {
      ok('No stale MCP server processes');
    } else {
      warn(`${pids.length} MCP server process(es) running: PIDs ${pids.join(', ')}`);
    }
  } catch {
    ok('No stale MCP server processes');
  }

  // 7. Stale reindex lock file
  if (existsSync(LOCK_PATH)) {
    try {
      const content = readFileSync(LOCK_PATH, 'utf-8');
      const lines = content.split('\n');
      const pid = lines[0] ?? '?';
      const timestamp = parseInt(lines[1] ?? '0', 10);
      const age = Date.now() - timestamp;
      const ageMin = (age / 60000).toFixed(1);

      if (age > 5 * 60 * 1000) {
        fail(`Stale reindex lock (PID ${pid}, ${ageMin} min old)`, () => {
          console.log('       Removing stale lock file...');
          unlinkSync(LOCK_PATH);
        });
      } else {
        ok(`Reindex lock held by PID ${pid} (${ageMin} min old — not stale)`);
      }
    } catch {
      warn('Reindex lock file exists but unreadable');
    }
  } else {
    ok('No reindex lock file');
  }

  // --- Apply fixes ---
  console.log('');

  if (issues.length === 0) {
    console.log('All checks passed.');
    db.close();
    return;
  }

  if (!fix) {
    console.log(`Found ${issues.length} issue(s). Run with --fix to repair.`);
    db.close();
    process.exit(1);
  }

  console.log(`Fixing ${issues.length} issue(s)...`);
  console.log('');

  // Deduplicate fix functions (e.g. multiple FTS issues -> one rebuild)
  const applied = new Set<Function>();
  for (const issue of issues) {
    if (issue.fix && !applied.has(issue.fix)) {
      applied.add(issue.fix);
      console.log(`FIXING: ${issue.label}`);
      issue.fix(db);
      console.log(`FIXED:  ${issue.label}`);
      console.log('');
    }
  }

  // Checkpoint WAL after repairs
  console.log('Checkpointing WAL...');
  db.pragma('wal_checkpoint(TRUNCATE)');
  ok('WAL checkpointed');

  db.close();
  console.log('');
  console.log('Repairs complete. Run again without --fix to verify.');
}

main();
