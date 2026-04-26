## 1. Mirror Manager

- [x] 1.1 Create `src/mirror_manager.py` with `ensure_mirror(repo_name, clone_url)` — bare clone if not exists, `git fetch` if exists, return mirror path
- [x] 1.2 Implement `git_diff_files(mirror_path, before_sha, after_sha)` — return list of (status, filepath) tuples via `git diff --name-status`, raise on unrelated SHAs
- [x] 1.3 Implement `git_show_file(mirror_path, sha, filepath)` — return file content as string via `git show`
- [x] 1.4 Implement `cleanup_old_mirrors(max_age_days)` — remove mirrors not fetched within max_age_days
- [x] 1.5 Add `MIRROR_DIR` config (env var, default `~/.claude-memory/mirrors/`)

## 2. Job Queue

- [x] 2.1 Create `src/job_queue.py` with `index_jobs` table schema and auto-creation on init
- [x] 2.2 Implement `enqueue_job(repo_name, clone_url, before_sha, after_sha, ref)` with dedup logic (update existing pending job's after_sha)
- [x] 2.3 Implement `claim_next_job()` with `BEGIN IMMEDIATE` for atomic claim, set status to `processing` and `started_at`
- [x] 2.4 Implement `mark_done(job_id)` and `mark_failed(job_id, error)` with `completed_at` timestamp

## 3. Index Worker

- [x] 3.1 Create `src/index_worker.py` — extract reusable indexing functions from `codebase-index.py` (model loading, embed_and_store_batch, chunk processing)
- [x] 3.2 Implement `process_job(job, model, conn, rotate_fn, codebook)` — ensure mirror, fetch, diff, index changed files
- [x] 3.3 Add adapter to pass `git show` content to `code_chunker.chunk_file()` (currently expects file path — need content-based variant)
- [x] 3.4 Handle deleted files: remove chunks, FTS entries, edges, symbols, and codebase_meta rows
- [x] 3.5 Handle force pushes and new branches (before_sha all zeros or diff failure): fall back to full re-index
- [x] 3.6 Implement worker loop: claim job, process, mark done/failed, sleep if empty, repeat

## 4. Webhook Receiver

- [x] 4.1 Create `src/webhook_server.py` with FastAPI app
- [x] 4.2 Implement `POST /webhook` — parse GitHub push payload, extract repo_name, clone_url, before/after SHA, ref
- [x] 4.3 Implement HMAC-SHA256 signature verification (read secret from Keychain `webhook-github-secret`, fallback to `WEBHOOK_SECRET` env var)
- [x] 4.4 Add branch filter: only enqueue for pushes to `refs/heads/main` or `refs/heads/master`, skip others with 200
- [x] 4.5 Implement `GET /health` endpoint returning `{"status": "ok"}`

## 5. Polling Fallback

- [x] 5.1 Create `src/poll_repos.py` — read tracked repos from `~/.claude-memory/webhook-config.json` or `TRACKED_REPOS` env var
- [x] 5.2 Implement SHA drift detection: `git ls-remote <url> HEAD` vs last indexed SHA from `codebase_meta`
- [x] 5.3 Enqueue jobs for repos with SHA drift (all-zeros before_sha for repos not yet indexed)
- [x] 5.4 Make script cron-compatible: one-shot execution, exit 0 on success

## 6. Entry Point and Integration

- [x] 6.1 Create `scripts/start-webhook-server.sh` — start uvicorn + worker thread in single process
- [x] 6.2 Add `fastapi` and `uvicorn` to project dependencies
- [x] 6.3 Wire worker thread startup into the FastAPI app lifespan (load model once, start worker loop in background thread)
- [x] 6.4 Add configuration documentation: `WEBHOOK_SECRET`, `MIRROR_DIR`, `TRACKED_REPOS`, `GITHUB_TOKEN`

## 7. Testing

- [ ] 7.1 Test mirror_manager: ensure_mirror, git_diff_files, git_show_file with a local test repo
- [ ] 7.2 Test job_queue: enqueue, dedup, claim, mark_done, mark_failed
- [ ] 7.3 Test webhook_server: signature verification (valid/invalid/missing), branch filtering, payload parsing
- [ ] 7.4 Test index_worker: normal diff indexing, force push fallback, deleted file cleanup
- [ ] 7.5 Test poll_repos: SHA drift detection, config loading
