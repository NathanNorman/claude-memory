## Capability: codebase-indexing

### Purpose
CLI tool to index source code from a repository into unified-memory's SQLite DB with embeddings for semantic search.

### Requirements

1. **Index a codebase**: `python3 scripts/codebase-index.py --path /path/to/repo --name my-repo`
   - Walks repo using `git ls-files` (respects .gitignore)
   - Filters to source extensions: `.py`, `.java`, `.kt`, `.scala`, `.sh`, `.sql`, `.js`, `.ts`, `.tf`
   - Chunks files via `code_chunker.py`
   - Generates 384-dim embeddings via sentence-transformers
   - Stores in `chunks` table with `file_path=codebase:<name>/<relative-path>`

2. **Incremental update**: `python3 scripts/codebase-index.py --path /path/to/repo --name my-repo --update`
   - Computes SHA256 of each source file
   - Compares against stored hashes in `codebase_meta` table
   - Only re-chunks and re-embeds files that changed
   - Removes chunks for deleted files

3. **List indexed codebases**: `python3 scripts/codebase-index.py --list`
   - Shows name, path, file count, chunk count, last indexed timestamp

4. **Remove a codebase**: `python3 scripts/codebase-index.py --remove --name my-repo`
   - Deletes all chunks and metadata for the named codebase

5. **Java/Kotlin chunking**: Extend `code_chunker.py` with regex-based chunking for `.java` and `.kt` files
   - Split on class/interface/function declarations
   - Each chunk gets a descriptive title (e.g., `class ManifestFinder`, `fun testAllQueryManifestValidators`)

### Schema

`codebase_meta` table:
```sql
CREATE TABLE IF NOT EXISTS codebase_meta (
    codebase TEXT NOT NULL,
    file_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    indexed_at TEXT NOT NULL,
    PRIMARY KEY (codebase, file_path)
);
```

Chunks stored in existing `chunks` table with `file_path` prefixed by `codebase:<name>/`.

### Acceptance Criteria
- First index of toast-analytics (~500 source files) completes in under 15 minutes
- Incremental update with no changes completes in under 10 seconds
- `memory_search "ManifestFinder"` returns the ManifestFinder.java chunk
- `memory_search "sync schema dump from S3"` returns syncSchemaDumpFromS3.sh
