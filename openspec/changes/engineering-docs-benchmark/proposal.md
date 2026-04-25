## Why

LoCoMo and LongMemEval test conversational memory — casual chats between friends, personal assistant sessions. llm-memory (Toast's Cognee-based knowledge engine) scored 34.2% on LoCoMo, but it was designed for engineering documentation, not conversation. No existing benchmark tests what llm-memory actually does: retrieve structured technical knowledge from runbooks, API docs, and engineering Q&A. We need a benchmark that tests retrieval on the data type these systems were built for, so we can compare graph-based vs. flat retrieval on a level playing field.

## What Changes

- Create a new benchmark dataset: 50-100 QA pairs derived from real engineering documentation (runbooks, API standards, Slack Q&A)
- Build a benchmark harness (`engineering_docs_bench.py`) that ingests docs into both claude-memory and llm-memory, then scores retrieval + generation quality
- Produce a fair comparison on llm-memory's home turf: structured technical content with entity relationships, multi-hop lookups, and procedural knowledge
- Extend the explainer with results from this domain-specific benchmark

## Capabilities

### New Capabilities
- `benchmark-dataset`: Curated QA dataset from engineering docs with question categories (single-hop, multi-hop, procedural, entity-relationship)
- `benchmark-harness`: Benchmark script that runs both systems against the dataset with identical scoring (F1 + J-score)
- `doc-ingestion-adapter`: Adapters to ingest the same docs into both claude-memory (markdown → chunks) and llm-memory (markdown → Cognee pipeline)

### Modified Capabilities

## Impact

- New files in `benchmarks/`: dataset JSON, harness script, adapter modules
- Requires llm-memory infrastructure (postgres + neo4j) to be running for the Cognee side
- Requires Bedrock access (VPN + otelHeadersHelper) for llm-memory's LLM-based entity extraction
- Results will be added to the explainer at `~/explainers/longmemeval-benchmark-results.html`
