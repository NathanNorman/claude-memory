## MODIFIED Requirements

### Requirement: indexFile chunks markdown content
The `indexFile()` function SHALL chunk markdown content using `chunkMarkdownSemantic()` from `src/semantic-markdown-chunker.ts` instead of `chunkMarkdown()` from `src/chunker.ts`. The rest of the indexing pipeline (embedding, insertion, file upsert) remains unchanged.

#### Scenario: Markdown files use semantic chunker
- **WHEN** `indexFile(db, filePath, content)` is called for a markdown file
- **THEN** it SHALL call `chunkMarkdownSemantic(content)` to produce chunks

#### Scenario: Return type unchanged
- **WHEN** `chunkMarkdownSemantic()` returns `RawChunk[]`
- **THEN** the downstream embedding, `MemoryChunk` construction, and `insertChunksTransaction` calls SHALL work without modification

#### Scenario: Config change triggers full reindex
- **WHEN** the `CHUNK_TOKENS` constant is updated to `'400-v4-semantic-md'`
- **THEN** `indexAll()` SHALL detect the mismatch with the stored value and wipe/rebuild all chunks

#### Scenario: Old chunker preserved as fallback
- **WHEN** `chunkMarkdown()` is still imported in `indexer.ts` for conversation fallback (line 410)
- **THEN** the old `chunkMarkdown()` function SHALL remain available and unchanged in `src/chunker.ts`
