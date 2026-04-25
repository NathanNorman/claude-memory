# Codebase Embedding

## Purpose

Index functions, classes, and files from configured git repositories as searchable vectors in unified-memory. Enables semantic search across code ("how did I handle X before?") alongside conversation history.

## Requirements

### R1: Source Configuration

- Configured via `~/.claude-memory/codebase-sources.json`:
  ```json
  {
    "repos": [
      {"path": "~/toast-analytics", "include": ["**/*.py", "**/*.java"], "exclude": ["**/test/**"]},
      {"path": "~/claude-memory", "include": ["**/*.py", "**/*.ts"]}
    ]
  }
  ```
- Each repo entry specifies: path, include globs, exclude globs (optional)
- If config file doesn't exist, codebase indexing is skipped (not an error)

### R2: Python AST-Aware Chunking

- For `.py` files: extract top-level functions and classes using Python's `ast` module
- Each function/class becomes one chunk with:
  - `title`: `def function_name` or `class ClassName`
  - `content`: full source text including docstring and decorators
  - `file_path`: relative path within repo (e.g., `codebase/toast-analytics/src/converter.py`)
  - `start_line` / `end_line`: line numbers in the source file
- Functions shorter than 3 lines are skipped (trivial getters/setters)
- Nested functions are included as part of their parent, not as separate chunks

### R3: File-Level Chunking for Other Languages

- For non-Python files (`.ts`, `.java`, `.sql`, `.md`, etc.): chunk at file level
- If file exceeds 200 lines, split at blank-line boundaries into chunks of ~100-150 lines
- Each chunk gets `title`: filename, `content`: the text block

### R4: Metadata and Deduplication

- Chunks are stored in the same `chunks` table with `file_path` prefixed by `codebase/<repo-name>/`
- Content hash-based deduplication: if a function's content hash matches an existing chunk, skip re-embedding
- On reindex, detect deleted functions/files and remove their chunks from the index

### R5: Git Integration

- Before indexing, run `git ls-files` to get the file list (respects .gitignore)
- For incremental updates: `git diff --name-only HEAD~1` to find changed files since last index
- Store the indexed commit SHA in `files` table for each repo to enable incremental diffing

### R6: Search Integration

- Codebase chunks appear in normal `memory_search` results alongside conversation and curated memory
- Results include the `file_path` (with `codebase/` prefix) so callers can distinguish code from conversation
- No special weighting — code and conversation compete on relevance via RRF as usual
