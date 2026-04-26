## ADDED Requirements

### Requirement: Boundary score cache table in SQLite

The `boundary_score_cache` table SHALL be created in `memory.db` during schema initialization in `db.ts`. The table SHALL store LLM-assigned boundary scores keyed by exchange-pair content hash and scorer version.

#### Scenario: Table schema
- **WHEN** `openDb()` is called
- **THEN** a `boundary_score_cache` table SHALL exist with columns: `pair_hash TEXT NOT NULL`, `scorer_version TEXT NOT NULL`, `score REAL NOT NULL`, `created_at INTEGER NOT NULL`, and a composite primary key on `(pair_hash, scorer_version)`

#### Scenario: Table creation is idempotent
- **WHEN** `openDb()` is called on an existing database that already has the `boundary_score_cache` table
- **THEN** no error SHALL occur and the existing data SHALL be preserved

### Requirement: Cache key is content hash of exchange pair

The cache key SHALL be the SHA-256 hash of the concatenation of both exchanges' user and assistant messages in the pair (exchange[i] and exchange[i+1]). This ensures cache invalidation when either exchange changes.

#### Scenario: Same exchange pair content produces same cache key
- **WHEN** two different conversation files contain an identical exchange pair (same user and assistant message text)
- **THEN** the cache key SHALL be identical and the cached score SHALL be reused

#### Scenario: Modified exchange invalidates cache
- **WHEN** an exchange's assistant message changes between reindexes (e.g., conversation file updated)
- **THEN** the cache key SHALL differ and a new LLM score SHALL be computed

### Requirement: Cache lookup before LLM call

Before making an LLM call for a window of exchanges, the scorer SHALL check the cache for each boundary pair in the window. If all pairs in the window are cached, the LLM call SHALL be skipped entirely.

#### Scenario: Fully cached window
- **WHEN** all 15 boundary pairs in a 16-exchange window have cached scores for the current scorer version
- **THEN** no LLM API call SHALL be made for that window and cached scores SHALL be returned

#### Scenario: Partially cached window
- **WHEN** some boundary pairs in a window are cached and others are not
- **THEN** the full window SHALL be sent to the LLM (cache is per-window-call granularity for context quality) and all resulting scores SHALL be written to the cache

### Requirement: Cache write after successful LLM scoring

After a successful LLM call returns scores for a window, the scorer SHALL write each individual boundary pair score to the cache with the current scorer version.

#### Scenario: Scores persisted after LLM call
- **WHEN** a window of 16 exchanges is scored by the LLM returning 15 scores
- **THEN** 15 rows SHALL be inserted or replaced in `boundary_score_cache`, one per boundary pair

### Requirement: Cache eviction during reindex cleanup

Stale cache entries (for exchange pairs no longer present in any indexed conversation) SHALL be evicted during the reindex cleanup phase to prevent unbounded cache growth.

#### Scenario: Eviction of orphaned cache entries
- **WHEN** a conversation file is deleted and its exchange pairs no longer appear in any indexed file
- **THEN** the corresponding `boundary_score_cache` entries SHALL be removed during the next reindex

### Requirement: Scorer version invalidation

The `scorer_version` column SHALL encode the prompt version and model identifier. When the scoring prompt or model changes, a new version string SHALL cause all existing cache entries to be bypassed (new version = cache miss).

#### Scenario: Prompt version change
- **WHEN** the scorer version changes from `"llm-v1"` to `"llm-v2"`
- **THEN** all existing cache entries with version `"llm-v1"` SHALL be cache misses and new scores SHALL be computed
