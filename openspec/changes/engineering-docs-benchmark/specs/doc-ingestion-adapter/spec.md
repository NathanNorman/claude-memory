## ADDED Requirements

### Requirement: claude-memory ingestion
The adapter MUST ingest engineering docs into claude-memory using the existing production pipeline: markdown → dialog-aware chunking (1600-char max) → bge-base-en-v1.5 embeddings → TurboQuant 4-bit quantization → FTS5 + vector index. Use a temporary SQLite database per benchmark run.

### Requirement: llm-memory ingestion
The adapter MUST ingest engineering docs into llm-memory using the Cognee pipeline: markdown → cognee.add() → custom_cognify() with the EngineeringModel ontology (Service, CLI, KnowledgeBase, ApiStandard, CodingStandard). Run ingestion in a subprocess to isolate Cognee's async state.

### Requirement: llm-memory search
The adapter MUST search llm-memory using Cognee's CHUNKS search type (vector similarity over pgvector), extracting the `text` field from results and truncating to 800 chars per chunk. Return top-10 chunks as context for the generator.

### Requirement: claude-memory search
The adapter MUST search claude-memory using the production hybrid pipeline: FTS5 keyword + quantized vector + RRF merge, with session dedup and smart_truncate. Return top-10 chunks as context.

### Requirement: Same source documents
Both systems MUST ingest the exact same set of markdown files from `benchmarks/data/engineering_docs/`. No preprocessing differences.

### Requirement: Infrastructure configuration
llm-memory adapter MUST connect to postgres on port 15432 and neo4j on port 7687 (matching the Docker setup from the LoCoMo benchmark). Environment variables MUST be set programmatically, not requiring manual configuration.

### Requirement: Auth handling
The llm-memory adapter MUST obtain Bedrock JWT tokens via `otelHeadersHelper` and inject them into Cognee's embedding and LLM config, following the pattern established in `llm_memory_locomo_bench.py`.
