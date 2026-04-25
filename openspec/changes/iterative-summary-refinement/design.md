## Context

Session summaries in claude-memory are the sole surviving record of most Claude Code sessions. The current system (`scripts/index_session.py`) parses conversation JSONL files, chunks them into exchange-aware segments, and inserts them into SQLite (FTS5 + embeddings). No summarization or quality check occurs -- chunks are stored verbatim.

Daily log entries (`~/.claude-memory/memory/YYYY-MM-DD.md`) are written by the MCP server's `memory_write` tool during sessions. These entries currently contain task name, turn count, and whatever the session chose to record. A separate "Daily Consolidation" section synthesizes themes, but this too is single-pass with no quality feedback loop.

Memento's research (in the sibling `memento/` directory) demonstrates that iterative judge-based refinement raises summary quality from 28% to 92% (scoring 8+/10), with diminishing returns after 2 iterations. The Memento pipeline (`data/pipeline/summarize_iterative.py`) implements this as: initial summarize -> judge scores on 6 dimensions -> conditional refine -> re-judge.

The SessionEnd hook (`~/.claude/hooks/memory-reindex.py`) already spawns `scripts/index_session.py` asynchronously via `subprocess.Popen(start_new_session=True)`, so added latency from LLM calls does not block the user's terminal.

**Stakeholders**: The user (sole consumer of memory search results), automated agents (which query memory via MCP tools).

**Constraints**:
- The Python MCP server venv (`~/.claude-memory/graphiti-venv/`) has `sentence-transformers`, `torch`, `numpy`, `mcp` but no OpenAI client library currently
- LLM calls require an API endpoint -- either the user's existing Anthropic/OpenAI key or a local model
- The `index_session.py` script runs as a detached background process; stdout/stderr go to `~/.claude-memory/index-session.log`

## Goals / Non-Goals

**Goals:**
- Add a summarize-judge-refine loop to session indexing that produces information-dense summaries capturing decisions, rationale, rejected approaches, key identifiers, and file paths
- Store the summary in the existing `files.summary` column so it appears in `memory_search` results
- Record quality metadata (score, iteration count, refinement triggered) for observability
- Make the feature configurable and gracefully degradable (no LLM available -> skip refinement, keep existing behavior)
- Adapt Memento's judge rubric from math/code reasoning to software engineering sessions

**Non-Goals:**
- Modifying the MCP server (`unified_memory_server.py`) -- it already reads `files.summary` and displays it in search results
- Changing the Node.js indexer -- it handles batch reindexing of archives, not live session capture
- Changing the SessionEnd hook -- it already spawns `index_session.py` asynchronously
- Retroactive re-summarization of already-indexed sessions (future work)
- Real-time streaming of judge feedback (the process runs in background)

## Decisions

### D1: LLM client -- use `claude` CLI with `--model haiku`

**Decision**: Invoke the `claude` CLI tool for LLM calls, matching the existing pattern in `scripts/ingest_session.py:summarize_for_graphiti()`.

**Rationale**: The `ingest_session.py` script already uses `subprocess.run(['claude', '-p', prompt, '--model', 'haiku', ...])` successfully. This avoids adding a new Python dependency (`openai` or `anthropic` SDK) to the graphiti-venv. The `claude` CLI handles authentication, model routing, and retries internally.

**Alternatives considered**:
- **OpenAI Python SDK**: Would require `pip install openai` into graphiti-venv, plus managing API keys. More flexible (can point at local vLLM) but adds dependency.
- **Anthropic Python SDK**: Similar dependency issue. The `claude` CLI already wraps this.
- **Direct HTTP requests**: No new dependencies but requires manual auth token management.

**Trade-off**: The `claude` CLI adds ~1s overhead per call for process spawn. For 2-4 calls total per session, this is acceptable given the process runs in background.

### D2: Integration point -- extend `scripts/index_session.py`, not MCP server

**Decision**: Add summarization to the standalone `scripts/index_session.py` script, not to the `index_session` MCP tool in `unified_memory_server.py`.

**Rationale**: The script is what the SessionEnd hook actually calls. The MCP tool duplicates much of the same logic but runs inside the long-lived server process -- adding blocking LLM calls there risks degrading MCP responsiveness. The script runs as a detached process with its own lifecycle.

**Alternatives considered**:
- **MCP tool**: Would centralize logic but blocks the server during LLM calls. The MCP server uses `asyncio` but `subprocess.run` is synchronous.
- **Separate summarization script**: Cleaner separation but adds another script to the chain and complicates the hook.

### D3: Prompts -- adapt Memento's prompts inline, not in separate files

**Decision**: Define the summarizer, judge, and refiner prompts as string constants in a new `scripts/summary_prompts.py` module.

**Rationale**: Memento uses external `.txt` files with a `===== USER Prompt =====` separator convention. For claude-memory, the prompts are shorter (session summaries, not multi-block math traces) and tightly coupled to the scoring logic. A Python module with constants is simpler to maintain and avoids the file-loading/parsing overhead.

The judge rubric adapts Memento's 6 dimensions:
| Memento dimension | claude-memory adaptation |
|---|---|
| Formulas & equations (0-3) | Decisions & rationale (0-3) |
| Numerical values (0-2) | Key identifiers, configs, errors (0-2) |
| Methods & techniques (0-2) | Approaches tried & rejected (0-2) |
| Validation & verification (0-1) | File paths & code references (0-1) |
| Correctness filter (0-1) | Correctness (0-1) |
| Result-first structure (0-1) | Structure (0-1) |

### D4: Summary storage -- use existing `files.summary` column

**Decision**: Store the final summary in the `files.summary` column of the `files` table, which already exists and is already surfaced by `memory_search`.

**Rationale**: The column was added via migration in `db.ts` and stores episodic-memory summaries. The MCP server's `memory_search` already reads it (`flat_backend.get_file_summary(fp)`) and includes it in results truncated to 200 chars. No schema changes needed.

### D5: Quality metadata -- append to daily log via `memory_write` pattern

**Decision**: After generating a quality-checked summary, write a quality metadata line to the daily log file directly (same pattern as the existing session entries). Format: `<!-- quality: score=8.5 iterations=1 refined=false -->` as an HTML comment within the session's `##` block.

**Rationale**: HTML comments are invisible in rendered markdown but preserved in the raw file. They can be grepped/parsed for quality tracking without cluttering the human-readable log. No schema changes needed.

**Alternatives considered**:
- **Separate quality database table**: More structured but adds schema migration complexity for marginal benefit.
- **JSON sidecar file**: Adds file proliferation.

### D6: Retry and error handling -- follow Memento's `retry_with_backoff` pattern

**Decision**: Implement exponential backoff with max 3 retries for LLM calls, matching Memento's `retry_with_backoff()` function. On total failure, fall back to no-summary behavior (the session is still indexed, just without a summary).

**Rationale**: The `claude` CLI can fail due to rate limits, network issues, or auth problems. Graceful degradation ensures session indexing is never blocked by summarization failures.

### D7: Configuration -- environment variables

**Decision**: Control the feature via environment variables read by `index_session.py`:
- `MEMORY_SUMMARY_ENABLED=1` (default: `0` initially, flip to `1` after validation)
- `MEMORY_SUMMARY_THRESHOLD=8` (judge score threshold, default 8)
- `MEMORY_SUMMARY_MAX_ITER=2` (max refinement iterations, default 2)
- `MEMORY_SUMMARY_MODEL=haiku` (model for summarize/judge/refine calls)

**Rationale**: Environment variables are the simplest configuration mechanism for a script invoked by a hook. No config files to manage. The hook script can pass them through, or they can be set in the user's shell profile.

## Risks / Trade-offs

**[Risk] LLM calls add 5-15s to session indexing** -> Mitigation: Process runs in background (`start_new_session=True`), user never waits. Log file captures timing for monitoring.

**[Risk] `claude` CLI unavailable or rate-limited** -> Mitigation: Graceful fallback -- if any LLM call fails after retries, session is indexed without summary. Existing behavior preserved.

**[Risk] Summary quality varies by model** -> Mitigation: `MEMORY_SUMMARY_MODEL` env var allows switching models. Start with `haiku` (fast, cheap), upgrade to `sonnet` if quality is insufficient.

**[Risk] Judge scores may not calibrate well for SE sessions initially** -> Mitigation: The adapted rubric is conservative (same 0-10 scale, same threshold). Quality metadata in daily logs enables tracking calibration over time.

**[Risk] Long sessions produce transcripts too large for context window** -> Mitigation: Truncate transcript to first 30K chars (matching `ingest_session.py:summarize_for_graphiti()`). Most decisions happen early in sessions.

**[Trade-off] Using `claude` CLI vs SDK** -> Adds ~1s per-call overhead but avoids dependency management. Acceptable for 2-4 background calls.

**[Trade-off] HTML comment metadata vs structured storage** -> Less queryable but zero schema changes. Can be migrated to structured storage later if analytics demand grows.

## Migration Plan

1. **Phase 1 (ship disabled)**: Add `summary_prompts.py` and extend `index_session.py` with the summarize-judge-refine loop behind `MEMORY_SUMMARY_ENABLED=0`. Merge to main. No behavior change.
2. **Phase 2 (enable + validate)**: Set `MEMORY_SUMMARY_ENABLED=1` in the hook environment. Monitor `~/.claude-memory/index-session.log` for quality scores and timing. Review a week of daily logs for summary quality.
3. **Phase 3 (tune)**: Adjust threshold, model, and prompt based on observed scores. Consider enabling by default.

**Rollback**: Unset `MEMORY_SUMMARY_ENABLED` or set to `0`. Sessions continue to be indexed without summaries. No data loss -- summaries are additive.

## Open Questions

1. **Should the summary replace or augment the existing daily log entry?** Current plan: augment (add summary text + quality metadata alongside existing task/turns info). But the daily log entries are written by sessions themselves, not by `index_session.py`. The summary would go into `files.summary` in the DB, not the daily log file. Need to clarify if we also want to append to the `.md` file.

2. **Should we summarize agent subagent sessions?** Currently `index_session.py` is only called for main sessions (the hook skips `agent-*.jsonl`). Agent sessions may have valuable decisions too, but they're typically single-phase and short.

3. **Token budget for judge prompt**: Memento uses `max_completion_tokens=16000` for its judge. For session summaries, the judge response is much shorter (score + feedback). Should we cap at 2000 tokens to save cost?
