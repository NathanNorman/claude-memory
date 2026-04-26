## ADDED Requirements

### Requirement: Chunks SHALL have event_date metadata
The system SHALL store an `event_date` (ISO 8601 YYYY-MM-DD) on each chunk representing the date of the event described in the chunk content. For conversation chunks, this SHALL default to the session timestamp. When the chunk content contains temporal expressions ("yesterday", "last week", "May 8"), the system SHALL resolve them against the session timestamp and store the resolved date.

#### Scenario: Session date as default
- **WHEN** a conversation chunk is indexed with session timestamp 2024-05-08
- **AND** the chunk contains no temporal expressions
- **THEN** `event_date` SHALL be set to `2024-05-08`

#### Scenario: Relative date resolution
- **WHEN** a conversation chunk with session timestamp 2024-05-08 contains "I went to the support group yesterday"
- **THEN** `event_date` SHALL be set to `2024-05-07`

#### Scenario: Absolute date in content
- **WHEN** a chunk contains "on January 15, 2024 we discussed..."
- **THEN** `event_date` SHALL be set to `2024-01-15`

#### Scenario: Curated memory files
- **WHEN** a curated memory file `memory/2024-05-08.md` is indexed
- **THEN** `event_date` SHALL be extracted from the filename

### Requirement: Temporal proximity SHALL be a retrieval signal
The system SHALL compute temporal proximity between query temporal references and chunk `event_date` values, producing a ranked list of temporally relevant chunks for RRF merge.

#### Scenario: Temporal query with date reference
- **WHEN** a search query contains "what happened in May 2024"
- **THEN** the temporal signal SHALL rank chunks with `event_date` in May 2024 highest, with Gaussian decay for adjacent months

#### Scenario: Non-temporal query
- **WHEN** a search query contains no temporal expressions
- **THEN** the temporal signal SHALL return an empty ranked list, contributing nothing to RRF

#### Scenario: Relative temporal query
- **WHEN** a search query contains "what did we discuss last week" with reference date 2024-05-15
- **THEN** the system SHALL resolve "last week" to approximately 2024-05-06 through 2024-05-12 and rank chunks in that date range highest

### Requirement: Temporal expressions SHALL be parsed without LLM calls
The system SHALL use `dateparser` (Python) for temporal expression extraction and resolution. No LLM API calls SHALL be made for temporal processing.

#### Scenario: dateparser resolution with reference timestamp
- **WHEN** parsing "yesterday" with reference timestamp 2024-05-08T13:56:00Z
- **THEN** the system SHALL return 2024-05-07
