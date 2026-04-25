## Why

During the DP-6599 PR review, a reviewer found 5 cases where we built new code when existing implementations already existed in the codebase. The root cause: we design solutions without first searching for what the codebase already has. Memory prompts alone don't prevent this. We need an automated system that indexes codebases semantically and surfaces existing implementations before new code is written, enforced via a Claude Code hook.

## What Changes

- Add a `codebase-index` CLI tool that walks a repo, chunks source files (using existing `code_chunker.py`), generates embeddings, and stores them in the unified-memory SQLite DB with a `source=codebase:<name>` tag
- Add `codebase-update` command for incremental re-indexing (only changed files since last index)
- Add `codebase-search` MCP tool that queries indexed codebases with semantic search
- Add a Claude Code `PreToolUse:Write` hook that blocks new source file creation until a codebase search has been performed and results acknowledged
- Extend `memory_search` to include codebase results when the `source` filter is empty (default hybrid search)

## Capabilities

### New Capabilities
- `codebase-indexing`: CLI to index/update codebases into unified-memory's SQLite DB with embeddings
- `codebase-search`: MCP tool for semantic search over indexed codebases
- `pre-write-hook`: Claude Code hook that enforces codebase search before new source file creation

### Modified Capabilities
- `memory-search`: Extend existing `memory_search` to include codebase chunks in hybrid results

## Impact

- `src/unified_memory_server.py`: New MCP tool, extended search
- `src/code_chunker.py`: Already exists, may need Java/Kotlin/shell support added
- New CLI script: `scripts/codebase-index.py`
- New hook script: `hooks/pre-write-codebase-check.py` (installed into `~/.claude/hooks/`)
- SQLite schema: New `codebase_chunks` table (or extend existing `chunks` table with source tagging)
