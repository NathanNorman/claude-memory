## Tasks

### Task 1: Collect source documents
Gather engineering markdown files from llm-memory's source configs into `benchmarks/data/engineering_docs/`. Read `~/llm-memory/sources_devx.json`, `sources_ghes_ops.json`, and `sources_csi_idp.json` to identify local doc paths. Copy 15-25 representative docs (runbooks, API standards, setup guides) into the benchmark data directory.

**Acceptance:** `benchmarks/data/engineering_docs/` contains 15-25 markdown files covering services, CLIs, setup procedures, and API standards.

### Task 2: Generate QA pairs
Write a script `benchmarks/scripts/generate_engineering_qa.py` that reads the source docs and uses an LLM to generate candidate QA pairs. For each doc, generate 3-5 questions across the four categories (single-hop, multi-hop, procedural, entity-relationship). Output raw candidates to a temp file for curation.

**Acceptance:** Script runs and produces 80-120 candidate QA pairs with question, answer, category, source_docs, and difficulty fields.

### Task 3: Curate QA dataset
Review generated QA pairs. Remove duplicates, fix incorrect answers, verify answers against source docs, ensure category balance (10+ per category), and adjust difficulty ratings. Write final dataset to `benchmarks/data/engineering_docs_qa.json`.

**Acceptance:** Dataset has 50-100 QA pairs, all answers verified against source docs, 10+ per category.

### Task 4: Build the benchmark harness
Create `benchmarks/engineering_docs_bench.py` following the pattern from `llm_memory_locomo_bench.py` and `memsearch_locomo_bench.py`. Include:
- claude-memory adapter: temp SQLite DB, production chunking + hybrid search
- llm-memory adapter: subprocess ingestion with EngineeringModel, CHUNKS search
- Shared scoring: F1 + J-score, per-category breakdown, comparison table
- CLI with --system, --skip-ingest, --limit, --save flags

**Acceptance:** `python3 benchmarks/engineering_docs_bench.py benchmarks/data/engineering_docs_qa.json --system claude-memory --limit 5` runs and produces scores.

### Task 5: Run claude-memory benchmark
Ingest docs and run the full dataset against claude-memory. Save results to `benchmarks/results_engineering_docs_claude_memory.json`.

**Acceptance:** Full results file with per-question predictions, F1, J-score, and stats file.

### Task 6: Run llm-memory benchmark
Ensure Docker infra is running (postgres:15432, neo4j:7687). Ingest docs via Cognee with EngineeringModel ontology. Run full dataset. Save results to `benchmarks/results_engineering_docs_llm_memory.json`.

**Acceptance:** Full results file with per-question predictions, F1, J-score, and stats file.

### Task 7: Compare results and update explainer
Produce side-by-side comparison. Add an "Engineering Docs Benchmark" section to the explainer at `~/explainers/longmemeval-benchmark-results.html` with the results, per-category breakdown, and analysis of where graph-based retrieval helps vs. flat retrieval.

**Acceptance:** Explainer updated and published with engineering docs benchmark results.
