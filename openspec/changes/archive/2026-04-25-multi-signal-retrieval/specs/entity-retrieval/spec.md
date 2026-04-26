## ADDED Requirements

### Requirement: Chunks SHALL have extracted named entities
The system SHALL extract named entities from chunk content at index time using spaCy `en_core_web_sm` and store entity-to-chunk mappings in a `chunk_entities` table. Entity types SHALL include at minimum: PERSON, DATE, ORG, GPE, LOC, EVENT, PRODUCT.

#### Scenario: Entity extraction from conversation chunk
- **WHEN** a chunk containing "Caroline said she went to the LGBTQ support group in Hartford" is indexed
- **THEN** `chunk_entities` SHALL contain entries for PERSON("caroline"), ORG("lgbtq support group"), GPE("hartford")

#### Scenario: Multiple entities in one chunk
- **WHEN** a chunk mentions "Sarah", "Toast", and "Hartford"
- **THEN** all three entities SHALL be stored with their respective types (PERSON, ORG, GPE)

#### Scenario: Entity normalization
- **WHEN** entities are stored
- **THEN** `entity_text` SHALL be lowercased for matching, and `entity_text_original` SHALL preserve original case

### Requirement: Entity overlap SHALL be a retrieval signal
The system SHALL compute entity overlap between query entities and chunk entities, producing a ranked list for RRF merge. Chunks containing more query entities SHALL rank higher.

#### Scenario: Single entity query
- **WHEN** a query mentions "Caroline" and the entity index contains 15 chunks mentioning "caroline"
- **THEN** all 15 chunks SHALL appear in the entity overlap ranked list

#### Scenario: Multi-entity query
- **WHEN** a query mentions "Caroline" and "support group"
- **THEN** chunks containing both entities SHALL rank above chunks containing only one

#### Scenario: No entity matches
- **WHEN** a query's extracted entities match no indexed entities
- **THEN** the entity signal SHALL return an empty ranked list, contributing nothing to RRF

### Requirement: Entity extraction SHALL use no LLM calls
The system SHALL use spaCy `en_core_web_sm` (12MB, ~10,000 words/sec CPU) for NER. No LLM API calls SHALL be made for entity extraction.

#### Scenario: Extraction performance
- **WHEN** processing 15,000 chunks at ~200 words each
- **THEN** entity extraction SHALL complete in under 10 minutes on CPU
