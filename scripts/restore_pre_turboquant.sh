#!/bin/bash
# Restore unified-memory database to pre-TurboQuant state
# Usage: bash scripts/restore_pre_turboquant.sh [--dry-run]
#        bash scripts/restore_pre_turboquant.sh [--db-only]
#        bash scripts/restore_pre_turboquant.sh
#
# --dry-run:  Show what would happen without changing anything
# --db-only:  Restore only the SQLite database (skip curated files)
# (default):  Restore database + curated memory files (MEMORY.md, memory/*.md)

set -euo pipefail

BACKUP_DB="$HOME/.claude-memory/backups/memory-pre-turboquant-20260330.db"
BACKUP_CURATED="$HOME/.claude-memory/backups/curated-pre-turboquant-20260330.tar.gz"
LIVE_DB="$HOME/.claude-memory/index/memory.db"
MEMORY_DIR="$HOME/.claude-memory"

# Expected counts from the backup (verified at backup time)
EXPECTED_CHUNKS=2147
EXPECTED_EMBEDDINGS=2147
EXPECTED_FILES=296

# --- Preflight checks ---

if [ ! -f "$BACKUP_DB" ]; then
    echo "ERROR: DB backup not found at $BACKUP_DB"
    exit 1
fi

if [ ! -f "$LIVE_DB" ]; then
    echo "ERROR: Live DB not found at $LIVE_DB"
    exit 1
fi

# --- Dry run ---

if [ "${1:-}" = "--dry-run" ]; then
    echo "DRY RUN — would restore from:"
    echo ""
    echo "  DB backup:      $BACKUP_DB ($(du -h "$BACKUP_DB" | cut -f1))"
    echo "  Curated backup: $BACKUP_CURATED ($(du -h "$BACKUP_CURATED" 2>/dev/null | cut -f1 || echo 'MISSING'))"
    echo "  Live DB:        $LIVE_DB ($(du -h "$LIVE_DB" | cut -f1))"
    echo ""
    echo "Backup DB stats:"
    sqlite3 "$BACKUP_DB" "SELECT '  Chunks:          ' || COUNT(*) FROM chunks;"
    sqlite3 "$BACKUP_DB" "SELECT '  With embeddings: ' || COUNT(*) FROM chunks WHERE embedding IS NOT NULL;"
    sqlite3 "$BACKUP_DB" "SELECT '  Files:           ' || COUNT(*) FROM files;"
    sqlite3 "$BACKUP_DB" "SELECT '  Embedding cache: ' || COUNT(*) FROM embedding_cache;"
    echo ""
    echo "  Backup integrity: $(sqlite3 "$BACKUP_DB" 'PRAGMA integrity_check;')"
    echo ""
    echo "Current live DB stats:"
    sqlite3 "$LIVE_DB" "SELECT '  Chunks:          ' || COUNT(*) FROM chunks;"
    sqlite3 "$LIVE_DB" "SELECT '  With embeddings: ' || COUNT(*) FROM chunks WHERE embedding IS NOT NULL;"
    sqlite3 "$LIVE_DB" "SELECT '  Files:           ' || COUNT(*) FROM files;"
    sqlite3 "$LIVE_DB" "SELECT '  Embedding cache: ' || COUNT(*) FROM embedding_cache;"
    echo ""
    echo "Open connections to live DB:"
    lsof "$LIVE_DB" 2>/dev/null | grep -c python || echo "  0"
    echo ""
    echo "To restore, run without --dry-run."
    exit 0
fi

DB_ONLY=false
if [ "${1:-}" = "--db-only" ]; then
    DB_ONLY=true
fi

echo "========================================="
echo "  RESTORE TO PRE-TURBOQUANT STATE"
echo "========================================="
echo ""

# --- Step 1: Safety backup of current state ---

SAFETY_BACKUP="$HOME/.claude-memory/backups/memory-before-restore-$(date +%Y%m%d_%H%M%S).db"
echo "1. Safety backup of current DB..."
echo "   Using sqlite3 .backup (WAL-safe)..."
sqlite3 "$LIVE_DB" ".backup $SAFETY_BACKUP"

# Verify safety backup
SAFETY_INTEGRITY=$(sqlite3 "$SAFETY_BACKUP" "PRAGMA integrity_check;")
if [ "$SAFETY_INTEGRITY" != "ok" ]; then
    echo "   ERROR: Safety backup failed integrity check! Aborting."
    rm -f "$SAFETY_BACKUP"
    exit 1
fi
echo "   Saved: $SAFETY_BACKUP"

# --- Step 2: Restore database using sqlite3 .restore (WAL-safe) ---

echo ""
echo "2. Restoring database via sqlite3 .restore..."
echo "   This is safe with open connections (WAL mode)."

# Checkpoint current WAL first to minimize in-flight data
sqlite3 "$LIVE_DB" "PRAGMA wal_checkpoint(TRUNCATE);" > /dev/null 2>&1

# .restore reads backup file and writes it into the open database
# This goes through SQLite's normal write path — safe with WAL + concurrent readers
sqlite3 "$LIVE_DB" ".restore $BACKUP_DB"

# --- Step 3: Verify restored database ---

echo ""
echo "3. Verifying restored database..."

INTEGRITY=$(sqlite3 "$LIVE_DB" "PRAGMA integrity_check;")
CHUNKS=$(sqlite3 "$LIVE_DB" "SELECT COUNT(*) FROM chunks;")
EMBEDDINGS=$(sqlite3 "$LIVE_DB" "SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL;")
FILES=$(sqlite3 "$LIVE_DB" "SELECT COUNT(*) FROM files;")

PASS=true

if [ "$INTEGRITY" != "ok" ]; then
    echo "   FAIL: Integrity check: $INTEGRITY"
    PASS=false
fi

if [ "$CHUNKS" != "$EXPECTED_CHUNKS" ]; then
    echo "   FAIL: Expected $EXPECTED_CHUNKS chunks, got $CHUNKS"
    PASS=false
fi

if [ "$EMBEDDINGS" != "$EXPECTED_EMBEDDINGS" ]; then
    echo "   FAIL: Expected $EXPECTED_EMBEDDINGS embeddings, got $EMBEDDINGS"
    PASS=false
fi

if [ "$FILES" != "$EXPECTED_FILES" ]; then
    echo "   FAIL: Expected $EXPECTED_FILES files, got $FILES"
    PASS=false
fi

if [ "$PASS" = false ]; then
    echo ""
    echo "   VERIFICATION FAILED — reverting to safety backup..."
    sqlite3 "$LIVE_DB" ".restore $SAFETY_BACKUP"
    REVERT_CHECK=$(sqlite3 "$LIVE_DB" "PRAGMA integrity_check;")
    if [ "$REVERT_CHECK" = "ok" ]; then
        echo "   Reverted successfully."
    else
        echo "   CRITICAL: Revert also failed integrity check!"
        echo "   Manual recovery needed from: $SAFETY_BACKUP"
    fi
    exit 1
fi

echo "   Integrity: ok"
echo "   Chunks: $CHUNKS (expected $EXPECTED_CHUNKS)"
echo "   Embeddings: $EMBEDDINGS (expected $EXPECTED_EMBEDDINGS)"
echo "   Files: $FILES (expected $EXPECTED_FILES)"

# --- Step 4: Restore curated files (optional) ---

if [ "$DB_ONLY" = false ]; then
    echo ""
    if [ -f "$BACKUP_CURATED" ]; then
        echo "4. Restoring curated memory files..."
        # Extract over existing files (preserves any new files not in backup)
        tar xzf "$BACKUP_CURATED" -C "$MEMORY_DIR"
        RESTORED_COUNT=$(tar tzf "$BACKUP_CURATED" | grep -c '\.md$')
        echo "   Restored $RESTORED_COUNT markdown files."
    else
        echo "4. Curated backup not found — skipping. (DB restored successfully.)"
    fi
else
    echo ""
    echo "4. Skipping curated files (--db-only mode)."
fi

# --- Done ---

echo ""
echo "========================================="
echo "  RESTORE COMPLETE"
echo "========================================="
echo ""
echo "  Safety backup: $SAFETY_BACKUP"
echo ""
echo "  MCP servers with open connections will see the restored data"
echo "  on their next query (WAL mode handles this automatically)."
echo ""
echo "  To undo this restore:"
echo "    sqlite3 $LIVE_DB \".restore $SAFETY_BACKUP\""
