## ADDED Requirements

### Requirement: Build script produces addon databases from file directories
A script `scripts/build-reference-db.py` SHALL accept a directory path and output path, and produce a SQLite database containing chunked, FTS5-indexed, and embedded content from all supported files in the directory.

#### Scenario: Build from markdown directory
- **WHEN** `build-reference-db.py ./spark-docs/ -o spark-sql.db` is run
- **AND** `./spark-docs/` contains 10 markdown files
- **THEN** a `spark-sql.db` file is created
- **AND** the database contains chunks from all 10 files
- **AND** the `chunks_fts` table is populated for keyword search
- **AND** all chunks have embeddings in the `chunks` table

#### Scenario: Build from nested directory
- **WHEN** the input directory contains subdirectories with files
- **THEN** all files in subdirectories are indexed recursively
- **AND** `file_path` in chunks reflects the relative path from the input directory root

### Requirement: Build script stamps model metadata
The build script SHALL write the embedding model name and dimensions to the `meta` table in the output database.

#### Scenario: Meta table populated
- **WHEN** a database is built with model `bge-base-en-v1.5`
- **THEN** the `meta` table contains `key=embedding_model, value=bge-base-en-v1.5`
- **AND** the `meta` table contains `key=embedding_dims, value=768`

### Requirement: Build script supports multiple input formats
The build script SHALL process `.md`, `.txt`, and `.rst` files. Other file types SHALL be skipped with an info log.

#### Scenario: Mixed file types in directory
- **WHEN** the input directory contains `.md`, `.txt`, `.png`, and `.pdf` files
- **THEN** the `.md` and `.txt` files are chunked and indexed
- **AND** the `.png` and `.pdf` files are skipped
- **AND** an info message logs which files were skipped

### Requirement: Build script uses heading-aware chunking for markdown
Markdown files SHALL be chunked at `##` heading boundaries, consistent with the existing `_chunk_markdown()` method in `FlatSearchBackend`.

#### Scenario: Markdown with multiple sections
- **WHEN** a markdown file has 5 `##` sections
- **THEN** the file produces 5 chunks
- **AND** each chunk's `title` is the heading text

#### Scenario: Plain text file chunking
- **WHEN** a `.txt` file has no markdown headings
- **THEN** the file is chunked by character count (max ~1600 chars per chunk)
- **AND** chunks split at paragraph boundaries when possible

### Requirement: Build script uses quantized embeddings when available
If quantization parameters are configured (matching the server's quantization setup), the build script SHALL produce quantized embedding BLOBs. Otherwise, float32 BLOBs are produced.

#### Scenario: Build with quantization
- **WHEN** quantization parameters are available for `bge-base-en-v1.5`
- **THEN** embedding BLOBs are 4-bit quantized
- **AND** the `quantization_meta` table is populated in the output database

#### Scenario: Build without quantization
- **WHEN** no quantization parameters are available
- **THEN** embedding BLOBs are float32 (768 dims * 4 bytes = 3072 bytes each)

### Requirement: Build script creates complete schema
The output database SHALL contain all tables needed for the server's `FlatSearchBackend` and `VectorSearchBackend` to operate: `chunks`, `chunks_fts`, `files`, `meta`. Tables not needed for read-only operation (`codebase_meta`, `edges`, `symbols`) MAY be omitted.

#### Scenario: Database is directly usable by server
- **WHEN** a built database is placed in `~/.claude/skills/my-skill/`
- **AND** the server discovers it
- **THEN** both keyword and vector search work without any migration or setup

### Requirement: Build script is idempotent
Running the build script twice with the same input directory and output path SHALL produce an equivalent database (same chunks, same embeddings).

#### Scenario: Rebuild produces same results
- **WHEN** `build-reference-db.py ./docs/ -o ref.db` is run twice
- **THEN** the second run produces a database with the same chunk count and content hashes
