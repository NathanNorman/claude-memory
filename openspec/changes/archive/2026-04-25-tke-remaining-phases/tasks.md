## Change 1: igraph Sidecar

- [ ] 1.1 Add `igraph` to the graphiti-venv dependencies (`pip install igraph`)
- [ ] 1.2 Add `GraphSidecar` class in `src/unified_memory_server.py` that loads edges from SQLite into an igraph directed graph, with node IDs as file paths and edge attributes `edge_type`/`metadata`
- [ ] 1.3 Add codebase-scoped loading: when a codebase is specified, only load edges for that codebase; when unspecified, load all
- [ ] 1.4 Add memory-bounded loading with configurable `max_edges` (default 5M), ordered by `updated_at DESC`, logging a warning when limit is hit
- [ ] 1.5 Add igraph BFS-based `traverse()` method on `GraphSidecar` with direction (upstream/downstream), edge_type filtering, max_depth, and max_results
- [ ] 1.6 Wire `graph_traverse` MCP tool to use `GraphSidecar.traverse()` when igraph is loaded, falling back to recursive CTEs transparently
- [ ] 1.7 Add SIGHUP handler to trigger atomic graph rebuild (build new graph, swap reference)
- [ ] 1.8 Add staleness detection: track edge count at load time, trigger background rebuild when `graph_traverse` detects >10% edge count drift
- [ ] 1.9 Add startup logging: node count, edge count, load time
- [ ] 1.10 Test: verify igraph traversal returns same results as CTE traversal on the existing test DB

## Change 2: Community Detection

- [ ] 2.1 Add `communities` table to `_ensure_dep_tables()`: columns `codebase`, `file_path`, `community_id` (INTEGER), `updated_at`
- [ ] 2.2 Add `compute_communities()` function using igraph Louvain on call+import edges for a codebase, storing results in `communities` table
- [ ] 2.3 Add `--communities` CLI flag to `codebase-index.py` that triggers `compute_communities()` after indexing
- [ ] 2.4 Add `community_search` MCP tool with parameters: `file_path`, `codebase`, `list_all` (bool), `show_bridges` (bool)
- [ ] 2.5 Implement file-based lookup: return all files in the same community, sorted by degree
- [ ] 2.6 Implement `list_all` mode: return all community IDs with file counts and top-3 representative files
- [ ] 2.7 Implement `show_bridges` mode: return edges that cross community boundaries
- [ ] 2.8 Add staleness detection: recompute if edge count changed >10% since last computation
- [ ] 2.9 Test: verify community assignments on a synthetic graph with clear clusters

## Change 3: SCIP Indexer Integration

- [ ] 3.1 Create `src/scip_parser.py` with protobuf parser for `.scip` files (using `scip` protobuf schema)
- [ ] 3.2 Add SCIP language detection: auto-select indexer based on build file presence
- [ ] 3.3 Add `run_scip_indexer()` function that executes the appropriate scip-* CLI tool and returns the `.scip` file path
- [ ] 3.4 Add `parse_scip_output()` function that extracts call edges, symbol definitions, and cross-references from the protobuf
- [ ] 3.5 Add edge merging logic: SCIP edges replace tree-sitter edges for the same call site (higher confidence), SCIP-only edges are added with `metadata` noting `source=scip`
- [ ] 3.6 Add `--scip` CLI flag to `codebase-index.py` that runs SCIP indexing as a second pass after tree-sitter
- [ ] 3.7 Add graceful fallback: if SCIP binary not found or build fails, log warning and continue with tree-sitter only
- [ ] 3.8 Test: verify SCIP edge parsing on a sample `.scip` protobuf file

## Change 4: Matryoshka Embeddings

- [ ] 4.1 Update `CODEBASE_EMBEDDING_MODEL` in `codebase-index.py` from `nomic-ai/CodeRankEmbed` to `nomic-ai/nomic-embed-text-v1.5`
- [ ] 4.2 Add `--dims` CLI flag (valid values: 64, 128, 256, 384, 512, 768; default 256) to control truncation
- [ ] 4.3 Add dimension truncation + L2 renormalization after embedding generation in `embed_and_store_batch()`
- [ ] 4.4 Store the configured dimension in `meta` table (key `codebase_embedding_dims`)
- [ ] 4.5 Update Python MCP server to auto-detect stored dimension from BLOB size and truncate query embeddings to match
- [ ] 4.6 Add storage reduction reporting in indexing output
- [ ] 4.7 Update `CODEBASE_QUERY_PREFIX` for nomic model (uses `search_query:` and `search_document:` prefixes)
- [ ] 4.8 Test: verify cosine similarity scores are comparable between 768d and 256d on a sample query set

## Change 5: LLM Semantic Labeling

- [ ] 5.1 Add `identify_high_value_nodes()` function in `scripts/codebase-index.py`: query symbols with >N incoming edges, public API annotations, and module entry points
- [ ] 5.2 Add `label_nodes_batch()` function that sends function signature + docstring to GPT-4o-mini and stores the 1-sentence label in symbols.metadata JSON
- [ ] 5.3 Add label caching: skip nodes whose content hash hasn't changed since last labeling
- [ ] 5.4 Add `--label` CLI flag to `codebase-index.py` that triggers identification + labeling
- [ ] 5.5 Add configurable delay between API calls (default 100ms) for rate limiting
- [ ] 5.6 Update `symbol_search` MCP tool to include `label` field in results when available
- [ ] 5.7 Update `codebase_search` MCP tool to include `label` field for matching symbols
- [ ] 5.8 Add `metadata` column to symbols table if not present (JSON text, nullable)
- [ ] 5.9 Test: verify label storage and retrieval roundtrip with a mock API response

## Change 6: Webhook Latency Optimization

- [ ] 6.1 Add `timing` JSON column to job queue table with per-stage timestamps
- [ ] 6.2 Instrument each pipeline stage in `index_worker.py`: webhook_received, job_enqueued, worker_picked_up, git_fetch_complete, diff_computed, chunks_generated, embeddings_computed, db_writes_complete
- [ ] 6.3 Add summary log line on job completion with total latency and slowest stage
- [ ] 6.4 Pre-load embedding model at worker startup (eliminate cold-start penalty)
- [ ] 6.5 Add small-diff optimization: for diffs <10 files, embed only changed file chunks
- [ ] 6.6 Add pipeline health to `get_status` MCP tool: `jobs_last_hour`, `avg_latency_ms`, `p95_latency_ms`, `queue_depth`
- [ ] 6.7 Add WARN logging when p95 latency exceeds 1s target
- [ ] 6.8 Test: verify timing instrumentation records all stages for a mock job
