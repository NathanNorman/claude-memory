## ADDED Requirements

### REQ-SD-1: Sequence Extraction
- The analyzer MUST extract ordered tool-call sequences from session data
- Sequences MUST be scoped to a single brew-skill (non-attributed calls are filtered out)
- Sequences MUST be split on user-message boundaries (a new user message starts a new sequence)
- User-message boundaries MUST be read from session JSONL transcript files

### REQ-SD-2: Sequence Matching
- Two sequences are considered identical if they contain the same tool-call method names in the same order
- Method names are extracted from tool_input: for Bash calls containing Python, extract the method name (e.g., `client.get_shard(...)` → `get_shard`); for CLI calls, extract the subcommand
- Sequences shorter than 2 calls MUST be excluded
- Sequences MUST appear in at least 3 distinct sessions to be considered candidates

### REQ-SD-3: Frequency Tracking
- Detected sequences MUST be stored in the `sequences` table with: brew_skill, sequence (JSON array), frequency, first_seen, last_seen, list of session_ids
- Sequence records MUST be upserted: if the same (brew_skill, sequence) exists, update frequency, last_seen, and append session_id
- Each sequence MUST have a status: `observed` (newly detected), `documented` (added to SKILL.md), `promoted` (PR opened)

### REQ-SD-4: Analysis Scheduling
- Analysis MUST be runnable on-demand via `ratchet analyze`
- Analysis MUST be schedulable via cron (registered by `ratchet install-cron`)
- Default schedule: weekly
- Analysis MUST be incremental: only process sessions newer than the last analysis run
