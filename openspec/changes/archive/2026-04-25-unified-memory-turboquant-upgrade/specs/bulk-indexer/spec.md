# Bulk Indexer

## Purpose

A standalone Python script for one-time or periodic batch embedding of large corpora (codebases, fine-grained conversation re-chunking). Runs as a background process, supports progress tracking, and feeds into the quantization pipeline.

## Requirements

### R1: CLI Interface

- Script: `scripts/bulk_index.py`
- Usage: `python3 scripts/bulk_index.py [--source conversations|codebase|all] [--repo PATH] [--model MODEL] [--progress]`
- `--source`: what to index (default: `all`)
- `--repo`: path to git repo for codebase indexing (can be specified multiple times)
- `--model`: override embedding model (default: from env or `all-MiniLM-L6-v2`)
- `--progress`: show progress bar (tqdm)

### R2: Incremental Operation

- Track what's been indexed via content hash in `embedding_cache` table (existing mechanism)
- On re-run, skip chunks whose content hash matches a cached embedding
- For codebase sources, use git diff to identify changed files since last index

### R3: Batch Embedding

- Embed chunks in batches of 32 (configurable) for throughput
- sentence-transformers `model.encode()` supports batch input natively
- After embedding, quantize via the vector-quantization pipeline and write to `chunks.embedding`

### R4: Progress and Resumability

- Log progress to stderr: `[bulk-index] Embedded 1000/30000 chunks (3.3%)`
- If interrupted (SIGINT), commit all work done so far — do not lose progress
- On resume, incremental mode skips already-embedded chunks automatically

### R5: Background Execution

- Support `--background` flag that daemonizes the process (double-fork or nohup)
- Write PID to `~/.claude-memory/bulk-index.pid` for status checking
- Log to `~/.claude-memory/bulk-index.log`

### R6: Reindex Lock Coordination

- Acquire `~/.claude-memory/index/reindex.lock` before writing to the database
- Respect the same 5-minute stale lock reclamation as the Node.js indexer
- Release lock between batches (not held for the entire run) to allow concurrent MCP server reads
