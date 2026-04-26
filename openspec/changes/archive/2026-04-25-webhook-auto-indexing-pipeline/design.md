## Context

claude-memory indexes codebases into a SQLite search index (FTS5 + embeddings) for semantic code search. Today, re-indexing requires manually running `codebase-index.py --update`. The existing indexer uses content-hash-based incremental updates via the `codebase_meta` table and reads files from a working copy on disk.

The system runs as a single-user local tool on macOS. The MCP server is a Python process using FastMCP over stdio. The Node.js indexer runs on-demand. Both share the same SQLite database in WAL mode.

## Goals / Non-Goals

**Goals:**
- Automatically re-index tracked codebases when code is pushed to GitHub
- Minimize disk usage by using bare git mirrors instead of working copies
- Provide reliable catch-up via polling fallback (GitHub does not retry failed webhooks)
- Reuse the existing chunking, embedding, and indexing pipeline
- Support GitHub Enterprise (github.toasttab.com)

**Non-Goals:**
- Multi-user / multi-tenant support
- Indexing non-default branches (only main/master)
- Real-time sub-second latency (minutes-scale is fine)
- Replacing the manual `codebase-index.py` script (it remains for ad-hoc use)
- Distributed job queue (SQLite is sufficient for single-machine workload)

## Decisions

### Single-process architecture (webhook + worker in one process)
Run the FastAPI webhook receiver and the background worker in the same Python process. The webhook handler runs in uvicorn's async event loop; the worker runs in a dedicated thread polling the SQLite queue.

**Rationale:** Simplest deployment model for a single-user tool. No process supervisor or IPC needed. A separate process would add operational complexity with no throughput benefit at this scale.

**Alternative considered:** Separate webhook and worker processes communicating via the SQLite queue. Rejected because it doubles the deployment surface for no gain.

### Bare git mirrors instead of working copies
Store repos as bare clones (`git clone --bare`) and read file contents via `git show <sha>:<path>` instead of checking out files.

**Rationale:** 40-60% less disk. No working tree means no risk of dirty state. `git show` reads directly from packfiles. The existing `code_chunker.chunk_file()` reads file content as a string, so we pass content from `git show` instead of a file path (requires a small adapter).

**Alternative considered:** Shallow clones with working copies. Rejected because they still require disk for the working tree and `git diff` between arbitrary SHAs can fail with shallow history.

### SQLite job queue (not Redis/PostgreSQL)
Use a table in the existing `memory.db` (or a separate `queue.db`) for the job queue.

**Rationale:** The system already depends on SQLite. Adding Redis or Postgres for a queue that processes single-digit jobs per hour is unnecessary complexity. SQLite's WAL mode handles the concurrent reader (webhook) + writer (worker) pattern well.

### Diff-based incremental indexing
Use `git diff --name-status <before>..<after>` to identify changed files, then only re-index those files.

**Rationale:** A full re-index of a large repo takes minutes. Diff-based updates process only what changed, typically completing in seconds. The existing `codebase_meta` table already tracks per-file content hashes, so we update those as we go.

### Job deduplication by repo
When a new push event arrives for a repo that already has a pending job, update the existing job's `after_sha` rather than creating a new job.

**Rationale:** Rapid pushes (e.g., merge queue) would create redundant jobs. Coalescing means the worker always indexes to the latest state. Only pending jobs are coalesced; processing/done/failed jobs are left alone.

### Webhook secret via macOS Keychain
Store the GitHub webhook secret in macOS Keychain (`webhook-github-secret`) rather than in environment variables or config files.

**Rationale:** Consistent with the project's existing credential storage pattern. Falls back to `WEBHOOK_SECRET` env var for flexibility.

## Risks / Trade-offs

- **[Force pushes / history rewrites]** When `before` SHA is not an ancestor of `after` (or is all zeros for new branches), `git diff` fails. Mitigation: detect this case and fall back to a full re-index of the repo.

- **[Webhook delivery failure]** GitHub does not auto-retry failed webhook deliveries. Mitigation: polling fallback runs every 15 minutes via cron, comparing `git ls-remote HEAD` against the last indexed SHA.

- **[Large diffs overwhelming the worker]** A push touching hundreds of files could take a long time. Mitigation: batch embedding (32 chunks at a time, matching existing pipeline), and job dedup prevents pile-up.

- **[SQLite contention]** The webhook receiver and worker both write to the job queue. Mitigation: `BEGIN IMMEDIATE` for the worker's claim transaction, `busy_timeout = 5000ms`, WAL mode. At single-digit TPS this is not a real concern.

- **[Embedding model loading time]** `sentence-transformers` model load takes several seconds on first use. Mitigation: the worker loads the model once at startup and reuses it across jobs.
