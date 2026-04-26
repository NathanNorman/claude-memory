# Proposal: Addon Reference Databases

## Problem

Skills and plugins want to ship domain knowledge (Spark SQL references, API docs, framework guides) that agents can search at runtime. Today, the only options are:

1. **Inline in SKILL.md** -- limited by context window, no semantic search
2. **Codebase indexing** -- designed for source code, not reference material, and pollutes the primary search index
3. **Merge into memory.db** -- contaminates personal memory search via RRF rank competition

There's no clean way to ship searchable, pre-built knowledge alongside a skill or plugin.

## Solution

Extend unified-memory to discover and search **addon `.db` files** shipped alongside skills and plugins. Each addon is a self-contained SQLite database with the same schema as `memory.db` (chunks, chunks_fts, embeddings). The existing `memory_search` tool gains the ability to route queries to addon databases via the `source` parameter.

## How It Works

### Discovery (mirrors Claude Code's skill resolution)

Two tiers, local wins on name collision:

| Tier | Location | Source name |
|------|----------|-------------|
| Plugin | `installed_plugins.json` installPath `/**/*.db` | `plugin-name:filename` |
| Local | `~/.claude/skills/**/*.db` | `filename` |

Discovery runs at server startup. The server reads `~/.claude/plugins/installed_plugins.json` to resolve plugin install paths (handles version bumps automatically), then globs local skills.

### Routing (no cross-contamination)

```
memory_search(source="spark-sql")    --> searches spark-sql.db only
memory_search(source="")             --> searches memory.db only (default, unchanged)
memory_search(source="conversations") --> searches memory.db, filtered to conversations
memory_search(source="curated")      --> searches memory.db, filtered to curated
```

Addon databases are completely isolated from the primary index. No RRF rank competition, no vector matrix mixing.

### Startup (async warmup)

All addon backends init eagerly in the existing warmup thread:
- SQLite connections: ~3ms per addon
- Vector index load (1,000 chunks): ~100ms per addon
- Embedding model: loaded once, shared across all backends
- Total for 5 addons: ~500ms, non-blocking

### Model compatibility

Each `.db` stores the embedding model name in its `meta` table. On discovery, the server checks that the addon's model matches the configured `MEMORY_EMBEDDING_MODEL`. Mismatched addons are skipped with a warning log, not silently degraded.

## Build Tooling

A new script `build-reference-db.py` that produces addon databases:

```bash
build-reference-db.py ./spark-sql-docs/ -o spark-sql.db
```

Input: directory of markdown/text files.
Output: SQLite database with chunks, FTS5 index, and embeddings.

Uses the same chunking (heading-aware markdown splitting) and embedding pipeline (bge-base-en-v1.5, 768-dim) as the existing system. Stamps model name and dimensions in the `meta` table.

## Skill Author Workflow

1. Gather reference material as markdown files
2. Run `build-reference-db.py ./my-docs/ -o my-skill.db`
3. Place `my-skill.db` next to `SKILL.md`
4. Add to SKILL.md: "Search reference material with `memory_search(source="my-skill")`"

## Size Estimates

| Content volume | Chunks | DB size |
|---------------|--------|---------|
| 1 book (~450KB text) | ~280 | ~1.5MB |
| 3-4 books | ~1,000 | ~3-5MB |
| 100 books (extreme) | ~28,000 | ~125-200MB |

Typical skills ship 3-5MB. Trivially committable to a repo.

## Non-Goals

- **Write path for addons** -- addon databases are read-only at runtime. The build script is the only writer.
- **Project-level discovery** -- no `.claude/skills/**/*.db` in repo roots. Only user-level local skills and plugins.
- **Cross-addon search** -- no `source="all"` that searches every addon. You name the source explicitly.
- **Hot reload** -- addon discovery happens at server startup. Adding a new addon requires restarting the MCP server (which happens naturally on session start).

## Changes Required

| Component | Change |
|-----------|--------|
| `unified_memory_server.py` | Addon discovery, source routing, expanded warmup |
| New: `scripts/build-reference-db.py` | Build script for producing addon `.db` files |
| New: `~/.claude-memory/addons.json` (optional) | Manual override registry for non-standard paths |

## Risks

- **Embedding model drift**: If the build script and server use different model versions, vector search quality degrades silently. Mitigated by the meta table check.
- **Stale addons**: Plugin updates may ship a new `.db` but the server caches the old backend until restart. Mitigated by the fact that MCP servers restart per-session.
- **Name collisions**: Two plugins could ship `references.db` and collide as `plugin-a:references` vs `plugin-b:references`. This is fine -- the namespace includes the plugin name.
