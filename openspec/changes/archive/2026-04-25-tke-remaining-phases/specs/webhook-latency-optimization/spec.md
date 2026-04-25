## ADDED Requirements

### Requirement: Pipeline timing instrumentation
The webhook indexing pipeline SHALL record per-stage timestamps for: webhook received, job enqueued, worker picked up, git fetch complete, diff computed, chunks generated, embeddings computed, DB writes complete.

#### Scenario: Timing data stored
- **WHEN** a webhook indexing job completes
- **THEN** the job queue row contains `timing` JSON with millisecond-precision timestamps for each stage

#### Scenario: Timing logged
- **WHEN** a job completes
- **THEN** a summary log line reports total latency and the slowest stage (e.g., "[webhook-pipeline] Job 42 complete: 1.2s total, slowest: embeddings (0.8s)")

### Requirement: Warm model for embedding stage
The embedding model SHALL be pre-loaded at worker startup rather than loaded on first use. This eliminates the cold-start penalty (~3-5s) on the first job after server restart.

#### Scenario: Worker startup
- **WHEN** the index worker process starts
- **THEN** the embedding model is loaded into memory before processing any jobs, and a log message confirms readiness

### Requirement: Incremental embedding for small diffs
For diffs affecting fewer than 10 files, the system SHALL embed only the changed chunks rather than re-embedding all chunks in the affected files' modules.

#### Scenario: Small diff optimization
- **WHEN** a webhook job processes a push with 3 changed files
- **THEN** only chunks from those 3 files are re-embedded (not the entire codebase), and the job completes in <1s

#### Scenario: Large diff fallback
- **WHEN** a webhook job processes a push with >10 changed files
- **THEN** the system uses the standard batch embedding path without per-file optimization

### Requirement: Sub-second target for small diffs
The system SHALL achieve <1s end-to-end latency (webhook receipt to searchable) for pushes affecting fewer than 10 files, measured at the 95th percentile.

#### Scenario: Latency target met
- **WHEN** 100 consecutive small-diff webhook jobs complete
- **THEN** the 95th percentile total latency is <1s

#### Scenario: Latency target missed
- **WHEN** the 95th percentile latency exceeds 1s
- **THEN** the timing breakdown identifies which stage to optimize, logged at WARN level

### Requirement: Pipeline health monitoring
The system SHALL expose pipeline health via the `get_status` MCP tool, including: jobs processed in last hour, average latency, p95 latency, and current queue depth.

#### Scenario: Status includes pipeline health
- **WHEN** `get_status` is called
- **THEN** the response includes a `pipeline` section with `jobs_last_hour`, `avg_latency_ms`, `p95_latency_ms`, and `queue_depth`
