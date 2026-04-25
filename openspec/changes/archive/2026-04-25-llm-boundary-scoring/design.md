## Context

claude-memory's conversation indexer chunks exchanges using a heuristic boundary scorer (`scoreAllBoundaries()` in `src/semantic-chunker.ts`) that detects topic shifts via surface signals: regex topic phrases, file path diffs, tool type changes, time gaps, and read/write transitions. These scores feed a DP segmenter (`segmentVarianceDp()`) that finds optimal chunk boundaries maximizing `avg_boundary_score - lambda * CV(chunk_sizes)`.

The heuristic scorer achieves 100% on LongMemEval but misses subtle semantic transitions (e.g., pivoting debugging strategies on the same file, shifting from understanding to planning). Memento's data pipeline (`memento/data/pipeline/score.py`) demonstrates that an LLM judge with windowed scoring and two-pass coprime averaging produces higher-quality boundary scores for these cases.

The goal is to add an optional LLM scoring path for offline/batch reindexing that produces the same `number[]` output as the heuristic scorer, so the DP segmenter and all downstream logic remain unchanged.

**Key constraints:**
- The Node.js indexer runs in the reindex-cli context, not the Python MCP server
- LLM scoring is expensive (~0.5-2s per window) so it must be opt-in and cached
- The heuristic path must remain the default, untouched
- Two embedding paths already exist (Node.js Xenova + Python sentence-transformers); adding a third external dependency (LLM API) follows the same pattern

## Goals / Non-Goals

**Goals:**
- Drop-in LLM boundary scoring that returns `number[]` on the same 0-3 scale as `scoreAllBoundaries()`
- Two-pass coprime window averaging (16, 11) matching Memento's proven approach
- SQLite cache for LLM scores to avoid re-scoring unchanged exchange pairs across reindexes
- Opt-in activation via CLI flag and env var, off by default
- Graceful fallback to heuristic scoring on LLM failure

**Non-Goals:**
- Real-time LLM scoring during the Python MCP server's `memory_write` path
- Replacing or modifying the heuristic scorer
- Fine-tuning or training a scoring model
- Scoring markdown memory files (only conversation exchanges benefit from LLM scoring)
- Supporting streaming or partial LLM responses

## Decisions

### 1. Node.js `fetch` client instead of OpenAI SDK

**Decision:** Use native `fetch()` to call the OpenAI-compatible `/v1/chat/completions` endpoint.

**Rationale:** The indexer is a Node.js process. Adding `openai` as an npm dependency would be the third API client in this project (after Python `openai` and `sentence-transformers`). The API surface needed is a single POST with JSON body/response. Native `fetch` keeps dependencies minimal and matches the project's existing approach of avoiding unnecessary packages.

**Alternative considered:** Importing the `openai` npm package. Rejected because it adds ~2MB of dependencies for one API call.

### 2. Conversation-adapted prompt, not Memento's chain-of-thought prompt

**Decision:** Write a new scoring prompt targeting conversation exchange boundaries rather than reusing Memento's `boundary_score_system_prompt.txt`.

**Rationale:** Memento's prompt is heavily tuned for mathematical derivations, proof patterns, and code execution traces ("CRITICAL RULES FOR MATHEMATICAL DERIVATIONS", "CRITICAL RULES FOR PROOF PATTERNS"). Conversation exchanges have different boundary signals: task intent shifts, tool context changes, topic pivots. The 0-3 scale, JSON output format `{"scores": [...]}`, and windowed batch structure stay the same; only the scoring criteria change.

**Signals the conversation prompt should evaluate:**
- Does the user's intent shift between exchanges? (new task vs. continuation)
- Does the tool/file context change significantly?
- Is there a logical completion point? (solution delivered, error resolved)
- Would splitting here preserve or break reasoning context?

### 3. Exchange-pair content hash for cache keys

**Decision:** Cache key = `sha256(exchange[i].userMessage + exchange[i].assistantMessage + exchange[i+1].userMessage + exchange[i+1].assistantMessage)` combined with a scorer version string.

**Rationale:** The cache must invalidate when either exchange in a pair changes but remain stable across reindexes when content is unchanged. Hashing the concatenated text of both exchanges in the pair gives content-addressable caching. The scorer version string (e.g., `"llm-v1"`) forces cache invalidation when the prompt or model changes.

**Alternative considered:** Caching per-window (all 16 exchanges). Rejected because window boundaries shift between passes, making cache hits unlikely. Per-pair caching means individual pair scores can be reused even when window composition changes.

### 4. Scoring cache as a new SQLite table, not a separate DB

**Decision:** Add `boundary_score_cache` table to the existing `memory.db`.

**Rationale:** The indexer already opens `memory.db` and manages its lifecycle (WAL mode, busy_timeout, backup before reindex). A separate DB would add connection management complexity. The cache table is small (one row per exchange pair, ~100 bytes each) and benefits from the existing WAL concurrency.

### 5. Window-level LLM calls with per-pair score extraction

**Decision:** Send one LLM call per window (up to 16 exchanges), extract individual boundary scores, cache per-pair.

**Rationale:** Matches Memento's `score_boundaries_batch()` pattern. Sending one exchange pair per LLM call would be 10-50x more API calls. The window gives the LLM enough context to judge transitions accurately. Per-pair caching is done by decomposing window results after the LLM call returns.

### 6. Fallback strategy: per-file, not per-boundary

**Decision:** If LLM scoring fails for a conversation file (after retries), fall back to heuristic scoring for that entire file and continue the reindex.

**Rationale:** Mixing LLM and heuristic scores within a single file's boundary array would create inconsistent scoring dynamics for the DP segmenter (LLM scores use the full 0-3 range differently than heuristics). Falling back per-file keeps each file's scores internally consistent.

### 7. CHUNK_TOKENS meta key bump for mode differentiation

**Decision:** When LLM scoring is enabled, use a different `CHUNK_TOKENS` value (e.g., `"400-v3-semantic-llm"`) to force full reindex when switching between modes.

**Rationale:** The existing mechanism (`META_CHUNK_TOKENS` check in `indexAll()`) already handles this pattern for model changes. Reusing it avoids adding new invalidation logic. Switching from heuristic to LLM scoring (or back) triggers a clean reindex rather than leaving a mix of scoring methods in the database.

## Risks / Trade-offs

**[LLM endpoint unavailability during reindex]** The LLM endpoint (local vLLM, Bedrock, etc.) may be down or misconfigured. -> Mitigation: Validate endpoint connectivity with a single test call before starting the scoring loop. On failure, log a warning and fall back to heuristic scoring for the entire reindex run. No partial data corruption.

**[Prompt quality determines chunk quality]** The conversation-adapted prompt is new and untested. Poor prompt design could produce worse boundaries than heuristics. -> Mitigation: The feature is opt-in. Include a `--compare-scoring` diagnostic mode in tasks that runs both scorers on the same file and prints score deltas for manual review.

**[Cost of full archive re-scoring]** First LLM-scored reindex of ~500 conversations requires ~6,000 API calls (two passes). At local vLLM throughput this is 30-60 minutes. -> Mitigation: The scoring cache makes subsequent runs incremental. Rate limiting (configurable delay between batches) prevents overwhelming the endpoint.

**[Cache bloat over time]** Exchange pairs from deleted conversations remain in the cache. -> Mitigation: Add a cache eviction query that removes entries not referenced by any current file in the `files` table. Run during reindex cleanup phase.

**[Two-pass averaging may not improve conversation scoring]** Memento's coprime windows help with chain-of-thought sentences where window alignment matters. Conversation exchanges are coarser units. -> Mitigation: Make two-pass optional (`--single-pass` flag). Default to two-pass to match the proven approach but allow experimentation.

## Open Questions

1. **Which LLM model to default to?** Local vLLM with a small model (Qwen2.5-7B) is fast but may produce lower-quality scores than a larger model. Should the default config point to local vLLM or allow Bedrock/external providers?

2. **Should the cache store the raw per-pair score or the averaged two-pass score?** Storing raw per-pair scores from each pass separately allows re-averaging with different strategies later. Storing only the final averaged score is simpler but less flexible.

3. **Concurrency for LLM calls?** Memento uses `ThreadPoolExecutor` for parallel scoring. The Node.js indexer is single-threaded with async/await. Should we use `Promise.all` with a concurrency limiter (e.g., 4 concurrent requests) or keep it sequential? Sequential is simpler and avoids rate limiting issues.
