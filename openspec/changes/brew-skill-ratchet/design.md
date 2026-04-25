## Context

Three existing systems partially cover the ratchet's needs:
1. **Failure audit** (`~/.claude/failure_audits/`) — PostToolUseFailure hook writes JSON per failure, weekly bash script aggregates with jq, generates HTML report
2. **Memory system** (`claude-memory`) — SessionEnd hooks write daily logs and index JSONL transcripts into SQLite (FTS5 + vector search)
3. **Session transcripts** (`~/.claude/projects/*/`) — JSONL files with every tool call, user message, and assistant response

The ratchet needs: all tool calls (not just failures), brew-skill attribution, sequence detection, and automated code generation + PR creation.

### Constraints
- `~/.claude/settings.json` corruption locks out Claude Code entirely — backup/validate/restore protocol is mandatory
- PostToolUse hooks must complete in <100ms to avoid blocking sessions
- Session JSONL files are the only source of user-message boundaries (needed for sequence splitting)
- Target brew-skill repos (e.g., `toastweb_wrapper`) must not require any changes — PRs are opened externally

## Goals / Non-Goals

### Goals
- Capture all tool calls to SQLite with brew-skill attribution
- Detect repeated multi-step tool-call sequences across sessions
- Automatically generate PRs that add new methods + skill updates to target brew-skill repos
- Ship as a single `brew install` with self-wiring hooks and cron
- Absorb the existing failure audit functionality

### Non-Goals
- Real-time pattern detection (weekly batch is fine)
- Modifying Claude Code's behavior mid-session based on detected patterns
- Supporting non-brew-skill tools (generic Bash/Read/Grep calls are captured but not analyzed for promotion)
- Auto-merging PRs (human reviews and merges)

## Architecture

```
brew-skill-ratchet/
├── src/ratchet/
│   ├── cli.py              # Argparse CLI: status, report, analyze, promote,
│   │                       #   install-hooks, install-cron, uninstall
│   ├── capture.py          # PostToolUse hook — fast (<100ms), writes to SQLite
│   ├── attribute.py        # Brew-skill attribution: match tool_input against
│   │                       #   registered patterns from ratchet.toml files
│   ├── analyzer.py         # Sequence detection: read JSONL for user-message
│   │                       #   boundaries, group attributed calls, find repeats
│   ├── proposer.py         # PR generation: clone target repo, invoke Claude to
│   │                       #   write method + tests + skill update, open PR via gh
│   ├── db.py               # SQLite schema, migrations, queries
│   ├── report.py           # HTML report generation (absorbs weekly-audit.sh)
│   └── settings.py         # Safe settings.json modification (backup/validate/restore)
├── .claude/skills/ratchet/
│   ├── SKILL.md
│   └── references/
│       └── cli-reference.md
├── Formula/ratchet.rb
├── scripts/
│   ├── install-skill.sh
│   └── build-formula.sh
└── pyproject.toml
```

## Data Model

### SQLite Database (`~/.ratchet/ratchet.db`)

```sql
-- Every tool call from every session
CREATE TABLE tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,           -- ISO 8601
    session_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,           -- "Bash", "Read", "Edit", etc.
    tool_input TEXT NOT NULL,          -- JSON string of the tool input
    succeeded INTEGER NOT NULL,        -- 1 or 0
    error TEXT,                        -- null if succeeded
    brew_skill TEXT,                   -- null if not attributed, else "toastweb-wrapper" etc.
    cwd TEXT,                          -- working directory
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_tool_calls_session ON tool_calls(session_id);
CREATE INDEX idx_tool_calls_brew_skill ON tool_calls(brew_skill);
CREATE INDEX idx_tool_calls_timestamp ON tool_calls(timestamp);

-- Brew-skill registry: which patterns map to which skill
CREATE TABLE brew_skills (
    name TEXT PRIMARY KEY,             -- "toastweb-wrapper"
    repo_path TEXT NOT NULL,           -- "~/toastweb_wrapper"
    skill_name TEXT NOT NULL,          -- "toastweb-toast"
    patterns TEXT NOT NULL             -- JSON array of attribution patterns
);

-- Detected sequences and their frequency
CREATE TABLE sequences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brew_skill TEXT NOT NULL,
    sequence TEXT NOT NULL,            -- JSON array of tool-call signatures
    frequency INTEGER NOT NULL,        -- count across sessions
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    sessions TEXT NOT NULL,            -- JSON array of session_ids
    status TEXT DEFAULT 'observed',    -- observed | documented | promoted
    promoted_pr TEXT,                  -- PR URL if promoted
    UNIQUE(brew_skill, sequence)
);
```

### Brew-skill Attribution

Each brew-skill repo can include a `ratchet.toml`:
```toml
[attribution]
name = "toastweb-wrapper"
skill = "toastweb-toast"
patterns = [
    "toastweb_wrapper",
    "ToastWebClient",
    "toastweb-wrapper",
]
```

The capture hook loads all registered patterns from the `brew_skills` table and matches against `tool_input`. Pattern matching is substring-based — if any pattern appears in the tool input string, the call is attributed.

Fallback: if no `ratchet.toml` exists, `ratchet register <name> --repo <path> --patterns "pat1,pat2"` manually adds entries.

## Key Design Decisions

### Capture hook writes directly to SQLite (not JSON files)
The failure audit uses individual JSON files with symlinks — this doesn't scale to all tool calls. SQLite with WAL mode handles concurrent writes from multiple sessions and supports efficient queries for analysis.

### Sequence detection uses JSONL transcripts, not the capture DB
User-message boundaries (needed to split sequences) are in the session JSONL files, not in the PostToolUse hook payload. The analyzer reads JONLs for structure, cross-references the capture DB for attribution, and outputs detected sequences.

### Promotion invokes Claude Code in a worktree
`ratchet promote` clones the target brew-skill repo into a git worktree, invokes Claude with a structured prompt ("write a method that encapsulates this sequence, update SKILL.md, add tests"), then opens a PR via `gh pr create`. The human reviews and merges.

### Install-hooks uses the settings.json safety protocol
1. `cp settings.json settings.json.bak`
2. Read and parse JSON
3. Add PostToolUse hook entry (if not already present)
4. Write modified JSON
5. `jq . settings.json > /dev/null` (validate)
6. If validation fails → `cp settings.json.bak settings.json` (restore)

Idempotent: if the hook entry already exists, skip silently.

### CLI supports --json for all reporting commands
`ratchet status --json`, `ratchet report --json`, `ratchet analyze --json` output structured JSON for Claude to parse. Human-readable colored text is the default.

## Alternatives Considered

1. **Build into claude-memory instead of a new repo** — Rejected because ratchet has different concerns (tool evolution vs. knowledge recall) and should follow its own brew-skill pattern.
2. **Use JSON files instead of SQLite** — Rejected for volume reasons (hundreds of tool calls/day vs. dozens of failures/week).
3. **Detect sequences at capture time** — Rejected because user-message boundaries aren't available in the PostToolUse hook payload. Batch analysis is simpler and sufficient (weekly).
4. **Auto-merge promoted PRs** — Rejected as too aggressive. Human review is the safety valve.
