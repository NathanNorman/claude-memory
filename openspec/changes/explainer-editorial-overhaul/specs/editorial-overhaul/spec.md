## ADDED Requirements

### Requirement: Thesis statement in opening
The document SHALL state a single thesis within the first 100 words of prose content, before any benchmark numbers are presented.

#### Scenario: Reader can identify the argument
- **WHEN** a reader finishes the first paragraph
- **THEN** they can state in one sentence what the document argues

### Requirement: Concluding section that extends
The document SHALL end with a concluding section that synthesizes findings and states what remains open, rather than restating scores.

#### Scenario: Conclusion adds new insight
- **WHEN** a reader reaches the conclusion
- **THEN** they encounter forward-looking analysis (benchmark saturation, next challenges, implications for memory system evaluation) not previously stated in the document

### Requirement: Chapter transition sentences
Each chapter SHALL open with a bridging sentence that explains why this chapter follows the previous one.

#### Scenario: Chapter 2 transition from Chapter 1
- **WHEN** the reader finishes Chapter 1 (LongMemEval)
- **THEN** a transition sentence explains why LoCoMo tests a different dimension before Chapter 2 begins

#### Scenario: Chapter 5 transition from Chapter 4
- **WHEN** the reader finishes Chapter 4 (Cross-Encoder)
- **THEN** a transition sentence explains why methodology matters after four chapters of results

### Requirement: Explanatory paragraphs after data tables
Each per-type or per-category breakdown table SHALL be followed by at least one paragraph explaining why the results look the way they do.

#### Scenario: Temporal reasoning explanation
- **WHEN** the reader sees temporal reasoning scores (68.8% on LoCoMo, lowest category)
- **THEN** a paragraph explains what makes temporal questions harder for retrieval systems

#### Scenario: Cross-encoder explanation
- **WHEN** the reader sees that cross-encoder reranking hurt performance
- **THEN** a paragraph explains the mechanism (why reranking degrades results for this architecture)

### Requirement: Concrete worked example
The document SHALL include one end-to-end worked example showing a benchmark question, the retrieved chunks, and the generated answer.

#### Scenario: Example is present and complete
- **WHEN** a reader looks for a concrete example
- **THEN** they find a LoCoMo multi-hop or temporal question with 2-3 retrieved chunks and the system's answer, in under 200 words

### Requirement: Chapter 5 reframed as methodology practices
Chapter 5 SHALL present the author's methodology practices positively rather than as a competitive audit of another system.

#### Scenario: No adversarial language
- **WHEN** a reader reads Chapter 5
- **THEN** they find no instances of "Real vs Fake", no direct critique of a named competitor's implementation, and no loaded editorial judgments

#### Scenario: Transparency points preserved
- **WHEN** a reader reads Chapter 5
- **THEN** they find clear statements about reproducibility practices (open-source benchmarks, accurate token counting, no cherry-picking)

### Requirement: Production pipeline concern seeded early
The shift from session-level chunking to production chunking SHALL be mentioned in the methodology or "What is this?" section, before the Production Pipeline Validation section.

#### Scenario: Reader expects the validation
- **WHEN** the reader reaches the Production Pipeline Validation section
- **THEN** they already know from an earlier section that the initial benchmark used different chunking than production
