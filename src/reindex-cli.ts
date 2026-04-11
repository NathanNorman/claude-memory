#!/usr/bin/env node
/**
 * Standalone CLI to trigger a full reindex of memory + conversation archives.
 * Usage: node dist/reindex-cli.js [--llm-scoring]
 */
import { join } from 'node:path';
import { homedir } from 'node:os';
import { copyFileSync, mkdirSync, existsSync } from 'node:fs';
import { openDb } from './db.js';
import { indexAll } from './indexer.js';
import { validateLlmConfig } from './llm-client.js';
import { LlmBoundaryScorer } from './llm-boundary-scorer.js';

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
  // Parse CLI flags
  const args = process.argv.slice(2);
  const cliLlmScoring = args.includes('--llm-scoring');
  const envLlmScoring = process.env.MEMORY_LLM_SCORING === '1';
  const llmScoring = cliLlmScoring || envLlmScoring;

  // Validate LLM config if scoring enabled
  let llmScorer: LlmBoundaryScorer | undefined;
  if (llmScoring) {
    try {
      const llmConfig = validateLlmConfig();
      process.stderr.write(`[claude-memory] LLM scoring enabled: ${llmConfig.model} at ${llmConfig.baseUrl}\n`);
      // DB will be passed after opening
      llmScorer = undefined; // placeholder, created after db open
    } catch (err) {
      process.stderr.write(`[claude-memory] ERROR: ${err instanceof Error ? err.message : err}\n`);
      process.exit(1);
    }
  }

  const dbPath = join(MEMORY_DIR, 'index', 'memory.db');
  const backup = backupDb(dbPath);
  if (backup) {
    console.log('Backed up DB to:', backup);
  }
  const db = openDb(dbPath);

  // Create scorer with DB reference for caching
  if (llmScoring) {
    const llmConfig = validateLlmConfig();
    llmScorer = new LlmBoundaryScorer({ llmConfig }, db);
  }

  console.log('Starting full reindex...');
  console.log('  Memory dir:', MEMORY_DIR);
  console.log('  Archive dir:', ARCHIVE_DIR);
  if (llmScoring) {
    console.log('  LLM scoring: ENABLED');
  }

  const result = await indexAll(db, MEMORY_DIR, ARCHIVE_DIR, {
    llmScoring,
    llmScorer,
  });

  // Log LLM scoring stats
  if (llmScorer) {
    const total = llmScorer.cacheHits + llmScorer.cacheMisses;
    const hitRate = total > 0 ? ((llmScorer.cacheHits / total) * 100).toFixed(1) : '0';
    process.stderr.write(
      `[claude-memory] LLM scoring stats: ${llmScorer.cacheHits} cache hits, ` +
      `${llmScorer.cacheMisses} misses (${hitRate}% hit rate)\n`,
    );
  }

  console.log(`Done! Indexed ${result.files} files, ${result.chunks} chunks.`);
  db.close();
}

main().catch((err) => {
  console.error('Reindex failed:', err);
  process.exit(1);
});
