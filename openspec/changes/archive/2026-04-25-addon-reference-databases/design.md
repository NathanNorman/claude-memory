## Context

unified-memory is a Python MCP server (`src/unified_memory_server.py`) that provides hybrid search (FTS5 keyword + vector cosine similarity + RRF merge) over a single SQLite database at `~/.claude-memory/index/memory.db`. It indexes personal memory files, conversation archives, and codebases.

Skills and plugins need to ship searchable reference material (documentation, guides, API references) without contaminating the personal search index. The `source` parameter on `memory_search` already supports filtering (`curated`, `conversations`, `codebase`), but filtering happens post-search — both backends still query the full index, causing RRF rank competition.

The server currently initializes one `FlatSearchBackend` and one `VectorSearchBackend`, both pointed at the same DB file. The warmup thread pre-loads the vector index and embedding model in the background.

Skills live in `~/.claude/skills/<name>/` (local) and plugins install to `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/` (paths resolved via `~/.claude/plugins/installed_plugins.json`).

## Goals / Non-Goals

**Goals:**
- Addon `.db` files are discoverable from local skills and installed plugins
- Source-based routing directs queries to the correct database before search (not post-filter)
- Zero cross-contamination between personal memory and addon knowledge bases
- Addon backends initialize asynchronously without blocking MCP server startup
- A build script produces addon `.db` files from directories of reference material
- Model compatibility is enforced — mismatched addons are rejected

**Non-Goals:**
- Write path for addon databases (read-only at runtime)
- Project-level `.db` discovery (`.claude/skills/` within repos)
- Cross-addon search (`source="all"` spanning every addon)
- Hot-reload of addons mid-session
- Supporting different embedding models per addon

## Decisions

### 1. Source routing before search, not post-filter

**Decision:** When `source` matches an addon name, the query is routed to that addon's backend pair exclusively. The primary `memory.db` backends are never touched.

**Alternatives considered:**
- *Post-filter (current pattern)*: Search everything, filter results by source tag. Rejected because RRF is rank-based — addon chunks compete with personal memory for rank positions, degrading both result sets.
- *Merged DB with prefix namespacing*: Copy addon chunks into `memory.db` under `codebase:addon-name/` prefix. Rejected because it couples addon lifecycle to the primary DB (uninstall requires surgical deletes, rebuilds lose addon data).

**Rationale:** Separate databases give clean isolation with no scoring interference. Each backend pair operates on its own vector matrix and FTS5 index.

### 2. Discovery via installed_plugins.json + skills glob

**Decision:** On startup, the server:
1. Reads `~/.claude/plugins/installed_plugins.json` and globs `**/*.db` under each `installPath`
2. Globs `~/.claude/skills/**/*.db` for local skills
3. Builds a source name map: plugins use `plugin-name:stem`, local skills use `stem`
4. Local names shadow plugin names on collision

**Alternatives considered:**
- *Registry file (`addons.json`)*: Explicit registration of DB paths. Rejected as primary mechanism because it requires a setup step. Kept as optional fallback for non-standard locations.
- *Blind glob of all plugin cache*: `~/.claude/plugins/cache/**/*.db`. Rejected because it could pick up stale version directories or uninstalled plugins. `installed_plugins.json` is the source of truth.

**Rationale:** Mirrors Claude Code's own skill resolution model. `installed_plugins.json` handles version bumps (path changes on update) and scoping. Local skills having higher precedence matches the project > local > plugin hierarchy.

### 3. One backend pair per addon, eager init in warmup thread

**Decision:** Each discovered addon gets its own `FlatSearchBackend` + `VectorSearchBackend` pair, initialized eagerly in the existing warmup thread alongside the primary backends.

**Alternatives considered:**
- *Lazy init on first query*: Defers cost but adds ~100ms latency to the first query per addon. Rejected because total eager cost for 5 addons is ~500ms, well within the warmup thread budget.
- *Single shared backend with DB switching*: Reuse one backend, swap DB connections per query. Rejected because it invalidates the in-memory vector matrix on every switch — effectively lazy-loading every time.

**Rationale:** Eagerly loading ~1,000 chunks per addon costs ~100ms each. The warmup thread already exists and runs asynchronously. The embedding model is loaded once and shared across all backends.

### 4. Model compatibility enforced via meta table

**Decision:** The build script stamps `embedding_model` and `embedding_dims` in the addon DB's `meta` table. On discovery, the server compares these against `MEMORY_EMBEDDING_MODEL` and skips mismatched addons with a warning log.

**Rationale:** Silent model mismatch causes vector search to return nonsense results with high confidence scores — the worst kind of failure. Better to fail loud and skip the addon entirely.

### 5. Build script reuses existing server components

**Decision:** `build-reference-db.py` imports `FlatSearchBackend._chunk_markdown()` for chunking and uses the same `sentence-transformers` model for embedding. The output DB uses the identical schema as `memory.db` (chunks, chunks_fts, files, meta tables).

**Alternatives considered:**
- *Custom schema for addons*: Lighter tables without conversation-specific fields. Rejected because reusing the exact schema means the server code doesn't need any schema-aware branching — same `FlatSearchBackend` and `VectorSearchBackend` classes work on both primary and addon DBs.

**Rationale:** Code reuse and schema compatibility. The unused columns (like `files.summary`) are just NULL in addon DBs — zero cost.

### 6. Empty source queries primary DB only

**Decision:** `memory_search(source="")` routes exclusively to `memory.db`. Addon databases are never included in default searches.

**Rationale:** The default search path is the most common one. It must never be degraded by addon installation. Addons are opt-in per query — you name the source you want.

## Risks / Trade-offs

**[Stale discovery on long sessions]** Addon discovery runs at startup. If a plugin is installed or updated mid-session, the server won't see the new `.db` until restart. → MCP servers restart per-session, so this is a non-issue in practice.

**[File locking on shared DB reads]** Multiple SQLite connections (server + build script) could conflict. → Addon DBs are read-only at runtime. Build script only runs offline. WAL mode handles concurrent readers.

**[Memory footprint scales with addon count]** Each addon's vector index lives in memory as a numpy matrix. 5 addons × 1,000 chunks × 768 dims × 4 bytes = ~15MB total. → Acceptable. Would only matter at 50+ large addons.

**[Plugin path instability]** `installed_plugins.json` paths include version numbers. If the JSON is stale or mid-update, discovery could fail. → Fail-open: skip unreadable paths with a warning, don't crash the server.

## Open Questions

- Should `get_status` report addon backends? Leaning yes — include a list of discovered addons with chunk counts.
- Should `codebase_search` also support addon routing, or is `memory_search` the only entry point? Leaning toward `memory_search` only for simplicity.
