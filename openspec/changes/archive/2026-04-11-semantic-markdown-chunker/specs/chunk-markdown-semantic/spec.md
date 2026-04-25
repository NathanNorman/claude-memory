## ADDED Requirements

### Requirement: Parse markdown into atomic units
The system SHALL parse markdown content into atomic units that are never split across chunk boundaries. Unit types: heading-with-content, fenced code block, list run, thematic break, YAML frontmatter, table, and paragraph.

#### Scenario: Fenced code block preserved
- **WHEN** markdown contains a fenced code block (`` ``` `` through closing `` ``` ``)
- **THEN** the entire code block SHALL be a single atomic unit, never split across chunks

#### Scenario: Heading kept with its content
- **WHEN** markdown contains a heading line (`#`, `##`, etc.) followed by content lines before the next heading or paragraph break
- **THEN** the heading and its immediate content SHALL form a single atomic unit

#### Scenario: List run preserved
- **WHEN** markdown contains consecutive list items (`- `, `* `, `1. `) including nested/indented continuations
- **THEN** the entire list run SHALL be a single atomic unit

#### Scenario: Thematic break as standalone unit
- **WHEN** markdown contains a thematic break (`---`, `***`, or `___`)
- **THEN** the thematic break SHALL be its own atomic unit

#### Scenario: Table preserved
- **WHEN** markdown contains consecutive `|`-prefixed lines forming a table
- **THEN** the entire table SHALL be a single atomic unit

#### Scenario: YAML frontmatter preserved
- **WHEN** a markdown file begins with `---` delimited YAML frontmatter
- **THEN** the entire frontmatter block SHALL be a single atomic unit

#### Scenario: Plain paragraph as unit
- **WHEN** markdown contains contiguous non-blank lines that do not match any special unit type
- **THEN** those lines SHALL form a single paragraph unit

### Requirement: Score boundaries between adjacent units
The system SHALL assign a boundary score between 0 and 3 (inclusive) to each pair of adjacent atomic units using markdown structural signals. Higher scores indicate stronger semantic breaks.

#### Scenario: Heading boundary scores high
- **WHEN** a `##` heading unit follows a non-heading unit
- **THEN** the boundary score SHALL be at least 1.5

#### Scenario: Thematic break scores high
- **WHEN** a thematic break (`---`) separates two units
- **THEN** the boundary score SHALL be at least 1.5

#### Scenario: Content type shift scores medium
- **WHEN** adjacent units have different content types (e.g., prose followed by code block, or code block followed by list)
- **THEN** the boundary score SHALL be at least 0.5

#### Scenario: Blank line separation adds signal
- **WHEN** adjacent units are separated by one or more blank lines
- **THEN** the boundary score SHALL increase by at least 0.25

#### Scenario: Score capped at 3
- **WHEN** multiple signals accumulate for a single boundary
- **THEN** the score SHALL NOT exceed 3.0

### Requirement: Segment units into chunks via DP optimization
The system SHALL use dynamic programming segmentation (reusing `segmentVarianceDp` from `src/semantic-chunker.ts`) to find optimal chunk boundaries that maximize average boundary score while penalizing uneven chunk sizes.

#### Scenario: Chunks respect token size bounds
- **WHEN** segmenting markdown units into chunks
- **THEN** each chunk SHALL contain at least 100 tokens and at most 2000 tokens (estimated as chars/4), except when a single atomic unit exceeds the maximum

#### Scenario: Single oversized unit becomes its own chunk
- **WHEN** a single atomic unit exceeds 2000 tokens
- **THEN** that unit SHALL be placed in its own chunk without splitting

#### Scenario: Small file produces single chunk
- **WHEN** the total content is under 100 tokens
- **THEN** the system SHALL return exactly one chunk containing all content

#### Scenario: DP balances boundary quality and size uniformity
- **WHEN** multiple valid segmentations exist
- **THEN** the system SHALL choose the segmentation that maximizes `avg_boundary_score - 0.3 * CV(chunk_sizes)` where CV is the coefficient of variation of chunk token sizes

### Requirement: Return RawChunk array
The function `chunkMarkdownSemantic()` SHALL return `RawChunk[]` with the same interface as `chunkMarkdown()`: each chunk has `startLine`, `endLine`, `text`, and `hash` fields.

#### Scenario: Output format matches existing interface
- **WHEN** `chunkMarkdownSemantic(content)` is called
- **THEN** it SHALL return an array of `RawChunk` objects where `startLine` and `endLine` are 1-indexed line numbers, `text` is the chunk content, and `hash` is `hashText(text)`

#### Scenario: Empty content returns empty array
- **WHEN** called with empty string or whitespace-only content
- **THEN** it SHALL return an empty array

#### Scenario: Line numbers are contiguous and complete
- **WHEN** chunking produces multiple chunks
- **THEN** the `endLine` of chunk N plus 1 SHALL equal the `startLine` of chunk N+1, covering all lines in the input
