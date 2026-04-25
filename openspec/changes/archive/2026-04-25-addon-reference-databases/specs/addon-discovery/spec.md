## ADDED Requirements

### Requirement: Server discovers addon databases from installed plugins
The server SHALL read `~/.claude/plugins/installed_plugins.json` on startup, extract the `installPath` for each installed plugin, and glob `**/*.db` under each path to find addon databases. The source name SHALL be `plugin-name:stem` where `plugin-name` is the key before `@` in the plugin identifier and `stem` is the `.db` filename without extension.

#### Scenario: Plugin ships a reference database
- **WHEN** `installed_plugins.json` contains plugin `toast-analytics-engineering@toast-marketplace` with installPath `/Users/x/.claude/plugins/cache/toast-marketplace/toast-analytics-engineering/0.8.2`
- **AND** that directory contains `references/analytics.db`
- **THEN** the server registers source `toast-analytics-engineering:analytics` pointing to that `.db` file

#### Scenario: Plugin has no database files
- **WHEN** a plugin's installPath contains no `.db` files
- **THEN** no addon source is registered for that plugin and no error is logged

#### Scenario: Plugin installPath does not exist
- **WHEN** `installed_plugins.json` references an installPath that does not exist on disk
- **THEN** the server skips that plugin with a warning log and continues discovery

### Requirement: Server discovers addon databases from local skills
The server SHALL glob `~/.claude/skills/**/*.db` to find addon databases in local skill directories. The source name SHALL be the `.db` filename without extension.

#### Scenario: Local skill ships a reference database
- **WHEN** `~/.claude/skills/spark-sql/spark-sql.db` exists
- **THEN** the server registers source `spark-sql` pointing to that `.db` file

#### Scenario: No local skills have databases
- **WHEN** no `.db` files exist under `~/.claude/skills/`
- **THEN** no local addon sources are registered and no error is logged

### Requirement: Local skills take precedence over plugins on name collision
When a local skill's source name (filename stem) matches a plugin's source name (the portion after the colon), the local skill's database SHALL be used and the plugin's database SHALL be ignored.

#### Scenario: Local skill shadows plugin database
- **WHEN** local skill has `~/.claude/skills/analytics/analytics.db` (source: `analytics`)
- **AND** plugin has `references/analytics.db` (source: `toast-analytics-engineering:analytics`)
- **THEN** source `analytics` resolves to the local skill's database
- **AND** source `toast-analytics-engineering:analytics` is still available via its fully-qualified name

### Requirement: Model compatibility check on discovery
The server SHALL read the `embedding_model` key from each addon database's `meta` table and compare it against the configured `MEMORY_EMBEDDING_MODEL`. Addon databases with mismatched models SHALL be skipped with a warning log.

#### Scenario: Addon model matches server model
- **WHEN** addon DB's `meta` table has `embedding_model = bge-base-en-v1.5`
- **AND** server's `MEMORY_EMBEDDING_MODEL` is `bge-base-en-v1.5`
- **THEN** the addon is registered and searchable

#### Scenario: Addon model does not match
- **WHEN** addon DB's `meta` table has `embedding_model = all-MiniLM-L6-v2`
- **AND** server's `MEMORY_EMBEDDING_MODEL` is `bge-base-en-v1.5`
- **THEN** the addon is skipped
- **AND** a warning is logged: "Skipping addon <name>: model mismatch (all-MiniLM-L6-v2 != bge-base-en-v1.5)"

#### Scenario: Addon has no meta table
- **WHEN** addon DB does not have a `meta` table or no `embedding_model` key
- **THEN** the addon is skipped with a warning log

### Requirement: Addon backends initialize in warmup thread
All discovered addon backends (FlatSearchBackend + VectorSearchBackend pairs) SHALL be initialized in the existing warmup thread, not on the main startup path.

#### Scenario: Server starts with 3 addon databases
- **WHEN** discovery finds 3 addon `.db` files
- **THEN** the MCP server is ready to accept requests immediately
- **AND** the warmup thread initializes all 3 addon backend pairs asynchronously
- **AND** queries to addon sources return results once warmup completes

#### Scenario: Query arrives before warmup completes
- **WHEN** a `memory_search` query with an addon source arrives before the warmup thread has initialized that addon
- **THEN** the search returns an empty result set (graceful degradation)

### Requirement: get_status reports addon backends
The `get_status` tool SHALL include a list of discovered addon sources with their chunk counts and status.

#### Scenario: Status with addons loaded
- **WHEN** `get_status` is called after warmup completes with 2 addons
- **THEN** the response includes an `addons` key listing each source name, chunk count, and vector count
