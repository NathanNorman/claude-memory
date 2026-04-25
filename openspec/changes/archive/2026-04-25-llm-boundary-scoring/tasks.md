## 1. Database Schema

- [x] 1.1 Add `boundary_score_cache` table creation to `src/db.ts` `openDb()` with columns: `pair_hash TEXT`, `scorer_version TEXT`, `score REAL`, `created_at INTEGER`, composite PK on `(pair_hash, scorer_version)`
- [x] 1.2 Add `getCachedBoundaryScore(db, pairHash, scorerVersion)` and `setCachedBoundaryScore(db, pairHash, scorerVersion, score)` helper functions to `src/db.ts`
- [x] 1.3 Add `evictStaleBoundaryScores(db)` function that deletes cache entries not referenced by any current exchange pair

## 2. LLM Client

- [x] 2.1 Create `src/llm-client.ts` with a `callChatCompletion(baseUrl, model, apiKey, messages)` function using native `fetch()` targeting `/v1/chat/completions`
- [x] 2.2 Add retry logic with exponential backoff (max 5 retries, starting at 1s) matching Memento's `score_boundaries_batch()` pattern
- [x] 2.3 Add JSON response parsing with markdown code fence stripping (handle models that wrap JSON in ``` blocks)
- [x] 2.4 Add `validateLlmConfig()` function that checks `MEMORY_LLM_BASE_URL` and `MEMORY_LLM_MODEL` env vars and optionally pings the endpoint

## 3. Scoring Prompt

- [x] 3.1 Create `src/prompts/boundary-score-system.txt` — conversation-adapted system prompt evaluating task intent shifts, tool context changes, logical completion points (not Memento's math/proof rules)
- [x] 3.2 Create `src/prompts/boundary-score-user.txt` — user prompt template with `{text}`, `{boundaries}`, `{count}` placeholders matching Memento's format
- [x] 3.3 Add prompt loading utility in `src/llm-boundary-scorer.ts` that reads prompts from disk at module init

## 4. LLM Boundary Scorer

- [x] 4.1 Create `src/llm-boundary-scorer.ts` with `LlmBoundaryScorer` class that accepts `ConversationExchange[]` and returns `Promise<number[] | null>`
- [x] 4.2 Implement `scoreWindow(exchanges, startIdx, endIdx)` — formats exchanges with `<<<BOUNDARY_N>>>` markers, calls LLM, parses `{"scores": [...]}` response
- [x] 4.3 Implement `scoreWithWindow(exchanges, windowSize)` — iterates windows across all exchanges, collects per-boundary scores, handles cache lookup/write for each pair
- [x] 4.4 Implement `scoreAll(exchanges)` — two-pass with window sizes 16 and 11, averages per-boundary scores across passes. Support `singlePass` config option
- [x] 4.5 Implement fallback: return `null` when all windows fail, return zeros for individual failed windows within an otherwise successful file

## 5. Indexer Integration

- [x] 5.1 Add `llmScoring?: boolean` option to `indexConversationFile()` in `src/indexer.ts`
- [x] 5.2 When `llmScoring` is true, call `LlmBoundaryScorer.scoreAll()` instead of `scoreAllBoundaries()` before `segmentVarianceDp()`. On `null` return, fall back to `scoreAllBoundaries()` with stderr warning
- [x] 5.3 Add `llmScoring?: boolean` option to `indexAll()` and pass it through to each `indexConversationFile()` call
- [x] 5.4 Update `CHUNK_TOKENS` logic: use `"400-v4-semantic-llm"` when `llmScoring` is true, keep `"400-v4-semantic-md"` as default. This triggers full reindex on mode switch
- [x] 5.5 Call `evictStaleBoundaryScores(db)` during the reindex cleanup phase (after prune loop)

## 6. CLI Integration

- [x] 6.1 Add `--llm-scoring` flag parsing to `src/reindex-cli.ts` (check `process.argv` or use a minimal arg parser)
- [x] 6.2 Add `MEMORY_LLM_SCORING` env var check (`"1"` = enabled), CLI flag takes precedence
- [x] 6.3 When LLM scoring is enabled, call `validateLlmConfig()` before starting reindex; exit with code 1 and descriptive error if config is missing
- [x] 6.4 Add progress logging to stderr: conversation count, cache hit rate, estimated time remaining

## 7. Build & Types

- [x] 7.1 Add `src/llm-boundary-scorer.ts` and `src/llm-client.ts` to the esbuild entry points or verify tsc picks them up
- [x] 7.2 Export `LlmBoundaryScorer` types needed by indexer (ensure no circular imports)
- [x] 7.3 Run `npm run typecheck` and fix any type errors

## 8. Testing

- [x] 8.1 Add integration tests to `src/integration.test.ts`: LLM scorer returns correct-length array for mock exchanges (mock the fetch call)
- [x] 8.2 Add integration test: cache hit skips LLM call (verify fetch not called on second invocation with same exchanges)
- [x] 8.3 Add integration test: scorer returns `null` on total LLM failure, indexer falls back to heuristic scores
- [x] 8.4 Add integration test: `CHUNK_TOKENS` mismatch triggers full reindex when switching scoring modes
- [x] 8.5 Manual validation: validated via `scripts/validate_llm_scoring.py` with sonnet on a real 19-exchange session. Scores semantically correct: strong (3.0) at major topic shifts, no-break (0.0) at command re-runs, avg=0.80.
