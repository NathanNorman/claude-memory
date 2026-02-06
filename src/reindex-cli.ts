#!/usr/bin/env node
/**
 * Standalone CLI to trigger a full reindex of memory + conversation archives.
 * Usage: node dist/reindex-cli.js
 */
import { join } from 'node:path';
import { homedir } from 'node:os';
import { openDb } from './db.js';
import { indexAll } from './indexer.js';

const MEMORY_DIR = join(homedir(), '.claude-memory');
const ARCHIVE_DIR = join(homedir(), '.claude', 'projects');

async function main() {
  const db = openDb(join(MEMORY_DIR, 'index', 'memory.db'));
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
