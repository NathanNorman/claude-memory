## 1. Addon Discovery

- [x] 1.1 Add `discover_addon_dbs()` function to `unified_memory_server.py` that reads `~/.claude/plugins/installed_plugins.json`, extracts installPaths, globs `**/*.db` under each, and builds source name map (`plugin-name:stem`)
- [x] 1.2 Add local skill discovery: glob `~/.claude/skills/**/*.db`, source name is filename stem. Local names shadow plugin names on collision.
- [x] 1.3 Add model compatibility check: read `meta` table from each discovered `.db`, compare `embedding_model` against `MEMORY_EMBEDDING_MODEL`, skip mismatched addons with warning log
- [x] 1.4 Store discovered addons in a module-level dict: `addon_backends: dict[str, dict]` mapping source name to `{'flat': FlatSearchBackend, 'vector': VectorSearchBackend, 'db_path': Path}`

## 2. Backend Initialization

- [x] 2.1 Add `init_addon_backends()` function that iterates discovered DB paths and creates `FlatSearchBackend` + `VectorSearchBackend` pairs for each
- [x] 2.2 Expand the existing warmup thread in `run()` to call `discover_addon_dbs()` then `init_addon_backends()`, including `_ensure_index()` and `_ensure_model()` for each addon's vector backend
- [x] 2.3 Add thread-safety: use a threading.Event or simple flag so `memory_search` knows when addon warmup is complete (return empty results for addon sources before warmup finishes)

## 3. Source Routing

- [x] 3.1 Modify `memory_search()` to detect addon source names: if `source` matches a key in `addon_backends`, route to that addon's backend pair exclusively (skip primary `memory.db`)
- [x] 3.2 Preserve existing source filter behavior: `source=""` queries primary only, `source="curated"/"conversations"/"codebase"` queries primary with post-filtering as before
- [x] 3.3 Return error response when `source` is not empty, not a known filter value, and not a registered addon name
- [x] 3.4 Add `source` field to addon search results so callers can identify where results came from

## 4. Status Reporting

- [x] 4.1 Update `get_status()` to include an `addons` key listing each registered addon source name, chunk count, vector count, and DB path

## 5. Build Script

- [x] 5.1 Create `scripts/build-reference-db.py` with CLI args: input directory path, `-o` output DB path, `--name` optional source name
- [x] 5.2 Implement file discovery: recursively find `.md`, `.txt`, `.rst` files, skip other types with info log
- [x] 5.3 Implement chunking: reuse heading-aware markdown splitting for `.md` files, paragraph-boundary splitting for plain text
- [x] 5.4 Create SQLite DB with required schema: `chunks`, `chunks_fts`, `files`, `meta` tables
- [x] 5.5 Generate embeddings using `sentence-transformers` (bge-base-en-v1.5), store as quantized BLOBs if quantization params available, else float32
- [x] 5.6 Stamp `meta` table with `embedding_model` and `embedding_dims`
- [x] 5.7 Populate `quantization_meta` table if quantized embeddings are produced

## 6. Testing

- [x] 6.1 Create a small test fixture: directory with 3 markdown files, build into a `.db` using the build script
- [x] 6.2 Test discovery: place fixture `.db` in a mock skills directory, verify `discover_addon_dbs()` finds it with correct source name
- [x] 6.3 Test source routing: search addon source returns addon results only, search empty source returns primary results only
- [x] 6.4 Test model mismatch: create a `.db` with wrong model in meta, verify it's skipped
- [x] 6.5 Test end-to-end: build reference DB from test fixtures, place in skills dir, search via `memory_search(source=...)`, verify relevant results returned

## 7. Documentation

- [x] 7.1 Update CLAUDE.md with addon database section: discovery mechanism, build script usage, source parameter values
- [x] 7.2 Add usage example to build script docstring showing full workflow (gather files → build → place → search)
