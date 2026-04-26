## MODIFIED Requirements

### Requirement: indexConversationFile supports LLM boundary scoring

`indexConversationFile()` SHALL accept an `llmScoring` option. When enabled, it SHALL call `LlmBoundaryScorer.scoreAll()` instead of `scoreAllBoundaries()` before passing scores to `segmentVarianceDp()`. All downstream logic (DP segmentation, chunk building, embedding, insertion) SHALL remain unchanged.

#### Scenario: LLM scoring enabled and succeeds
- **WHEN** `indexConversationFile()` is called with `llmScoring: true` and the LLM scorer returns a valid score array
- **THEN** the LLM scores SHALL be passed to `segmentVarianceDp()` and the conversation SHALL be chunked using those scores

#### Scenario: LLM scoring enabled but scorer returns null (total failure)
- **WHEN** `indexConversationFile()` is called with `llmScoring: true` and `LlmBoundaryScorer.scoreAll()` returns `null`
- **THEN** `scoreAllBoundaries()` SHALL be called as fallback and a warning SHALL be logged to stderr

#### Scenario: LLM scoring disabled (default)
- **WHEN** `indexConversationFile()` is called without `llmScoring` or with `llmScoring: false`
- **THEN** the existing heuristic path via `chunkExchangesSemantic()` SHALL be used with no changes

#### Scenario: LLM scoring does not affect markdown memory files
- **WHEN** `indexFile()` is called for a markdown memory file (e.g., `MEMORY.md`, `memory/2024-01-15.md`)
- **THEN** the `llmScoring` option SHALL have no effect; markdown files SHALL continue using `chunkMarkdown()`

### Requirement: indexAll passes llmScoring option through to conversation indexing

`indexAll()` SHALL accept an `llmScoring` option and pass it through to each `indexConversationFile()` call.

#### Scenario: LLM scoring propagated to conversation files
- **WHEN** `indexAll(db, memoryDir, archiveDir, { llmScoring: true })` is called
- **THEN** every `indexConversationFile()` call SHALL receive `llmScoring: true`

### Requirement: CHUNK_TOKENS meta key differentiates scoring modes

When LLM scoring is enabled, the `CHUNK_TOKENS` meta value SHALL differ from the heuristic value (e.g., `"400-v3-semantic-llm"` vs `"400-v3-semantic"`). This SHALL trigger a full reindex when switching between scoring modes.

#### Scenario: Switching from heuristic to LLM scoring
- **WHEN** the previous reindex used heuristic scoring (`CHUNK_TOKENS = "400-v3-semantic"`) and the current run has `llmScoring: true`
- **THEN** the stored `CHUNK_TOKENS` SHALL not match and `indexAll()` SHALL wipe all chunks and files before reindexing

#### Scenario: Switching from LLM to heuristic scoring
- **WHEN** the previous reindex used LLM scoring and the current run has `llmScoring: false`
- **THEN** the `CHUNK_TOKENS` mismatch SHALL trigger a full reindex back to heuristic-scored chunks
