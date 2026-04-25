## Why

Session summaries are the only surviving record of most Claude Code sessions -- conversation archives get deleted, and the daily log summary is the sole copy of decisions, rejected approaches, and key findings. The current system generates summaries in a single pass with no quality check. Memento's research shows single-pass summarization achieves only 28% quality (scoring 8+/10), while two iterations of judge feedback bring this to 92%. When a single-pass summary drops a critical detail like "chose RRF over weighted merge because keyword results were being suppressed below threshold," that knowledge is permanently lost.

## What Changes

- Add an LLM-based judge step that scores each session summary on key dimensions (decisions and rationale, rejected approaches, key values/configs, file paths and code references, correctness, structure) adapted from Memento's 6-dimension rubric for software engineering context
- Add a refinement loop: if the judge scores a summary below threshold (default 8/10), feed the judge's specific feedback back to the summarizer for a second pass (max 2 iterations total, matching Memento's finding that additional iterations yield diminishing returns)
- Add a `summary_quality` metadata field to daily log entries recording the final judge score, iteration count, and whether refinement was triggered -- enabling measurement of summary quality over time
- Make the feature configurable (on/off, score threshold, max iterations) so it can be disabled when LLM costs or latency are a concern, or for automated agent sessions where summaries are less critical
- Adapt Memento's judge prompt from math/code reasoning to software engineering sessions: replace "formulas and equations" with "decisions and rationale," "numerical values" with "key identifiers, configs, and error messages," and "validation" with "what was tried and what was rejected"

## Capabilities

### New Capabilities
- `summary-judge`: LLM call that scores a session summary against the source conversation on 6 adapted dimensions (0-10 scale), returning a score and specific, actionable feedback identifying what is missing
- `summary-refine`: LLM call that takes an existing summary plus judge feedback and produces an improved summary that addresses all flagged gaps
- `summary-quality-tracking`: Metadata appended to daily log entries recording judge scores and iteration counts for observability

### Modified Capabilities
- `index_session.py`: Extended to run the summarize-judge-refine loop after generating the initial summary, before writing to the daily log. The existing single-pass path remains as the fallback when the feature is disabled or LLM calls fail.
- `memory-reindex.py` (SessionEnd hook): No change to the hook itself -- it already spawns `index_session.py` asynchronously, so added latency from refinement iterations does not block the user's terminal.

## Impact

- `scripts/index_session.py`: Primary change site. Currently does parse-chunk-insert with no summarization. Will gain: (1) initial summary generation, (2) judge call, (3) conditional refinement call, (4) quality metadata recording. Each LLM call needs retry-with-backoff (pattern exists in Memento's `summarize_iterative.py`).
- New file: `scripts/summary_prompts.py` (or `scripts/prompts/` directory) -- system and user prompts for the summarizer, judge, and refiner, adapted from Memento's `prompts/summary_prompt.txt` and `prompts/judge_prompt.txt`.
- `~/.claude-memory/memory/YYYY-MM-DD.md` (daily logs): Output format gains a quality metadata line per session entry. Existing entries remain readable.
- LLM dependency: Requires an OpenAI-compatible API endpoint. Can reuse the same model the MCP server already has access to, or use a dedicated key. Cost estimate: ~2-6 LLM calls per session (1 summarize + 1 judge + conditional 1 refine + 1 judge), with most sessions needing only 1 iteration based on Memento's 92% pass rate at iteration 2.
- No changes to: the MCP server (`unified_memory_server.py`), the Node.js indexer, the SQLite schema, or the SessionEnd hook script.
