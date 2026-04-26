## Capability: codebase-search

### Purpose
MCP tool for semantic search over indexed codebases, and extension of existing `memory_search` to include codebase results.

### Requirements

1. **New MCP tool `codebase_search`**:
   ```
   codebase_search(query: str, codebase: str = "", maxResults: int = 10)
   ```
   - Runs hybrid search (FTS5 keyword + vector cosine similarity + RRF merge)
   - Filters to `file_path LIKE 'codebase:%'`
   - If `codebase` provided, further filters to `file_path LIKE 'codebase:<name>/%'`
   - Returns: file path, chunk title, snippet, relevance score, line numbers

2. **Extend `memory_search`**:
   - When `source` filter is empty (default), include codebase chunks in hybrid results
   - When `source=codebase`, return only codebase chunks
   - When `source=conversations` or `source=curated`, exclude codebase chunks (existing behavior)

3. **Result format**:
   Each result includes:
   - `path`: full path (e.g., `codebase:toast-analytics/toast-analytics-extractor/src/test/.../ManifestFinder.java`)
   - `title`: chunk title (e.g., `class ManifestFinder`)
   - `snippet`: first 300 chars of chunk content
   - `score`: RRF-merged relevance score
   - `startLine` / `endLine`: line numbers in the original file

### Acceptance Criteria
- `codebase_search("manifest discovery")` returns ManifestFinder.java for toast-analytics
- `memory_search("sync schema dump")` returns both the codebase chunk (syncSchemaDumpFromS3.sh) and any conversation memories about schema dumps
- `codebase_search("JUnit Suite", codebase="toast-analytics")` returns QueryRegistryValidationSuite.java
