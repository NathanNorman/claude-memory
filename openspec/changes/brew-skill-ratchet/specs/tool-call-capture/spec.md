## ADDED Requirements

### REQ-TCC-1: PostToolUse Hook Capture
- The system MUST register a PostToolUse hook in `~/.claude/settings.json` that fires on every tool call
- The hook MUST complete in <100ms to avoid blocking Claude Code sessions
- The hook MUST write a row to `~/.ratchet/ratchet.db` SQLite database (WAL mode)
- Each row MUST contain: timestamp (ISO 8601), session_id, tool_name, tool_input (JSON string), succeeded (boolean), error (nullable), cwd

### REQ-TCC-2: Brew-Skill Attribution
- The hook MUST check tool_input against registered brew-skill patterns from the `brew_skills` table
- Attribution is substring match: if any registered pattern appears in the tool_input string, the call is attributed to that brew_skill
- If no pattern matches, `brew_skill` column MUST be null
- Attribution patterns are loaded once at hook startup and cached for the session

### REQ-TCC-3: Database Schema
- The database MUST be created at `~/.ratchet/ratchet.db` on first write
- The database MUST use WAL mode for concurrent access from multiple sessions
- The database MUST have a `busy_timeout` of 5000ms
- Schema migrations MUST be idempotent (CREATE TABLE IF NOT EXISTS)

### REQ-TCC-4: Failure Backward Compatibility
- All tool calls with `succeeded=false` MUST contain the error message in the `error` column
- `ratchet report` MUST be able to produce the same failure breakdown as the current `weekly-audit.sh`
