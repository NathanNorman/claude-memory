## Context

We benchmarked llm-memory (Cognee + Neo4j + pgvector) on LoCoMo and it scored 34.2% — but LoCoMo tests casual conversation, not the engineering documentation llm-memory was built for. To produce a fair comparison, we need a benchmark using the same data type llm-memory ingests in production: runbooks, API standards, setup guides, and Slack Q&A.

llm-memory already has ingested data in its Cognee instance from Toast engineering docs (sources_devx.json, sources_ghes_ops.json, etc.). We need to build a QA dataset from those same docs, then test both systems on it.

claude-memory's existing benchmark infrastructure (F1 + J-score scoring, adapter-based LLM calls, per-category breakdowns) can be reused. The new work is the dataset and the ingestion adapters.

## Goals / Non-Goals

**Goals:**
- Create a QA dataset (50-100 pairs) from real engineering documentation that exercises entity-relationship, multi-hop, and procedural question types
- Benchmark both claude-memory and llm-memory on this dataset using identical scoring methodology
- Determine whether graph-based retrieval adds value for structured technical content
- Produce results suitable for adding to the benchmark explainer

**Non-Goals:**
- Building a general-purpose engineering QA benchmark (this is specific to our two systems)
- Testing more than two systems (can add memsearch later if useful)
- Achieving publication-quality dataset curation (pragmatic, not academic)
- Modifying either system's retrieval pipeline to optimize for this benchmark

## Decisions

### 1. Dataset source: llm-memory's own ingested docs

**Decision:** Use the markdown files that llm-memory was designed to ingest — Toast engineering runbooks, API standards, devx guides. These are already defined in `~/llm-memory/sources_devx.json` and similar config files.

**Why:** This gives llm-memory maximum home-field advantage. If it can't beat flat retrieval on its own training data, the graph approach genuinely doesn't help.

**Alternative considered:** Synthetic engineering docs. Rejected — real docs have the messiness (inconsistent formatting, cross-references, version drift) that matters for retrieval quality.

### 2. Question categories

**Decision:** Four categories, designed to test different retrieval strengths:

| Category | Tests | Example | Graph advantage? |
|---|---|---|---|
| **Single-hop** | Direct fact lookup | "What port does service X run on?" | No — flat retrieval handles this fine |
| **Multi-hop** | Connecting facts across documents | "Which team owns the service that handles auth token refresh?" | Yes — graph traversal connects entities |
| **Procedural** | Step-by-step knowledge | "What are the steps to set up braid for local dev?" | Neutral — both should retrieve the runbook |
| **Entity-relationship** | Structured relationships | "What services depend on the config-service?" | Yes — graph stores relationships explicitly |

**Why:** Multi-hop and entity-relationship are where graph retrieval should theoretically excel. Including single-hop and procedural gives a balanced picture.

### 3. QA pair generation: LLM-assisted with human curation

**Decision:** Use an LLM to generate candidate QA pairs from the source docs, then manually review and curate to ensure correctness and non-trivial difficulty.

**Why:** Hand-writing 100 QA pairs is slow. LLM generation + human filtering is 5x faster and produces consistent formatting. The human review catches hallucinated answers and trivially easy questions.

### 4. Scoring methodology: Same as LoCoMo benchmarks

**Decision:** Reuse the existing F1 + J-score pipeline with Sonnet as both generator and judge, via the localhost:3456 adapter. Same `call_claude`, same `judge_answer_llm`, same per-category reporting.

**Why:** Direct comparability with all our existing benchmark results. No new scoring infrastructure to build.

### 5. Ingestion: Per-system adapters, same source docs

**Decision:** Both systems ingest the same markdown files. claude-memory uses its standard chunking pipeline. llm-memory uses its Cognee pipeline (add → cognify with EngineeringModel ontology, not our custom ConversationModel).

**Why:** llm-memory should use its own ontology — that's the whole point. The EngineeringModel (Service, CLI, KnowledgeBase, ApiStandard, CodingStandard) is purpose-built for this data.

## Risks / Trade-offs

**[Dataset bias]** → QA pairs generated from docs llm-memory has already ingested may inadvertently test what the system indexed well. Mitigation: include questions that require cross-document reasoning, not just in-chunk lookups.

**[Small dataset]** → 50-100 QA pairs is small. Statistical significance is limited. Mitigation: report per-category breakdowns and confidence intervals. This is a directional signal, not a definitive verdict.

**[Infrastructure dependency]** → llm-memory requires postgres + neo4j + VPN + Bedrock auth. If any of these are down, the benchmark can't run. Mitigation: `--skip-ingest` flag to reuse previously ingested data, same pattern as the LoCoMo benchmark.

**[Ingestion cost]** → llm-memory's cognify pipeline makes LLM calls for every chunk during ingestion. With many docs, this could be expensive and slow. Mitigation: ingest once, benchmark many times. The `--skip-ingest` pattern handles this.

**[llm-memory's EngineeringModel ontology may not cover all doc types]** → Some docs may not fit the CLI/Service/KnowledgeBase schema. Mitigation: select source docs that align with the existing ontology, since that's what the system is designed for.
