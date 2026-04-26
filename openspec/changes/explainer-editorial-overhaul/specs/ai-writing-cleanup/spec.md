## ADDED Requirements

### Requirement: Em dash density within limit
The document SHALL contain no more than 2 em dashes per 1,000 words of prose content.

#### Scenario: Em dash count
- **WHEN** the prose content is scanned for em dash characters (— and --)
- **THEN** the count is at most 4 total (for ~2,000 words of prose)

### Requirement: No mechanical bold-label pattern
Callout boxes and insight sections SHALL NOT mechanically lead every paragraph with a bold phrase.

#### Scenario: Bold variety in callouts
- **WHEN** a section contains 3+ consecutive callout paragraphs
- **THEN** at least one does NOT open with a bold label

### Requirement: No authority trope introductions
The document SHALL NOT use "The [noun]:" as a rhetorical frame to introduce insights (e.g., "The honest take:", "The conclusion:", "The takeaway:").

#### Scenario: Authority tropes removed
- **WHEN** the prose is scanned for patterns matching "The [noun]:" at the start of a statement
- **THEN** zero instances of "The honest take:", "The conclusion:", "The takeaway:", "The cleanest metric to own:" remain

### Requirement: No repeated promotional phrases
The document SHALL NOT repeat the same promotional phrase more than once.

#### Scenario: "standout strength" deduplicated
- **WHEN** the prose is scanned
- **THEN** "standout strength" appears zero times (replaced with specific data references)

#### Scenario: "best-in-class" backed by data
- **WHEN** "best-in-class" appears
- **THEN** it is immediately followed by a specific metric in the same sentence, OR it is removed

### Requirement: Tailing negations converted to positive statements
Clipped negative fragments ("not a marketing claim", "not a static architecture") SHALL be rewritten as positive assertions.

#### Scenario: Positive framing
- **WHEN** the prose is scanned for "not a [noun]" and "No [noun]." fragment patterns
- **THEN** at least 3 of the 5 identified instances are rewritten as positive statements
