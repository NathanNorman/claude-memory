## 1. Markdown Splitter (Stage 1)

- [x] 1.1 Create `src/semantic-markdown-chunker.ts` with `MarkdownUnit` interface (`type`, `lines`, `startLine`, `endLine`, `text`, `tokenCount`)
- [x] 1.2 Implement `parseMarkdownUnits(content: string): MarkdownUnit[]` -- splits markdown into atomic units (heading+content, fenced code blocks, list runs, thematic breaks, YAML frontmatter, tables, paragraphs)
- [x] 1.3 Handle edge cases: unclosed code fences treated as plain text, empty files return empty array, nested lists grouped into single list run

## 2. Boundary Scorer (Stage 2)

- [x] 2.1 Implement `scoreMarkdownBoundary(prev: MarkdownUnit, curr: MarkdownUnit): number` returning 0-3 score based on heading boundaries (+1.5), thematic breaks (+1.5), heading level changes (+1.0), content type shifts (+0.5), blank line separation (+0.25), capped at 3.0
- [x] 2.2 Implement `scoreAllMarkdownBoundaries(units: MarkdownUnit[]): number[]` returning array of length `units.length - 1`

## 3. DP Segmentation Adapter

- [x] 3.1 Create adapter that wraps `MarkdownUnit[]` to satisfy the interface expected by `segmentVarianceDp()` from `src/semantic-chunker.ts` (provide token counts and allow index-based access)
- [x] 3.2 Call `segmentVarianceDp()` with markdown-tuned parameters: `minChunkTokens: 100`, `maxChunkTokens: 2000`, `varianceWeight: 0.3`
- [x] 3.3 Map DP output `[startIdx, endIdx]` segment ranges back to source line numbers and concatenated text for `RawChunk` construction

## 4. Public API

- [x] 4.1 Implement `chunkMarkdownSemantic(content: string): RawChunk[]` that chains stages 1-3 and returns `RawChunk[]` with `startLine`, `endLine`, `text`, `hash` fields
- [x] 4.2 Handle empty/whitespace input (return `[]`) and single-unit input (return one chunk)

## 5. Integration

- [x] 5.1 In `src/indexer.ts`, add import for `chunkMarkdownSemantic` from `./semantic-markdown-chunker.js`
- [x] 5.2 Replace `chunkMarkdown(content)` call on line 311 with `chunkMarkdownSemantic(content)`
- [x] 5.3 Update `CHUNK_TOKENS` constant from `'400-v3-semantic'` to `'400-v4-semantic-md'`
- [x] 5.4 Verify `chunkMarkdown` import is retained for the conversation fallback path (line 410)

## 6. Validation

- [x] 6.1 Run `npm run build` -- confirm clean compilation with no type errors
- [x] 6.2 Run `npm test` -- confirm existing integration tests pass with the new chunker
- [x] 6.3 Manually verify chunking on `MEMORY.md` and one daily log file by adding a temporary debug log, confirming chunks align with `##` sections and code blocks are not split
- [x] 6.4 Run a full reindex (`npx tsc && node dist/reindex-cli.js`) and confirm it completes without errors
