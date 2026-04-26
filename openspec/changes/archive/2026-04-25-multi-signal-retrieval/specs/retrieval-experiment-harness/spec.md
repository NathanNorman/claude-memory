## ADDED Requirements

### Requirement: Retrieval benchmark SHALL support N-way RRF with pluggable signals
The existing `locomo_retrieval_bench.py` SHALL be extended to support configurable retrieval signals: keyword, vector, entity overlap, temporal proximity, and scene expansion. Each signal SHALL be independently toggleable via CLI flags.

#### Scenario: Baseline comparison
- **WHEN** running with `--signals keyword,vector` (default)
- **THEN** results SHALL match the existing baseline (R@5=82.9%, R@10=91.6%)

#### Scenario: Adding temporal signal
- **WHEN** running with `--signals keyword,vector,temporal`
- **THEN** temporal proximity SHALL be included as a third RRF lane

#### Scenario: Full multi-signal
- **WHEN** running with `--signals keyword,vector,entity,temporal --scene-expand`
- **THEN** all four signals SHALL be merged via RRF and scene expansion applied post-merge

### Requirement: Benchmark SHALL report per-category breakdown
For each run, the benchmark SHALL report R@5 and R@10 broken down by LoCoMo category (Single-hop, Temporal, Temporal-inference, Open-domain) plus an overall score.

#### Scenario: Per-category output
- **WHEN** a benchmark run completes
- **THEN** output SHALL include a table with R@5, R@10, and count per category

### Requirement: Benchmark SHALL support A/B comparison mode
The benchmark SHALL accept a `--compare <baseline.json>` flag that loads a previous run's results and prints a side-by-side delta table.

#### Scenario: A/B comparison
- **WHEN** running with `--compare results_baseline.json`
- **THEN** output SHALL show baseline vs current R@5/R@10 per category with delta columns

### Requirement: Benchmark SHALL complete in under 15 minutes
A full 10-conversation retrieval-only benchmark run SHALL complete in under 15 minutes with zero API calls, enabling rapid iteration.

#### Scenario: Timing
- **WHEN** running all 1,536 questions with all signals enabled
- **THEN** total wall time SHALL be under 15 minutes on a standard laptop CPU
