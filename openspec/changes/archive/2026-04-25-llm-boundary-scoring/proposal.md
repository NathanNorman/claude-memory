## Why

The heuristic boundary scorer in `semantic-chunker.ts` achieves 100% on LongMemEval but relies on surface signals (regex topic phrases, file path diffs, time gaps). It cannot detect subtle semantic transitions -- a user pivoting from debugging approach A to approach B on the same file, or shifting from understanding a system to planning changes to it. Memento's LLM boundary scorer catches these transitions because it evaluates semantic coherence and logical flow directly. Adding an LLM scoring path for offline/batch indexing would improve chunk quality where it matters most (long sessions, daily memory files) without affecting real-time indexing latency.

## What Changes

- **New `LlmBoundaryScorer` module** (`src/llm-boundary-scorer.ts`): Adapts Memento's `score.py` pattern to claude-memory's exchange-based chunking. Takes an array of `ConversationExchange[]` and returns `number[]` boundary scores (0-3), same interface as `scoreAllBoundaries()`. Uses two-pass coprime windows (16, 11) with RRF-style averaging, calling any OpenAI-compatible endpoint.

- **Prompt adapted for conversations**: Memento's prompt targets chain-of-thought reasoning (math derivations, proof patterns). The claude-memory prompt needs to target conversation exchanges -- evaluating whether adjacent exchanges share a task, whether the user's intent shifted, whether tool usage context changed. The 0-3 scale and JSON output format stay the same.

- **`indexer.ts` gains an `llmScoring` option**: When enabled (off by default), `indexConversationFile()` calls `LlmBoundaryScorer` instead of `scoreAllBoundaries()` before passing scores to `segmentVarianceDp()`. The DP segmenter, chunk building, and embedding pipeline are unchanged.

- **Scoring mode selection via environment or CLI flag**: `MEMORY_LLM_SCORING=1` env var or `--llm-scoring` flag on `reindex-cli.ts`. This keeps the default path fast (heuristics, no network calls) while enabling LLM scoring for scheduled reindexes.

- **LLM client configuration**: Reuses the existing Python venv's OpenAI client pattern or a lightweight `fetch`-based client in Node.js. Endpoint URL, model, and API key configurable via env vars (`MEMORY_LLM_BASE_URL`, `MEMORY_LLM_MODEL`, `MEMORY_LLM_API_KEY`). Default: local vLLM or the same provider used by other tools.

- **Scoring cache in SQLite**: New `boundary_score_cache` table keyed by `(exchange_pair_hash, scorer_version)` storing the LLM-assigned score. Prevents re-scoring unchanged exchange pairs across reindexes. The heuristic scorer is free so it needs no cache; LLM calls are expensive and slow.

## Capabilities

### New Capabilities

- `LlmBoundaryScorer`: Scores inter-exchange boundaries using an LLM judge with two-pass coprime windows. Drop-in replacement for `scoreAllBoundaries()` returning the same `number[]` on the same 0-3 scale.

- `boundary_score_cache` table: Persists LLM boundary scores keyed by exchange-pair content hash, avoiding redundant API calls on incremental reindexes.

- `--llm-scoring` CLI flag / `MEMORY_LLM_SCORING` env var: Opt-in toggle for LLM-based boundary scoring during reindex.

### Modified Capabilities

- `indexConversationFile()`: Gains a code path that, when LLM scoring is enabled, substitutes LLM scores for heuristic scores before calling `segmentVarianceDp()`. All downstream logic (DP segmentation, embedding, chunk insertion) is unchanged.

- `reindex-cli.ts`: Accepts `--llm-scoring` flag, passes it through to `indexAll()`.

- `CHUNK_TOKENS` meta key: Bumped to differentiate LLM-scored indexes from heuristic-scored ones, triggering a full reindex when switching modes (same mechanism used today for model changes).

## Impact

**Affected code:**
- `src/semantic-chunker.ts` -- no changes; `scoreAllBoundaries()` remains the default path
- `src/indexer.ts` -- conditional LLM scoring path in `indexConversationFile()`
- `src/reindex-cli.ts` -- new CLI flag
- `src/db.ts` -- new `boundary_score_cache` table creation in schema init

**Dependencies:**
- OpenAI-compatible LLM endpoint (local vLLM, Bedrock, or any provider). No new npm packages required -- Node.js `fetch` suffices for the API calls.

**Cost/latency:**
- Heuristic scoring: ~0ms per conversation (unchanged default)
- LLM scoring: ~0.5-2s per boundary window (16 exchanges), so a 100-exchange conversation needs ~12 API calls across two passes. At typical local vLLM throughput, a full archive reindex of ~500 conversations would take 30-60 minutes. The scoring cache makes subsequent reindexes incremental.

**Risk:**
- LLM scoring is strictly additive and off by default. The heuristic path is untouched. If LLM scoring fails mid-reindex (endpoint down, auth error), the indexer falls back to heuristic scores for remaining files -- no data loss, no partial corruption.
