## ADDED Requirements

### Requirement: Judge scores session summary on 6 adapted dimensions

The system SHALL evaluate a session summary against the source conversation transcript using 6 scoring dimensions adapted from Memento's rubric for software engineering context:

| Dimension | Points | Evaluates |
|---|---|---|
| Decisions & rationale | 0-3 | Architecture choices, why X over Y, trade-offs |
| Key identifiers, configs, errors | 0-2 | File paths, env vars, config values, error messages |
| Approaches tried & rejected | 0-2 | What was attempted, what failed, why it was abandoned |
| File paths & code references | 0-1 | Specific files touched, functions modified, PRs referenced |
| Correctness | 0-1 | Only confirmed findings; no hallucinated or speculative content |
| Structure | 0-1 | Leads with outcomes/decisions before process narrative |

Total scale: 0-10. The judge SHALL return a numeric score and specific, actionable feedback identifying what is missing or incorrect.

#### Scenario: Judge scores a high-quality summary
- **WHEN** the summary contains all decisions, rationale, key identifiers, rejected approaches, file paths, and is correctly structured
- **THEN** the judge SHALL return a score of 8 or higher and feedback confirming completeness

#### Scenario: Judge scores a summary missing key decisions
- **WHEN** the summary omits an architecture decision present in the transcript (e.g., "chose RRF over weighted merge")
- **THEN** the judge SHALL return a score below 8 and feedback specifically naming the missing decision

#### Scenario: Judge scores a summary with hallucinated content
- **WHEN** the summary includes facts not present in the source transcript
- **THEN** the judge SHALL deduct from the Correctness dimension and flag the specific hallucinated content in feedback

### Requirement: Judge invocation uses claude CLI

The system SHALL invoke the judge via `subprocess.run(['claude', '-p', prompt, '--model', MODEL, '--tools', '', '--no-session-persistence'])` where MODEL is configured via `MEMORY_SUMMARY_MODEL` environment variable (default: `haiku`).

#### Scenario: Judge call succeeds
- **WHEN** the claude CLI returns exit code 0 with stdout containing a score and feedback
- **THEN** the system SHALL parse the score (float) and feedback (string) from the response

#### Scenario: Judge call fails
- **WHEN** the claude CLI returns a non-zero exit code or times out
- **THEN** the system SHALL retry with exponential backoff up to 3 times, then fall back to skipping refinement (score=0, empty feedback)

### Requirement: Judge prompt includes full transcript context

The system SHALL pass the judge both the session transcript excerpt (up to 30K chars) and the summary being evaluated, formatted as clearly delimited sections.

#### Scenario: Long transcript truncation
- **WHEN** the session transcript exceeds 30,000 characters
- **THEN** the system SHALL truncate to the first 30,000 characters with a `...(truncated)` marker before passing to the judge
