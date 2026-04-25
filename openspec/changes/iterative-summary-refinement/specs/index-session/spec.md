## MODIFIED Requirements

### Requirement: index_session.py runs summarize-judge-refine loop after chunking

The `scripts/index_session.py` script SHALL, after inserting chunks into the database and before exiting, optionally run a summarize-judge-refine loop to generate a quality-checked session summary. This behavior is controlled by the `MEMORY_SUMMARY_ENABLED` environment variable.

The existing chunking, noise filtering, and FTS5 insertion logic SHALL remain unchanged. The summarization step is purely additive -- if it fails or is disabled, session indexing completes normally without a summary.

#### Scenario: Summarization enabled and succeeds
- **WHEN** `MEMORY_SUMMARY_ENABLED=1` and the session has non-trivial content (>= 3 non-noise exchanges)
- **THEN** the script SHALL: (1) generate initial summary, (2) judge it, (3) optionally refine, (4) store final summary in `files.summary`, (5) log quality metrics to stderr

#### Scenario: Summarization disabled
- **WHEN** `MEMORY_SUMMARY_ENABLED` is unset or not `1`
- **THEN** the script SHALL skip all LLM calls and behave identically to current behavior

#### Scenario: Session too short for summarization
- **WHEN** `MEMORY_SUMMARY_ENABLED=1` but the session has fewer than 3 non-noise exchanges
- **THEN** the script SHALL skip summarization (sessions this short are unlikely to contain meaningful decisions)

#### Scenario: LLM calls fail completely
- **WHEN** `MEMORY_SUMMARY_ENABLED=1` but all LLM calls fail after retries
- **THEN** the script SHALL log the failure, leave `files.summary` as NULL, and exit successfully (exit code 0). Session chunks SHALL still be indexed.

### Requirement: Transcript preparation for LLM context

The script SHALL prepare a transcript excerpt for the summarizer by concatenating filtered exchanges in `[User]: ... [Assistant]: ...` format, truncated to 30,000 characters. This matches the existing pattern in `scripts/ingest_session.py:summarize_for_graphiti()`.

#### Scenario: Normal-length session
- **WHEN** a session has 20 exchanges totaling 15,000 characters
- **THEN** the full transcript SHALL be passed to the summarizer without truncation

#### Scenario: Very long session
- **WHEN** a session transcript exceeds 30,000 characters
- **THEN** the transcript SHALL be truncated to 30,000 characters with `...(truncated)` appended

### Requirement: Summary stored in files table

After successful summarization, the script SHALL update the `files` table row for the indexed session, setting the `summary` column to the quality-prefixed summary text. The existing `INSERT OR REPLACE INTO files` statement SHALL be extended to include the summary column.

#### Scenario: Summary column populated
- **WHEN** summarization succeeds
- **THEN** the `files` row for the session SHALL have a non-NULL `summary` value starting with `[quality: ...]`

#### Scenario: Summary column remains NULL on failure
- **WHEN** summarization fails or is disabled
- **THEN** the `files` row SHALL have `summary` as NULL (matching current behavior where the column is not set)
