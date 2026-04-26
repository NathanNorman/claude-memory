## Shared Test Fixtures

- [x] 1.1 Create `scripts/test_fixtures.py` with `create_test_db()` that returns an in-memory SQLite connection with edges, symbols, chunks, codebase_meta, communities, community_meta, and index_jobs tables populated with a synthetic 3-cluster graph (~50 nodes, ~80 edges, ~30 symbols)
- [x] 1.2 Add helper `mock_embedding_model()` that returns a fake model whose `.encode()` produces deterministic hash-based vectors of configurable dimension

## igraph Sidecar Tests

- [x] 2.1 Create `scripts/test_igraph_sidecar.py` with `TestGraphSidecarLoad` — test load from populated DB, empty DB, codebase-scoped loading
- [x] 2.2 Add `TestGraphSidecarTraverse` — downstream BFS, upstream BFS, edge type filtering, max_depth cap, max_results cap, include_paths mode
- [x] 2.3 Add `TestGraphSidecarStaleness` — no drift (False), >10% drift (True), zero initial edges
- [x] 2.4 Add `TestGraphSidecarMemoryCap` — set MAX_EDGES=50, verify graph has ≤50 edges
- [x] 2.5 Add `TestGraphSidecarRebuild` — successful rebuild adds new edges, failed rebuild preserves old graph

## Community Detection Tests

- [x] 3.1 Create `scripts/test_community_detection.py` with `TestComputeCommunities` — 3-cluster graph produces 3 communities, results stored correctly, empty edges returns error
- [x] 3.2 Add `TestCommunityStaleness` — never computed (True), within threshold (False), beyond threshold (True)
- [x] 3.3 Add `TestCommunitySearch` — file_path lookup returns members sorted by degree, list_all returns all communities with representatives, show_bridges returns cross-community edges

## SCIP Parser Tests

- [x] 4.1 Create `scripts/test_scip_parser.py` with `TestDetectScipLanguages` — Java from build.gradle.kts, TS from tsconfig.json, multi-language, no build files
- [x] 4.2 Add `TestParseScipJson` — definition+reference produces edge, same-file reference excluded, method symbol classified as 'calls'
- [x] 4.3 Add `TestMergeScipEdges` — SCIP replaces existing edge, SCIP-only added, existing preserved when no match

## Matryoshka Embedding Tests

- [x] 5.1 Create `scripts/test_matryoshka.py` with `TestTruncationMath` — 768→256 truncation, L2 norm ≈1.0 after renorm, no-op when dims match
- [x] 5.2 Add `TestEmbedAndStoreBatch` — stored BLOB size matches truncated dims, doc_prefix prepended to model input (using mock model)
- [x] 5.3 Add `TestDimensionAutoDetect` — detect from meta table, detect from BLOB size fallback
- [x] 5.4 Add `TestQueryDimensionMatching` — query vector truncated from 768→256 when stored_dims=256

## LLM Labeling Tests

- [x] 6.1 Create `scripts/test_llm_labeling.py` with `TestIdentifyHighValueNodes` — nodes with many edges selected, entry points selected, no duplicates
- [x] 6.2 Add `TestLabelCaching` — already-labeled with same hash skipped, changed hash re-labeled, unlabeled gets labeled (mock OpenAI client)
- [x] 6.3 Add `TestLabelSurfacing` — symbol_search includes label when present, omits when absent

## Webhook Latency Tests

- [x] 7.1 Create `scripts/test_webhook_latency.py` with `TestPipelineTimer` — single stage timing, multi-stage, to_json valid, summary identifies slowest
- [x] 7.2 Add `TestJobQueueTiming` — mark_done stores timing JSON, mark_done without timing stores NULL
- [x] 7.3 Add `TestPipelineHealth` — empty table returns zeroes, compute avg/p95 from 5 jobs, queue_depth counts pending, old jobs excluded
