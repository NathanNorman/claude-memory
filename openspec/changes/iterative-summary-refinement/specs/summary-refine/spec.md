## ADDED Requirements

### Requirement: Refiner produces improved summary from judge feedback

The system SHALL generate an improved session summary by providing the original summary, the judge's specific feedback, and the source transcript to an LLM call. The refined summary SHALL address all issues identified in the judge feedback while preserving correct content from the original.

#### Scenario: Refiner adds missing decision
- **WHEN** the judge feedback states "Missing: decision to use RRF over weighted merge because keyword results were suppressed below threshold"
- **THEN** the refined summary SHALL include that decision with its rationale

#### Scenario: Refiner removes hallucinated content
- **WHEN** the judge feedback flags "Summary mentions file X.py but transcript only references Y.py"
- **THEN** the refined summary SHALL not contain the hallucinated file reference

#### Scenario: Refiner preserves correct content
- **WHEN** the judge feedback only flags missing items (no correctness issues)
- **THEN** the refined summary SHALL retain all existing correct content and add the missing items

### Requirement: Refinement loop runs up to max iterations

The system SHALL run a summarize-judge-refine loop:
1. Generate initial summary
2. Judge scores summary
3. If score >= threshold, stop
4. If score < threshold and iterations < max, refine and go to step 2
5. If iterations >= max, stop with current best summary

The maximum iterations SHALL be configured via `MEMORY_SUMMARY_MAX_ITER` (default: 2). The score threshold SHALL be configured via `MEMORY_SUMMARY_THRESHOLD` (default: 8).

#### Scenario: Summary passes on first judge
- **WHEN** the initial summary scores 8.5/10 with threshold=8
- **THEN** the system SHALL use the initial summary without refinement (1 judge call, 0 refine calls)

#### Scenario: Summary refined once then passes
- **WHEN** the initial summary scores 6/10, refined summary scores 9/10, threshold=8, max_iter=2
- **THEN** the system SHALL use the refined summary (2 judge calls, 1 refine call)

#### Scenario: Summary never reaches threshold
- **WHEN** the summary scores below threshold after all iterations are exhausted
- **THEN** the system SHALL use the last refined summary as the final summary

#### Scenario: Max iterations set to 1
- **WHEN** `MEMORY_SUMMARY_MAX_ITER=1`
- **THEN** the system SHALL judge once and never refine, regardless of score

### Requirement: Refiner uses same model as judge

The system SHALL use the model specified in `MEMORY_SUMMARY_MODEL` for both the initial summary generation, judge calls, and refine calls.

#### Scenario: Model override
- **WHEN** `MEMORY_SUMMARY_MODEL=sonnet` is set
- **THEN** all LLM calls (summarize, judge, refine) SHALL use `--model sonnet`
