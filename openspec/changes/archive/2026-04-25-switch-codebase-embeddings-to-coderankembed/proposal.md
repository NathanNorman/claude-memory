## Why

The current codebase embedding model (bge-base-en-v1.5) was designed for general-purpose text retrieval and achieves ~50-55% MRR on code search benchmarks. CodeRankEmbed (nomic-ai/CodeRankEmbed) is a code-specialized model trained on CoRNStack with dual-consistency filtering, achieving 77.9% CSN MRR and 60.1% CoIR NDCG@10 -- a 15-30% improvement in code retrieval quality. Both models produce 768-dimensional embeddings, so no schema changes are needed.

## What Changes

- Replace `BAAI/bge-base-en-v1.5` with `nomic-ai/CodeRankEmbed` for codebase embedding in `scripts/codebase-index.py`
- Implement asymmetric query prefix for CodeRankEmbed: queries get `"Represent this query for searching relevant code: "` prefix, documents are embedded without prefix
- Add structural context prefixes to code chunks before embedding (file path + symbol type/name), based on the cAST paper (EMNLP 2025, +4.3 Recall@5)
- Introduce dual-model architecture: CodeRankEmbed for codebase sources, bge-base-en-v1.5 for memory/conversation sources
- Update `codebase_search` tool in the MCP server to apply query prefix before embedding
- Track per-source model names in meta table to trigger automatic reindex on model change

## Capabilities

### New Capabilities
- `codebase-embedding-model`: Dual-model embedding architecture with CodeRankEmbed for code and bge-base for natural language, including asymmetric query prefixes and structural context enrichment

### Modified Capabilities

## Impact

- **`scripts/codebase-index.py`**: New model constants, query prefix logic, structural context prefix for chunks
- **`src/unified_memory_server.py`**: Dual-model loading in VectorSearchBackend, query prefix in codebase_search, per-source model tracking
- **`src/code_chunker.py`**: Structural context prefix prepended to chunk content before embedding
- **Dependencies**: `nomic-ai/CodeRankEmbed` (~521MB, MIT license, runs on CPU via sentence-transformers)
- **Migration**: Changing codebase model triggers full reindex of codebase chunks; memory/conversation chunks unaffected
- **`src/quantize.py`**: TurboQuant codebook may need recalculation for the new model's weight distribution
