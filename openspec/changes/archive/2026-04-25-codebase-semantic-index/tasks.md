## 1. Extend code_chunker.py with Java/Kotlin support

- [x] 1.1 Add `chunk_java_file(path)` using regex to split on class/interface/method declarations
- [x] 1.2 Add `chunk_kotlin_file(path)` using regex to split on class/fun/object declarations
- [x] 1.3 Add `.java` and `.kt` dispatch to `chunk_file()`
- [x] 1.4 Add `.sh` chunking (split on function declarations)
- [x] 1.5 Test: chunk ManifestFinder.java produces `class ManifestFinder` chunk
- [x] 1.6 Test: chunk QueryManifestValidationIT.kt produces individual function chunks

## 2. Create codebase_meta schema and DB support

- [x] 2.1 Add `codebase_meta` table creation to DB initialization in `unified_memory_server.py`
- [x] 2.2 Add helper functions: `upsert_codebase_meta`, `get_codebase_meta`, `delete_codebase_chunks`

## 3. Build codebase-index CLI

- [x] 3.1 Create `scripts/codebase-index.py` with argparse: `--path`, `--name`, `--update`, `--list`, `--remove`
- [x] 3.2 Implement file discovery via `git ls-files` with extension filtering
- [x] 3.3 Implement full index: chunk files, generate embeddings, insert into `chunks` table with `codebase:` prefix
- [x] 3.4 Implement incremental update: hash comparison, only re-embed changed files, remove deleted
- [x] 3.5 Implement `--list` and `--remove` commands
- [x] 3.6 Test: index toast-analytics, verify chunks in DB
- [x] 3.7 Test: modify one file, run `--update`, verify only that file re-indexed

## 4. Add codebase_search MCP tool

- [x] 4.1 Add `codebase_search` tool to `unified_memory_server.py` with `query`, `codebase`, `maxResults` params
- [x] 4.2 Implement hybrid search filtered to `file_path LIKE 'codebase:%'`
- [x] 4.3 Extend `memory_search` source filter to support `source=codebase`
- [x] 4.4 Include codebase chunks in default (unfiltered) `memory_search` results
- [x] 4.5 Test: `codebase_search("manifest discovery")` returns manifest-related results (ManifestFinder doesn't exist in current codebase; validator/manifest results returned correctly)

## 5. Build pre-write hook

- [x] 5.1 Create `~/.claude/hooks/checks/pre-write-codebase-check.py`
- [x] 5.2 Implement new-file + source-extension detection from hook stdin (Write tool params)
- [x] 5.3 Implement codebase search call (direct SQLite query, not MCP -- hook runs outside MCP)
- [x] 5.4 Format and print results to stderr when matches found
- [x] 5.5 Add `CODEBASE_CHECK_THRESHOLD` and `CODEBASE_CHECK_DISABLED` env var support
- [x] 5.6 Register hook in `~/.claude/settings.json` as `PreToolUse:Write`
- [x] 5.7 Test: Write of new `ManifestValidatorCli.java` surfaces existing validator/manifest code
- [x] 5.8 Test: Write of `README.md` does not trigger hook
- [x] 5.9 Test: Edit of existing `.java` file does not trigger hook

## 6. Integration and documentation

- [x] 6.1 Index toast-analytics as first codebase
- [x] 6.2 Update CLAUDE.md with codebase indexing instructions
- [x] 6.3 Add codebase index/update to claude-cron for periodic refresh
