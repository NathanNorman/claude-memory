## Context

The unified-memory system currently uses a single embedding model (`BAAI/bge-base-en-v1.5`, 768d) for all content types: conversations, curated memory files, and codebase source code. The Python MCP server (`unified_memory_server.py`) and the codebase indexer (`codebase-index.py`) both load this model via `sentence-transformers`. Source routing already separates codebase chunks (prefixed `codebase:`) from memory chunks at search time, but they share the same model.

CodeRankEmbed (`nomic-ai/CodeRankEmbed`) is a 137M-param, 768d code-specialized model that uses asymmetric embedding: queries require a prefix string while documents are embedded raw. It runs on CPU via sentence-transformers with no additional dependencies.

## Goals / Non-Goals

**Goals:**
- Replace the codebase embedding model with CodeRankEmbed for higher code retrieval quality
- Implement asymmetric query prefix correctly (queries get prefix, documents do not)
- Add structural context prefixes to code chunks (file path + symbol info) before embedding
- Maintain backward compatibility: memory/conversation embeddings stay on bge-base unchanged
- Track per-source model identity in the meta table so model changes trigger reindex

**Non-Goals:**
- Changing the Node.js indexer's model (it handles conversations/memory, not codebase)
- Migrating existing memory/conversation embeddings
- Switching to a different vector storage format or adding a new vector table
- Optimizing CodeRankEmbed inference speed (CPU is acceptable for batch indexing)

## Decisions

### 1. Dual-model constants, single VectorSearchBackend

Introduce two model constants:
- `CODEBASE_EMBEDDING_MODEL = 'nomic-ai/CodeRankEmbed'`
- `MEMORY_EMBEDDING_MODEL = 'BAAI/bge-base-en-v1.5'`

The VectorSearchBackend continues using bge-base for its in-memory index (which loads all embeddings for brute-force search). The codebase_search tool will load a second model instance for query embedding only when the query targets codebase chunks.

**Alternative considered**: Two separate VectorSearchBackend instances. Rejected because embeddings from both models live in the same `chunks` table and the existing source-routing post-filter handles separation. A second backend would duplicate index loading for no benefit.

### 2. Query prefix applied at search time only

CodeRankEmbed requires the prefix `"Represent this query for searching relevant code: "` on queries but NOT on documents. This prefix is applied:
- In `codebase_search()` before calling `model.encode()` on the query
- In `memory_search()` when `source='codebase'` before vector search

Document embeddings in `codebase-index.py` are embedded without any prefix.

### 3. Structural context prefix on chunks at indexing time

Before embedding a code chunk, prepend a structural context line:
```
{relative_file_path} | {symbol_type} {symbol_name}
```
Example: `src/payments/processor.kt | method PaymentProcessor.validate`

This is applied in `codebase-index.py` at embedding time (not stored in the chunk content column -- the prefix is only in the embedding input). The `code_chunker.py` title field already contains symbol info (e.g., `def my_function`, `class MyClass`), so we derive the prefix from `chunk['title']` and the file path.

**Rationale**: Based on cAST (EMNLP 2025) showing +4.3 Recall@5. Prepending to embedding input rather than stored content keeps the content column clean for display.

### 4. Per-source model tracking in meta table

Add a new meta key `codebase_embedding_model` alongside the existing `embedding_model` key. The codebase indexer checks this key on startup: if it differs from the configured model, all codebase chunks are purged and reindexed.

### 5. Lazy model loading for CodeRankEmbed in the MCP server

The MCP server loads bge-base eagerly during warmup (it's needed for all memory searches). CodeRankEmbed is loaded lazily on the first `codebase_search` call to avoid doubling startup memory and time when codebase search isn't used.

## Risks / Trade-offs

- **[Memory]** Loading two 768d models doubles RAM for embeddings (~1GB total). -> Mitigation: CodeRankEmbed loaded lazily; most sessions never call codebase_search.
- **[Reindex cost]** Switching the codebase model forces a full reindex of all codebase chunks. -> Mitigation: One-time cost, incremental after that. The `--update` flag continues to work post-migration.
- **[Quantization]** TurboQuant codebook was calibrated for bge-base weight distribution. CodeRankEmbed may need a new codebook. -> Mitigation: Check if existing codebook works; if recall drops, recalculate via `quantize.py` for the new model.
- **[Embedding mismatch]** If someone searches codebase with the old bge-base embeddings still present, cosine similarity will be meaningless. -> Mitigation: Model change detection in meta table forces full reindex before mixed embeddings can occur.
