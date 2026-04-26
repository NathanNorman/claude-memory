## ADDED Requirements

### Requirement: PipelineTimer tests
The test suite SHALL verify PipelineTimer correctly records per-stage timing.

#### Scenario: Single stage recorded
- **GIVEN** a PipelineTimer instance
- **WHEN** start("git_fetch") then sleep(10ms) then stop() is called
- **THEN** stages["git_fetch"] is approximately 10ms (±5ms)

#### Scenario: Multiple stages recorded in sequence
- **GIVEN** a PipelineTimer instance
- **WHEN** start/stop is called for "git_fetch", "diff_compute", "embeddings"
- **THEN** all three stages appear in stages dict with non-zero values

#### Scenario: to_json produces valid JSON
- **GIVEN** a PipelineTimer with 3 recorded stages
- **WHEN** to_json() is called
- **THEN** result is valid JSON parseable back to a dict with 3 keys

#### Scenario: summary identifies slowest stage
- **GIVEN** stages: git_fetch=10ms, embeddings=500ms, db_writes=20ms
- **WHEN** summary() is called
- **THEN** output contains "slowest: embeddings (500ms)" and total ~530ms

### Requirement: Job queue timing storage tests
The test suite SHALL verify timing JSON is stored and retrievable from index_jobs.

#### Scenario: mark_done stores timing
- **GIVEN** a pending job in the queue
- **WHEN** claimed, then mark_done(job_id, timing='{"git_fetch": 100}')
- **THEN** the job row has timing column = '{"git_fetch": 100}' and status = 'done'

#### Scenario: mark_done without timing stores NULL
- **GIVEN** a pending job
- **WHEN** mark_done(job_id) with no timing arg
- **THEN** timing column is NULL

### Requirement: Pipeline health metrics tests
The test suite SHALL verify get_pipeline_health() computes correct aggregates.

#### Scenario: No jobs returns zeroes
- **GIVEN** empty index_jobs table
- **WHEN** get_pipeline_health() is called
- **THEN** returns jobs_last_hour=0, avg_latency_ms=0, p95_latency_ms=0, queue_depth=0

#### Scenario: Completed jobs compute latency stats
- **GIVEN** 5 done jobs in last hour with latencies [100, 200, 300, 400, 500]ms
- **WHEN** get_pipeline_health() is called
- **THEN** avg_latency_ms=300, p95_latency_ms=500, jobs_last_hour=5

#### Scenario: Queue depth counts pending jobs
- **GIVEN** 3 pending jobs and 2 done jobs
- **WHEN** get_pipeline_health() is called
- **THEN** queue_depth=3

#### Scenario: Old jobs excluded from stats
- **GIVEN** 2 done jobs from 2 hours ago and 1 done job from 30 minutes ago
- **WHEN** get_pipeline_health() is called
- **THEN** jobs_last_hour=1 (old jobs excluded)
