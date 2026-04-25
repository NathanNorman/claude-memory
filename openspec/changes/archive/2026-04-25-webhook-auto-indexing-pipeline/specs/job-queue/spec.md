## ADDED Requirements

### Requirement: Enqueue index jobs
The job queue SHALL provide an `enqueue_job()` function that inserts a new job with repo_name, clone_url, before_sha, after_sha, ref, and status `pending`. The created_at timestamp SHALL be set automatically.

#### Scenario: New job for unqueued repo
- **WHEN** `enqueue_job()` is called for a repo that has no existing `pending` job
- **THEN** a new row is inserted into `index_jobs` with status `pending` and the provided fields

#### Scenario: Deduplication of pending jobs
- **WHEN** `enqueue_job()` is called for a repo that already has a `pending` job
- **THEN** the existing pending job's `after_sha` is updated to the new value instead of creating a duplicate job

### Requirement: Claim next job atomically
The job queue SHALL provide a `claim_next_job()` function that atomically selects the oldest pending job and transitions it to `processing` status. The function SHALL use `BEGIN IMMEDIATE` to prevent race conditions.

#### Scenario: Pending job exists
- **WHEN** `claim_next_job()` is called and at least one job has status `pending`
- **THEN** the oldest pending job is updated to status `processing`, its `started_at` is set to the current timestamp, and the job record is returned

#### Scenario: No pending jobs
- **WHEN** `claim_next_job()` is called and no jobs have status `pending`
- **THEN** the function returns None

### Requirement: Mark job completion
The job queue SHALL provide `mark_done(job_id)` and `mark_failed(job_id, error)` functions that update job status and set the `completed_at` timestamp.

#### Scenario: Mark job done
- **WHEN** `mark_done(job_id)` is called for a processing job
- **THEN** the job status is set to `done` and `completed_at` is set to the current timestamp

#### Scenario: Mark job failed
- **WHEN** `mark_failed(job_id, error)` is called for a processing job
- **THEN** the job status is set to `failed`, the `error` column is set to the error message, and `completed_at` is set to the current timestamp

### Requirement: Job queue schema
The job queue SHALL create the `index_jobs` table with columns: id (autoincrement PK), repo_name, clone_url, before_sha, after_sha, ref, status, error, created_at, started_at, completed_at. An index SHALL exist on (status, created_at) for efficient claim queries.

#### Scenario: Table creation on first use
- **WHEN** the job queue is initialized and the `index_jobs` table does not exist
- **THEN** the table and index are created automatically
