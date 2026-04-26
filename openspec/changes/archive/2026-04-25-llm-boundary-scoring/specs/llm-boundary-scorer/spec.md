## ADDED Requirements

### Requirement: LLM boundary scorer produces score array compatible with heuristic scorer

The `LlmBoundaryScorer` module SHALL accept an array of `ConversationExchange[]` and return a `number[]` of boundary scores on the 0-3 scale, with length equal to `exchanges.length - 1`. This is the same interface as `scoreAllBoundaries()` in `semantic-chunker.ts`.

#### Scenario: Score array for a 10-exchange conversation
- **WHEN** `LlmBoundaryScorer.scoreAll(exchanges)` is called with 10 exchanges
- **THEN** the returned array SHALL have exactly 9 elements, each a number between 0.0 and 3.0 inclusive

#### Scenario: Single exchange produces empty array
- **WHEN** `LlmBoundaryScorer.scoreAll(exchanges)` is called with 1 exchange
- **THEN** the returned array SHALL be empty (`[]`)

#### Scenario: Empty exchange array
- **WHEN** `LlmBoundaryScorer.scoreAll(exchanges)` is called with 0 exchanges
- **THEN** the returned array SHALL be empty (`[]`)

### Requirement: Two-pass coprime window scoring with RRF-style averaging

The scorer SHALL use two passes with coprime window sizes (16 and 11 exchanges) and average the boundary scores from both passes for each boundary position. This matches Memento's `score_task()` pattern.

#### Scenario: Two-pass averaging produces stable scores
- **WHEN** a boundary at position `i` receives score 2.0 from pass 1 (window size 16) and score 1.0 from pass 2 (window size 11)
- **THEN** the final score for boundary `i` SHALL be 1.5

#### Scenario: Single-pass mode when configured
- **WHEN** the scorer is configured with `singlePass: true`
- **THEN** only one pass with window size 16 SHALL be executed and no averaging SHALL occur

### Requirement: Windowed LLM calls with conversation-adapted prompt

Each LLM call SHALL send a window of up to N adjacent exchanges (default 16) with boundary markers between them, using a prompt adapted for conversation exchange boundaries rather than chain-of-thought reasoning. The prompt SHALL instruct the LLM to return JSON `{"scores": [s1, s2, ...]}`.

#### Scenario: Window of 16 exchanges produces 15 boundary scores
- **WHEN** a window of 16 exchanges is sent to the LLM
- **THEN** the LLM response SHALL be parsed as JSON with a `scores` array of exactly 15 numbers

#### Scenario: Last window with fewer than 16 exchanges
- **WHEN** the final window contains 7 exchanges (because the conversation has 23 total exchanges, third window gets 7)
- **THEN** the LLM call for that window SHALL request exactly 6 boundary scores

#### Scenario: Exchange text formatting in prompt
- **WHEN** exchanges are formatted for the LLM prompt
- **THEN** each exchange SHALL include the user message and assistant message text, with `<<<BOUNDARY_N>>>` markers between adjacent exchanges

### Requirement: Native fetch client for OpenAI-compatible API

The scorer SHALL use Node.js native `fetch()` to call an OpenAI-compatible `/v1/chat/completions` endpoint. No `openai` npm package dependency SHALL be added.

#### Scenario: Successful API call
- **WHEN** the LLM endpoint is available and returns valid JSON
- **THEN** the scorer SHALL parse the response content as `{"scores": [...]}` and return the score values

#### Scenario: Configuration via environment variables
- **WHEN** `MEMORY_LLM_BASE_URL`, `MEMORY_LLM_MODEL`, and `MEMORY_LLM_API_KEY` environment variables are set
- **THEN** the scorer SHALL use those values for the API endpoint, model name, and authorization header

#### Scenario: Missing configuration
- **WHEN** LLM scoring is enabled but `MEMORY_LLM_BASE_URL` is not set
- **THEN** the scorer SHALL throw a descriptive error before attempting any API calls

### Requirement: Retry with exponential backoff on LLM failures

The scorer SHALL retry failed LLM calls up to 5 times with exponential backoff, matching Memento's `score_boundaries_batch()` retry pattern.

#### Scenario: Transient API error with successful retry
- **WHEN** the first LLM call fails with a network error and the second succeeds
- **THEN** the scorer SHALL return the scores from the successful second attempt

#### Scenario: JSON parse error triggers retry
- **WHEN** the LLM returns non-JSON content
- **THEN** the scorer SHALL retry up to 5 times, and if all attempts fail, return zeros for that window

#### Scenario: Score count mismatch triggers retry
- **WHEN** the LLM returns a `scores` array with the wrong number of elements
- **THEN** the scorer SHALL retry, and if all retries produce mismatched counts, pad or truncate to the expected count

### Requirement: Graceful fallback to heuristic scoring on total failure

If LLM scoring fails for all windows of a conversation file after exhausting retries, the scorer SHALL return `null` to signal the caller to fall back to heuristic scoring for that file.

#### Scenario: All LLM calls fail for a file
- **WHEN** every window in a conversation file fails LLM scoring after retries
- **THEN** the scorer SHALL return `null`
- **AND** the caller (`indexConversationFile`) SHALL use `scoreAllBoundaries()` instead

#### Scenario: Partial window failures within a file
- **WHEN** some windows succeed and some fail (returning zeros) within the same file
- **THEN** the scorer SHALL return the combined score array (with zeros for failed windows) rather than falling back entirely
