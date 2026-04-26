## Context

TKE Wave 3 added 6 capabilities across `unified_memory_server.py`, `codebase-index.py`, `index_worker.py`, `job_queue.py`, and `scip_parser.py`. The existing test suite covers only build parsing (26 tests) and cross-repo resolution (14 tests). All new features — igraph traversal, community detection, SCIP parsing, Matryoshka embeddings, LLM labeling, and pipeline timing — have zero test coverage.

## Goals / Non-Goals

**Goals:**
- Integration tests for every Wave 3 feature using real SQLite databases
- Shared test fixture that creates a realistic edge/symbol/chunk graph
- Tests runnable without external deps (no GPT-4o-mini, no SCIP binaries, no sentence-transformers)
- Tests follow existing pattern: `python3 scripts/test_*.py` with `unittest.TestCase`
- Each test file independently runnable

**Non-Goals:**
- End-to-end MCP server tests (requires FastMCP startup, too heavy)
- Performance benchmarks (separate effort)
- Testing the actual embedding model quality (Matryoshka quality is validated upstream)

## Decisions

### 1. In-memory SQLite for all tests

**Decision**: Use `:memory:` SQLite databases with the same schema as production.

**Why**: Fast (no disk I/O), isolated (no test pollution), disposable. The production DB is SQLite anyway, so there's no impedance mismatch.

**Approach**: A shared `create_test_db()` fixture populates edges, symbols, chunks, and codebase_meta tables with a synthetic graph (3 connected components, ~50 nodes, ~80 edges).

### 2. Mock LLM calls, not real API

**Decision**: Mock `openai.OpenAI` for labeling tests.

**Why**: Tests must work offline, not cost money, and run in <1s. Label caching and node identification logic is testable without real API calls.

### 3. Mock sentence-transformers for embedding tests

**Decision**: Use deterministic fake embeddings (e.g., hash-based vectors) instead of loading the real model.

**Why**: Loading nomic-embed-text-v1.5 takes ~5s and 500MB. Tests need to verify truncation math and dimension matching, not embedding quality.

### 4. One test file per capability

**Decision**: 6 test files, each independently runnable.

**Why**: Matches existing pattern (`test_build_parser.py`, `test_cross_repo.py`). Allows running just the relevant tests during development.

### 5. Test synthetic graph design

**Decision**: 3 clusters of files connected by call/import edges, with a few bridge edges between clusters.

**Why**: This gives community detection clear expected outputs (3 communities), graph traversal testable paths, and bridge detection known results.

## Risks / Trade-offs

- **[Mock fidelity]** Mocking embeddings means we won't catch model-specific bugs. Acceptable since Matryoshka math (truncate + renorm) is deterministic.
- **[Schema drift]** If table schemas change, test fixtures need updating. Mitigated by having fixtures call the same `_ensure_dep_tables()` function as production.
- **[No MCP tool tests]** The MCP tools are thin wrappers calling internal functions. Testing the internal functions gives ~95% coverage without MCP startup overhead.
