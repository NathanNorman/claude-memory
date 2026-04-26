## Architecture

Extends the existing unified-memory system (Python MCP server + SQLite FTS5 + sentence-transformers embeddings) with codebase-aware indexing and a Claude Code enforcement hook.

### Data Model

Reuse the existing `chunks` table with source tagging rather than a separate table. Each codebase chunk gets:
- `file_path`: e.g., `codebase:toast-analytics/toast-analytics-extractor/src/test/java/.../ManifestFinder.java`
- `title`: e.g., `class ManifestFinder` or `def syncSchemaDumpFromS3`
- `content`: the chunk text (function body, class body, or file segment)
- `embedding`: 384-dim vector (existing sentence-transformers pipeline)
- `project`: the codebase name (e.g., `toast-analytics`)
- `date`: last indexed timestamp

FTS5 and vector search already work on the `chunks` table. By tagging codebase chunks with a `codebase:` prefix on `file_path`, existing `memory_search` automatically includes them in hybrid results. The `source` filter can be extended to support `source=codebase` filtering.

### Codebase Indexer (`scripts/codebase-index.py`)

CLI that:
1. Accepts `--path <repo-root>` and `--name <codebase-name>`
2. Walks the repo respecting `.gitignore` (use `git ls-files` for file list)
3. Filters to source files: `.py`, `.java`, `.kt`, `.scala`, `.sh`, `.sql`, `.js`, `.ts`, `.tf`
4. Chunks each file via `code_chunker.py` (extend with Java/Kotlin regex-based chunking)
5. Generates embeddings via the existing sentence-transformers model
6. Upserts into the `chunks` table with `file_path=codebase:<name>/<relative-path>`
7. Tracks indexed file hashes in a metadata table for incremental updates

### Code Chunker Extensions

`code_chunker.py` currently handles:
- Python: AST-based (functions, classes)
- Everything else: file-level with size-based splitting

Add regex-based chunking for:
- Java/Kotlin: Split on `class`/`fun`/`def`/`interface` declarations
- Shell: Split on function declarations
- SQL: File-level (already handled)

### MCP Tool: `codebase_search`

New tool on the unified-memory MCP server:
```
codebase_search(query: str, codebase: str = "", maxResults: int = 10)
```
Runs hybrid search (FTS5 + vector) filtered to `file_path LIKE 'codebase:%'`. If `codebase` specified, further filters to that codebase name.

### Pre-Write Hook

A `PreToolUse:Write` hook in Claude Code settings that:
1. Checks if the target file is a new source file (doesn't exist, has a code extension)
2. Extracts the file name and any available context (from the Write tool's content parameter first 200 chars)
3. Calls `memory_search` with the file description + codebase filter
4. If similar code found: prints results and asks for acknowledgment
5. If no matches or VALIDATION_RESULTS_DIR not set: passes through silently

The hook is a Python script at `~/.claude/hooks/checks/pre-write-codebase-check.py`.

## Key Decisions

- **Reuse `chunks` table** over separate table: keeps hybrid search unified, no query-time joins
- **`git ls-files`** for file discovery: respects .gitignore, handles submodules
- **Incremental indexing** via file content hashes: only re-embed changed files
- **Regex-based chunking for Java/Kotlin** over full AST parsing: simpler, good enough for semantic search (class/function boundaries)
- **Hook is advisory, not blocking**: prints results but doesn't prevent the Write. Blocking would require Claude to respond to hook output, which is already how hooks work. The hook output surfaces in the conversation, making it hard to ignore.

## Constraints

- Embedding generation is local (sentence-transformers, no API cost) but slow (~50ms per chunk). A large codebase (10k files) could take 5-10 minutes for first index.
- SQLite WAL mode handles concurrent reads (search during index) but writes are serialized.
- The hook adds ~1-2s latency to new file creation (one memory_search call).
