## MODIFIED Requirements

### Requirement: reindex-cli accepts --llm-scoring flag

`reindex-cli.ts` SHALL accept a `--llm-scoring` command-line flag that enables LLM-based boundary scoring for conversation files during the reindex.

#### Scenario: Flag passed on command line
- **WHEN** `node dist/reindex-cli.js --llm-scoring` is executed
- **THEN** `indexAll()` SHALL be called with `llmScoring: true`

#### Scenario: Flag not passed (default)
- **WHEN** `node dist/reindex-cli.js` is executed without `--llm-scoring`
- **THEN** `indexAll()` SHALL be called with `llmScoring: false` (heuristic scoring)

### Requirement: MEMORY_LLM_SCORING environment variable as alternative to flag

The `MEMORY_LLM_SCORING=1` environment variable SHALL enable LLM scoring as an alternative to the `--llm-scoring` CLI flag. The CLI flag takes precedence if both are set.

#### Scenario: Environment variable enables LLM scoring
- **WHEN** `MEMORY_LLM_SCORING=1 node dist/reindex-cli.js` is executed
- **THEN** `indexAll()` SHALL be called with `llmScoring: true`

#### Scenario: Environment variable set to 0
- **WHEN** `MEMORY_LLM_SCORING=0 node dist/reindex-cli.js` is executed
- **THEN** `indexAll()` SHALL be called with `llmScoring: false`

### Requirement: LLM endpoint validation before reindex starts

When LLM scoring is enabled, `reindex-cli` SHALL validate that the required environment variables (`MEMORY_LLM_BASE_URL`, `MEMORY_LLM_MODEL`) are set and the endpoint is reachable before starting the reindex.

#### Scenario: Missing LLM configuration
- **WHEN** `--llm-scoring` is passed but `MEMORY_LLM_BASE_URL` is not set
- **THEN** the CLI SHALL print an error message listing the required environment variables and exit with code 1 without starting the reindex

#### Scenario: Unreachable LLM endpoint
- **WHEN** `--llm-scoring` is passed and `MEMORY_LLM_BASE_URL` is set but the endpoint does not respond
- **THEN** the CLI SHALL print a warning and ask whether to fall back to heuristic scoring or abort (in non-interactive mode, fall back to heuristic scoring with a warning)

### Requirement: Progress logging for LLM scoring

When LLM scoring is active, the CLI SHALL log progress to stderr including the number of conversations scored, cache hit rate, and estimated time remaining.

#### Scenario: Progress output during LLM-scored reindex
- **WHEN** LLM scoring is processing conversation files
- **THEN** stderr SHALL show messages like `[claude-memory] LLM scoring: 50/500 conversations (42% cache hit rate)`
