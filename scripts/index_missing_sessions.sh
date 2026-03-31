#!/bin/bash
# Catch-all: index any session JSONL files that the SessionEnd hook missed.
# Runs via claude-cron every 30 minutes. Purely additive — never deletes.

PYTHON="$HOME/.claude-memory/graphiti-venv/bin/python3"
SCRIPT="$HOME/claude-memory/scripts/index_session.py"
DB="$HOME/.claude-memory/index/memory.db"
PROJECTS="$HOME/.claude/projects"

if [ ! -f "$SCRIPT" ] || [ ! -f "$DB" ]; then
    echo "Missing script or DB, exiting"
    exit 1
fi

count=0
skipped=0

for jsonl in $(find "$PROJECTS" -name "*.jsonl" -not -path "*/subagents/*" -not -name "agent-*" 2>/dev/null); do
    name=$(basename "$jsonl")
    # Check if already indexed
    indexed=$(sqlite3 "$DB" "SELECT COUNT(*) FROM chunks WHERE file_path LIKE '%$name';" 2>/dev/null)
    if [ "$indexed" = "0" ]; then
        "$PYTHON" "$SCRIPT" "$jsonl" 2>&1
        count=$((count + 1))
    else
        skipped=$((skipped + 1))
    fi
done

echo "Done: $count indexed, $skipped already indexed"
