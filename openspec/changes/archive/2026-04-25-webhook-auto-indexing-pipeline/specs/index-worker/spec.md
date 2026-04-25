## ADDED Requirements

### Requirement: Process index jobs continuously
The index worker SHALL run in a loop, claiming jobs from the queue and processing them. When no jobs are available, the worker SHALL sleep for a configurable interval (default 5 seconds) before checking again.

#### Scenario: Job available
- **WHEN** the worker polls the queue and a pending job exists
- **THEN** the worker claims the job, processes it, and marks it done or failed

#### Scenario: No jobs available
- **WHEN** the worker polls the queue and no pending jobs exist
- **THEN** the worker sleeps for the configured interval and polls again

### Requirement: Incremental diff-based indexing
The worker SHALL use `git diff --name-status <before_sha>..<after_sha>` to identify changed files. For deleted files, the worker SHALL remove corresponding chunks, edges, and symbols from the database. For added or modified files, the worker SHALL read content via `git show`, chunk, embed, and index.

#### Scenario: Normal push with file changes
- **WHEN** a job has valid before and after SHAs that are related (before is ancestor of after)
- **THEN** the worker runs `git diff --name-status` between the SHAs, removes chunks for deleted files, and re-indexes added/modified files

#### Scenario: Files deleted in push
- **WHEN** `git diff` reports files with status `D` (deleted)
- **THEN** the worker removes all chunks, FTS entries, edges, and symbols for those files from the database and removes their entries from `codebase_meta`

#### Scenario: Files added or modified in push
- **WHEN** `git diff` reports files with status `A` (added) or `M` (modified) that have extensions in the indexable set
- **THEN** the worker reads file content via `git show <after_sha>:<path>`, chunks it, generates embeddings, and stores the chunks in the database

### Requirement: Handle force pushes and new branches
The worker SHALL detect when `before_sha` is all zeros (new branch) or when `git diff` fails (force push / non-ancestor SHAs). In these cases, the worker SHALL fall back to a full re-index of the repository.

#### Scenario: New branch (before_sha is all zeros)
- **WHEN** a job has `before_sha` equal to `0000000000000000000000000000000000000000`
- **THEN** the worker performs a full re-index of the repository

#### Scenario: Force push (SHAs not related)
- **WHEN** `git diff` between before and after SHAs fails because they have no common ancestry
- **THEN** the worker falls back to a full re-index of the repository

### Requirement: Idempotent processing
Re-processing the same job (same repo, same before/after SHAs) SHALL produce the same result in the database. The worker SHALL delete existing chunks for a file before re-indexing it.

#### Scenario: Job processed twice
- **WHEN** the same job is processed a second time (e.g., after a retry)
- **THEN** the database state is identical to processing it once (no duplicate chunks)

### Requirement: Load embedding model once
The worker SHALL load the sentence-transformers embedding model once at startup and reuse it across all jobs to avoid repeated model loading overhead.

#### Scenario: Multiple jobs processed
- **WHEN** the worker processes its second job after startup
- **THEN** the embedding model is already loaded in memory and is not re-loaded
