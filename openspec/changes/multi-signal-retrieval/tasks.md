## 1. Experiment Harness

- [ ] 1.1 Extend `locomo_retrieval_bench.py` with `--signals` CLI flag accepting comma-separated list (keyword,vector,entity,temporal)
- [ ] 1.2 Implement N-way `merge_rrf_multi()` accepting a list of ranked lists
- [ ] 1.3 Add `--compare <baseline.json>` flag for A/B delta table output
- [ ] 1.4 Add `--save-retrieval <path.json>` to persist per-question retrieval results for comparison
- [ ] 1.5 Run baseline with `--signals keyword,vector --save-retrieval` to capture baseline results

## 2. Temporal Retrieval (Round 1 experiments)

- [ ] 2.1 Add `event_date` column to chunks table in benchmark's `create_fresh_db()`
- [ ] 2.2 Populate `event_date` from LoCoMo session timestamps during benchmark indexing
- [ ] 2.3 Implement `dateparser`-based relative date resolution for chunk content ("yesterday" → absolute date)
- [ ] 2.4 Implement `parse_temporal_query()` to extract date range from query text
- [ ] 2.5 Implement temporal proximity scoring function (Gaussian decay, σ=7 days)
- [ ] 2.6 Wire temporal signal as RRF lane in benchmark; run experiment: `--signals keyword,vector,temporal`
- [ ] 2.7 Run alternative experiment: temporal as boost multiplier on base RRF scores
- [ ] 2.8 Run alternative experiment: temporal as pre-filter (SQL WHERE event_date BETWEEN) then semantic rank
- [ ] 2.9 Compare all temporal variants vs baseline; pick winner

## 3. Entity Retrieval (Round 2 experiments)

- [ ] 3.1 Install `spacy` + `en_core_web_sm` in graphiti-venv
- [ ] 3.2 Add `chunk_entities` table to benchmark's `create_fresh_db()`
- [ ] 3.3 Implement spaCy NER extraction during benchmark indexing (PERSON, DATE, ORG, GPE, LOC)
- [ ] 3.4 Implement entity overlap scoring: extract query entities, look up matching chunks, score by overlap ratio
- [ ] 3.5 Wire entity signal as RRF lane in benchmark; run experiment: `--signals keyword,vector,entity`
- [ ] 3.6 Run experiment with temporal winner + entity: `--signals keyword,vector,temporal_winner,entity`
- [ ] 3.7 Run alternative experiment: entity as boost multiplier
- [ ] 3.8 Compare all entity variants; pick winner

## 4. Scene Clustering (Round 3 experiments)

- [ ] 4.1 Add `scenes` and `chunk_scenes` tables to benchmark's `create_fresh_db()`
- [ ] 4.2 Implement nearest-centroid clustering assignment during benchmark indexing (τ=0.70)
- [ ] 4.3 Implement scene expansion post-RRF: look up scene neighbors, score against query, add top-5
- [ ] 4.4 Run experiment: baseline + scene expansion (no new RRF lane, just expansion)
- [ ] 4.5 Run threshold sweep: τ=0.60, 0.65, 0.70, 0.75, 0.80
- [ ] 4.6 Run experiment: best temporal + best entity + best scene config
- [ ] 4.7 Compare combined config vs baseline; confirm cumulative improvement

## 5. Score-Gated Query Expansion (Round 4)

- [ ] 5.1 Implement score-gate heuristic: if max RRF score < threshold, generate keyword-combo variant of query
- [ ] 5.2 Implement simple query expansion: extract nouns/entities from query, form keyword combo, re-search
- [ ] 5.3 Run experiment with score-gated expansion on top of best combined config
- [ ] 5.4 Measure: what % of queries trigger expansion? Does it help or hurt?

## 6. Full E2E Validation

- [ ] 6.1 Run full LoCoMo E2E benchmark with winning retrieval config (Sonnet generator + Sonnet judge, 1,540 QA pairs)
- [ ] 6.2 Run memsearch comparison with same generator/judge for updated head-to-head
- [ ] 6.3 Compare J-scores: baseline 87.3% vs new config vs memsearch 84.3%
- [ ] 6.4 Per-category analysis: confirm temporal and multi-hop improvements carry through to E2E

## 7. Production Port

- [ ] 7.1 Add new SQLite tables to `db.ts` schema (chunk_entities, scenes, chunk_scenes, event_date column)
- [ ] 7.2 Add schema migration for existing databases (CREATE TABLE IF NOT EXISTS, ALTER TABLE ADD COLUMN)
- [ ] 7.3 Add spaCy NER + dateparser extraction to Python server's `memory_write` path
- [ ] 7.4 Add scene assignment to Python server's `memory_write` path
- [ ] 7.5 Implement N-way RRF merge in Python server's `memory_search`
- [ ] 7.6 Add temporal and entity retrieval signals to Python server's `memory_search`
- [ ] 7.7 Add scene expansion post-processing to Python server's `memory_search`
- [ ] 7.8 Write backfill migration script: process all existing chunks for entities, dates, scenes
- [ ] 7.9 Run backfill on production database (~15,000 chunks)
- [ ] 7.10 Install spacy + en_core_web_sm + dateparser in graphiti-venv

## 8. Documentation & Explainer

- [ ] 8.1 Update CLAUDE.md with new retrieval architecture description
- [ ] 8.2 Update explainer with new benchmark results and multi-signal retrieval description
- [ ] 8.3 Publish updated explainer
