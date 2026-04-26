## 1. Model Constants and Configuration

- [ ] 1.1 Add `CODEBASE_EMBEDDING_MODEL = 'nomic-ai/CodeRankEmbed'` and `CODEBASE_QUERY_PREFIX = 'Represent this query for searching relevant code: '` constants to `scripts/codebase-index.py`
- [ ] 1.2 Add CodeRankEmbed to `MODEL_DIMS` dict (768d) and `MODEL_PREFIXES` dict in `scripts/codebase-index.py`
- [ ] 1.3 Change `DEFAULT_MODEL` in `codebase-index.py` from `bge-base-en-v1.5` to `nomic-ai/CodeRankEmbed`
- [ ] 1.4 Add matching constants (`CODEBASE_EMBEDDING_MODEL`, `CODEBASE_QUERY_PREFIX`) to `src/unified_memory_server.py` VectorSearchBackend

## 2. Structural Context Prefix for Code Chunks

- [ ] 2.1 In `codebase-index.py`, add a function `build_structural_prefix(file_path: str, title: str) -> str` that constructs `"{rel_path} | {title}\n"` from chunk metadata
- [ ] 2.2 In `embed_and_store_batch()`, prepend structural prefix to each chunk's text before embedding (embedding input only, not stored content)

## 3. Codebase Indexer Model Switch

- [ ] 3.1 Update `codebase-index.py` `main()` to use `CODEBASE_EMBEDDING_MODEL` as the default model for codebase indexing
- [ ] 3.2 Add `codebase_embedding_model` meta key write after successful indexing
- [ ] 3.3 Add model change detection at indexer startup: read `codebase_embedding_model` from meta table, if different from configured model, purge all codebase chunks and force full reindex

## 4. MCP Server Dual-Model Search

- [ ] 4.1 Add a `_codebase_model` lazy-loaded attribute to VectorSearchBackend for CodeRankEmbed (separate from the existing `_model` for bge-base)
- [ ] 4.2 Add `_ensure_codebase_model()` method that lazy-loads `nomic-ai/CodeRankEmbed` on first call
- [ ] 4.3 Update `codebase_search()` tool to encode query with CodeRankEmbed + query prefix instead of bge-base
- [ ] 4.4 Update `memory_search()` to apply query prefix when `source='codebase'` and use CodeRankEmbed for the vector search portion

## 5. Meta Table and Migration

- [ ] 5.1 Add `codebase_embedding_model` key to meta table reads/writes in VectorSearchBackend `_check_model_version()`
- [ ] 5.2 Verify that changing the codebase model in meta table does NOT trigger reindex of memory/conversation chunks

## 6. Verification

- [ ] 6.1 Syntax-check both modified Python files: `python3 -c "import py_compile; py_compile.compile('...', doraise=True)"`
- [ ] 6.2 Run a test codebase index with `--path` on a small repo to confirm CodeRankEmbed loads and embeds correctly
- [ ] 6.3 Test `codebase_search` query to verify the query prefix is applied and results return
- [ ] 6.4 Verify `memory_search` without source filter still uses bge-base (no regression)
