## ADDED Requirements

### Requirement: Quality metadata stored in files.summary column

After the summarize-judge-refine loop completes, the system SHALL store the final summary text in the `files.summary` column of the `files` table for the indexed session. The summary SHALL be prefixed with a metadata line in the format:

```
[quality: score=X.X iter=N refined=BOOL]
```

followed by the summary text. This metadata line enables downstream parsing of quality metrics without a schema change.

#### Scenario: Summary with refinement
- **WHEN** a session summary scores 9.0 after 1 refinement iteration
- **THEN** the `files.summary` value SHALL begin with `[quality: score=9.0 iter=2 refined=true]` followed by the summary text

#### Scenario: Summary passes on first judge
- **WHEN** a session summary scores 8.5 on the first judge call
- **THEN** the `files.summary` value SHALL begin with `[quality: score=8.5 iter=1 refined=false]` followed by the summary text

#### Scenario: Summarization disabled or failed
- **WHEN** `MEMORY_SUMMARY_ENABLED` is not set to `1`, or all LLM calls fail
- **THEN** the `files.summary` column SHALL remain NULL (existing behavior preserved)

### Requirement: Quality metrics logged to stderr

The system SHALL log quality metrics to stderr (captured in `~/.claude-memory/index-session.log`) including: final score, number of iterations, whether refinement was triggered, and wall-clock time for the summarization loop.

#### Scenario: Successful summarization logging
- **WHEN** a session is summarized with score 8.5 in 1 iteration taking 4.2 seconds
- **THEN** stderr SHALL contain a line matching: `[index-session] summary: score=8.5 iter=1 refined=false time=4.2s`

#### Scenario: Failed summarization logging
- **WHEN** the initial LLM call fails after retries
- **THEN** stderr SHALL contain a line matching: `[index-session] summary: failed after N retries`
