## 1. Prompts Module

- [x] 1.1 Create `scripts/summary_prompts.py` with three prompt constants: `SUMMARIZER_SYSTEM`, `JUDGE_SYSTEM`, and `REFINER_SYSTEM`. Adapt Memento's judge rubric dimensions (decisions & rationale 0-3, key identifiers 0-2, approaches tried & rejected 0-2, file paths & code references 0-1, correctness 0-1, structure 0-1). Include `JUDGE_USER_TEMPLATE` and `REFINER_USER_TEMPLATE` with `{transcript}`, `{summary}`, and `{feedback}` placeholders.
- [x] 1.2 Add a `parse_judge_response(text: str) -> tuple[float, str]` function that extracts the numeric score (0-10) and feedback string from the judge LLM response, matching the `SCORE: X/10` and `FEEDBACK:` format from Memento's `parse_judge_score()`.

## 2. LLM Client Helpers

- [x] 2.1 Create `scripts/summary_llm.py` with a `call_llm(prompt: str, model: str, timeout: int = 90) -> str` function that invokes `subprocess.run(['claude', '-p', prompt, '--model', model, '--tools', '', '--no-session-persistence'])` and returns stdout. Strip `CLAUDECODE` from env to prevent nested-session rejection (matching `ingest_session.py` pattern).
- [x] 2.2 Add `retry_with_backoff(func, max_retries=3, initial_delay=2.0)` to `summary_llm.py`, adapted from Memento's `summarize_iterative.py:retry_with_backoff()`. Handle rate limit detection (429, "rate limit" in error string) with exponential backoff.

## 3. Summarize-Judge-Refine Loop

- [x] 3.1 Create `scripts/summary_refinement.py` with `generate_summary(transcript: str, model: str) -> str` that calls the LLM with the summarizer prompt and returns the initial summary text.
- [x] 3.2 Add `judge_summary(transcript: str, summary: str, model: str) -> tuple[float, str]` that calls the LLM with the judge prompt and parses the score and feedback using `parse_judge_response`.
- [x] 3.3 Add `refine_summary(transcript: str, summary: str, feedback: str, model: str) -> str` that calls the LLM with the refiner prompt (original summary + judge feedback + transcript) and returns the improved summary.
- [x] 3.4 Add `summarize_with_refinement(transcript: str, model: str, threshold: float, max_iter: int) -> dict` that orchestrates the loop: generate -> judge -> (optionally refine -> re-judge) up to `max_iter` times. Return dict with keys: `summary`, `score`, `iterations`, `refined` (bool), `elapsed_seconds`.

## 4. Integrate into index_session.py

- [x] 4.1 Add environment variable reading at top of `scripts/index_session.py`: `MEMORY_SUMMARY_ENABLED`, `MEMORY_SUMMARY_THRESHOLD` (default 8), `MEMORY_SUMMARY_MAX_ITER` (default 2), `MEMORY_SUMMARY_MODEL` (default "haiku").
- [x] 4.2 Add `prepare_transcript(filtered_exchanges) -> str` function that formats exchanges as `[User]: ... [Assistant]: ...` and truncates to 30,000 chars with `...(truncated)` marker.
- [x] 4.3 After the existing chunk insertion and `files` table update, add a conditional block: if `MEMORY_SUMMARY_ENABLED == '1'` and `len(filtered) >= 3`, call `summarize_with_refinement()` with the prepared transcript.
- [x] 4.4 On successful summarization, update the `files` row to set `summary` column to `[quality: score=X.X iter=N refined=BOOL]\n{summary_text}`. Modify the existing `INSERT OR REPLACE INTO files` to include the `summary` column.
- [x] 4.5 Log quality metrics to stderr: `[index-session] summary: score=X.X iter=N refined=BOOL time=X.Xs` on success, or `[index-session] summary: failed after N retries` on failure.

## 5. Testing

- [x] 5.1 Create `scripts/test_summary_prompts.py` with unit tests for `parse_judge_response()`: test valid score parsing, missing score fallback to 0.0, feedback extraction, edge cases (empty response, malformed score).
- [x] 5.2 Create `scripts/test_summary_refinement.py` with integration test that runs `summarize_with_refinement()` against a sample transcript excerpt (can use a fixture from an existing daily log entry). Requires `claude` CLI available. Mark as `@skip` in CI.
- [x] 5.3 Manual end-to-end test: ran with deep-live-cam session. (a) summary in files.summary, (b) quality prefix `[quality: score=9.0 iter=2 refined=true]`, (c) log: `score=9.0 iter=2 refined=true time=81.1s`.

## 6. Enable and Validate

- [x] 6.1 Add `MEMORY_SUMMARY_ENABLED=1` and `MEMORY_SUMMARY_MODEL=sonnet` to `~/.zshrc` (user requested sonnet over haiku).
- [ ] 6.2 Monitor `~/.claude-memory/index-session.log` for 1-2 days. Verify: summaries are being generated, scores are reasonable (>= 7 average), timing is acceptable (< 20s per session), no error storms.
- [ ] 6.3 Spot-check 5 session summaries in the DB (`SELECT file_path, summary FROM files WHERE summary IS NOT NULL ORDER BY last_indexed DESC LIMIT 5`) against the actual session content to validate quality.
