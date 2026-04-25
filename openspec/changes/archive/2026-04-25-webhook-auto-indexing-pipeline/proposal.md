## Why

Codebase indexing currently requires manual invocation of `codebase-index.py --update`. This means the search index drifts out of date whenever code is pushed, and someone must remember to re-index. A webhook-driven pipeline that automatically re-indexes on push events keeps the search index fresh with zero manual intervention.

## What Changes

- New webhook receiver (FastAPI) accepts GitHub push events, verifies HMAC-SHA256 signatures, and enqueues index jobs
- New SQLite job queue with deduplication (pending jobs for the same repo coalesce)
- New background worker claims jobs, fetches changes via bare git mirrors, and incrementally re-indexes only changed files using the existing chunking and embedding pipeline
- New polling fallback checks tracked repos via `git ls-remote` on a cron schedule, enqueuing jobs when SHAs diverge (GitHub does not auto-retry failed webhooks)
- New bare clone mirror manager to avoid full working copies (~40-60% less disk)
- Configuration via environment variables and optional JSON config file

## Capabilities

### New Capabilities
- `webhook-receiver`: HTTP endpoint for GitHub push events with signature verification and job enqueueing
- `job-queue`: SQLite-backed job queue with enqueue, claim, complete/fail lifecycle and dedup
- `index-worker`: Background worker that processes index jobs using bare git mirrors and incremental diff-based re-indexing
- `poll-fallback`: Cron-compatible polling script that detects SHA drift and enqueues catch-up jobs
- `mirror-manager`: Bare clone lifecycle management (create, fetch, diff, read files, cleanup)

### Modified Capabilities

## Impact

- New Python files: `src/webhook_server.py`, `src/job_queue.py`, `src/index_worker.py`, `src/poll_repos.py`, `src/mirror_manager.py`
- New entry point script: `scripts/start-webhook-server.sh`
- New dependencies: `fastapi`, `uvicorn` (added to project requirements)
- SQLite schema addition: `index_jobs` table (can live in the existing `memory.db` or a separate queue DB)
- Reuses existing `code_chunker.chunk_file()`, embedding pipeline, and `codebase_meta` table from `codebase-index.py`
- Must work with GitHub Enterprise (`github.toasttab.com`)
