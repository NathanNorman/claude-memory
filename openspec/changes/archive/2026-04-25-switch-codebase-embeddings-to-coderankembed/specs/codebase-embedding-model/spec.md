## ADDED Requirements

### Requirement: Codebase chunks use CodeRankEmbed model
The codebase indexer SHALL use `nomic-ai/CodeRankEmbed` (768d) to generate embeddings for all codebase source code chunks. Memory and conversation chunks SHALL continue using `BAAI/bge-base-en-v1.5`.

#### Scenario: Indexing a new codebase
- **WHEN** `codebase-index.py` is run with `--path` and `--name`
- **THEN** the indexer loads `nomic-ai/CodeRankEmbed` and embeds all code chunks with that model

#### Scenario: Memory write still uses bge-base
- **WHEN** `memory_write` tool creates chunks for a curated memory file
- **THEN** embeddings are generated using `BAAI/bge-base-en-v1.5`, not CodeRankEmbed

### Requirement: Asymmetric query prefix for codebase search
The system SHALL prepend the prefix `"Represent this query for searching relevant code: "` to search queries when performing vector search against codebase chunks. Document embeddings SHALL NOT include any prefix.

#### Scenario: codebase_search applies query prefix
- **WHEN** `codebase_search` tool receives a query string
- **THEN** the vector search encodes the query with the prefix `"Represent this query for searching relevant code: "` prepended before computing embeddings

#### Scenario: memory_search with source=codebase applies query prefix
- **WHEN** `memory_search` tool is called with `source="codebase"`
- **THEN** the vector search encodes the query with the CodeRankEmbed query prefix prepended

#### Scenario: Document embedding has no prefix
- **WHEN** `codebase-index.py` embeds a code chunk
- **THEN** the chunk content is embedded without any query prefix

### Requirement: Structural context prefix on code chunk embeddings
Before embedding a code chunk, the indexer SHALL prepend a structural context line in the format `"{relative_file_path} | {symbol_type} {symbol_name}\n"` to the text sent to the embedding model. This prefix SHALL NOT be stored in the `content` column of the chunks table.

#### Scenario: Python function chunk gets context prefix
- **WHEN** a chunk with title `def process_payment` from file `codebase:myapp/src/billing.py` is embedded
- **THEN** the embedding input text starts with `src/billing.py | def process_payment\n` followed by the chunk content

#### Scenario: Kotlin class chunk gets context prefix
- **WHEN** a chunk with title `class PaymentProcessor` from file `codebase:myapp/src/payments/processor.kt` is embedded
- **THEN** the embedding input text starts with `src/payments/processor.kt | class PaymentProcessor\n` followed by the chunk content

#### Scenario: Stored content is unmodified
- **WHEN** a code chunk is stored in the chunks table
- **THEN** the `content` column contains only the original source code without any structural prefix

### Requirement: Per-source model tracking in meta table
The system SHALL store a `codebase_embedding_model` key in the `meta` table recording which model was used for codebase embeddings. This is separate from the existing `embedding_model` key used for memory/conversation embeddings.

#### Scenario: Model name recorded after indexing
- **WHEN** `codebase-index.py` completes indexing a codebase
- **THEN** the meta table contains a row with `key='codebase_embedding_model'` and `value='nomic-ai/CodeRankEmbed'`

#### Scenario: Model change triggers full reindex
- **WHEN** `codebase-index.py` starts and detects the configured model differs from the `codebase_embedding_model` value in the meta table
- **THEN** the indexer purges all existing codebase chunks and performs a full reindex

### Requirement: Lazy loading of CodeRankEmbed in MCP server
The MCP server SHALL load `nomic-ai/CodeRankEmbed` lazily on the first `codebase_search` or `memory_search(source="codebase")` call, not during server startup warmup.

#### Scenario: Server starts without loading CodeRankEmbed
- **WHEN** the MCP server starts up
- **THEN** only `BAAI/bge-base-en-v1.5` is loaded during warmup; CodeRankEmbed is not loaded

#### Scenario: First codebase search triggers model load
- **WHEN** `codebase_search` is called for the first time in a session
- **THEN** `nomic-ai/CodeRankEmbed` is loaded before encoding the query
