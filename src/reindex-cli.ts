#!/usr/bin/env node
/**
 * Standalone CLI to trigger a full reindex of memory + conversation archives.
 * Usage: node dist/reindex-cli.js
 */
import { join } from 'node:path';
import { homedir } from 'node:os';
import { copyFileSync, mkdirSync, existsSync } from 'node:fs';
import { openDb } from './db.js';
import { indexAll } from './indexer.js';

const MEMORY_DIR = join(homedir(), '.claude-memory');
const ARCHIVE_DIR = join(homedir(), '.claude', 'projects');

function backupDb(dbPath: string): string | null {
  const backupDir = join(MEMORY_DIR, 'backups');
  mkdirSync(backupDir, { recursive: true });
  const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const backupPath = join(backupDir, `memory-pre-reindex-${ts}.db`);
  if (existsSync(dbPath)) {
    copyFileSync(dbPath, backupPath);
    return backupPath;
  }
  return null;
}

async function main() {
  const dbPath = join(MEMORY_DIR, 'index', 'memory.db');
  const backup = backupDb(dbPath);
  if (backup) {
    console.log('Backed up DB to:', backup);
  }
  const db = openDb(dbPath);
  console.log('Starting full reindex...');
  console.log('  Memory dir:', MEMORY_DIR);
  console.log('  Archive dir:', ARCHIVE_DIR);
  const result = await indexAll(db, MEMORY_DIR, ARCHIVE_DIR);
  console.log(`Done! Indexed ${result.files} files, ${result.chunks} chunks.`);
  db.close();
}

main().catch((err) => {
  console.error('Reindex failed:', err);
  process.exit(1);
});
