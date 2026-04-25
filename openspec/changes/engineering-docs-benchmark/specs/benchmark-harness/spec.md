## ADDED Requirements

### Requirement: Harness script
A benchmark harness at `benchmarks/engineering_docs_bench.py` MUST accept a dataset JSON path and run the full pipeline: ingest docs → search per question → generate answer → judge answer → report scores.

### Requirement: CLI interface
The harness MUST support these arguments:
- `data` (positional): path to the QA dataset JSON
- `--system`: which system to benchmark (`claude-memory`, `llm-memory`, or `both`)
- `--skip-ingest`: skip ingestion (reuse previously ingested data)
- `--limit N`: limit to first N questions
- `--save PATH`: save per-question results to JSON
- `--llm MODEL`: generator model (default: claude-sonnet-4-6-20250514)
- `--judge MODEL`: judge model (default: claude-sonnet-4-6-20250514)
- `--backend`: LLM backend (default: adapter)

### Requirement: Scoring methodology
The harness MUST compute F1 (token overlap with stemming) and J-score (LLM-as-judge binary CORRECT/WRONG) per question, using the same `score_qa` and `judge_answer_llm` functions from `production_locomo_bench.py`.

### Requirement: Per-category reporting
Results MUST be broken down by question category (single-hop, multi-hop, procedural, entity-relationship) with F1 and J-score per category, plus overall scores.

### Requirement: Comparison output
When `--system both` is used, the harness MUST produce a side-by-side comparison table showing each system's scores per category.

### Requirement: Stats output
When `--save` is used, the harness MUST also write a `_stats.json` file with system metadata, overall scores, per-category scores, elapsed time, and configuration details.
