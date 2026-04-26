## Why

The markdown chunker (`chunkMarkdown()` in `src/chunker.ts`) splits files by accumulating lines until hitting ~1600 chars, then flushing with overlap. It has zero semantic awareness -- chunk boundaries can land mid-paragraph, mid-list, mid-code-block, or between a heading and its content. After proving that semantic chunking (heuristic boundary scoring + DP segmentation) achieves 100% on LongMemEval for conversations vs 99.6% for budget chunking, the same approach should be applied to markdown files. Daily logs and MEMORY.md have rich structural signals (headings, session blocks, code fences, lists) that the current chunker ignores entirely.

## What Changes

- **New function `chunkMarkdownSemantic()`** in a new file `src/semantic-markdown-chunker.ts`. Drop-in replacement for `chunkMarkdown()`, returning the same `RawChunk[]` type.
- **Markdown-aware sentence/block splitting** (stage 1): Parse markdown into atomic units that must never be split:
  - Heading + its immediate content (until next heading or blank line paragraph break)
  - Fenced code blocks (``` ... ```)
  - List runs (consecutive `- ` or `1. ` lines, including nested)
  - Session log entries (the `## Session ended ...` through next `##` pattern dominant in daily logs)
  - YAML frontmatter blocks (`---` delimited)
  - Tables (consecutive `|`-prefixed lines)
- **Heuristic boundary scoring** (stage 2): Score boundaries between adjacent units on a 0-3 scale using markdown-native signals:
  - Heading level changes (h2 boundary > h3 boundary > paragraph break)
  - Thematic breaks (`---`, `***`)
  - Shift in content type (prose to code, code to list, etc.)
  - Blank-line separation (double newline = paragraph break signal)
  - Topic-shift phrases in heading text (e.g., different project name, different date)
- **DP segmentation** (stage 3): Reuse the existing `segmentVarianceDp()` from `src/semantic-chunker.ts` (it operates on generic item arrays with token counts and boundary scores -- no conversation-specific logic). Tune min/max chunk token params for markdown (likely 200-2000 tokens vs 150-1600 for conversations).
- **Wire into `indexFile()`** in `src/indexer.ts`: Replace the `chunkMarkdown(content)` call on line 311 with `chunkMarkdownSemantic(content)`. The `RawChunk[]` return type is identical, so nothing downstream changes.
- **Bump `CHUNK_TOKENS` config string** to trigger a full re-index on next run (existing mechanism in `indexAll()`).

## Capabilities

### New Capabilities
- `chunkMarkdownSemantic()`: Structure-aware markdown chunker that respects headings, code blocks, lists, and session entries as atomic units, then uses DP to find optimal chunk boundaries at semantic transitions.

### Modified Capabilities
- `indexFile()`: Switches from `chunkMarkdown()` to `chunkMarkdownSemantic()` for all markdown files (MEMORY.md and memory/*.md daily logs). Return type and downstream embedding/insertion pipeline unchanged.

## Impact

**Affected code:**
- `src/semantic-markdown-chunker.ts` -- new file (~200-300 lines)
- `src/indexer.ts` -- one import change, one call-site change (line 311)
- `src/chunker.ts` -- `chunkMarkdown()` retained but no longer called for memory files (still used as fallback in conversation indexing at line 410)

**No API changes:** The MCP tools (`memory_search`, `memory_read`, `memory_write`, `get_status`) are unaffected. The `RawChunk` interface is unchanged. The SQLite schema is unchanged.

**Reindex required:** Bumping `CHUNK_TOKENS` forces a one-time full reindex of all ~80 markdown files. This runs automatically on next SessionEnd hook or cron trigger. Estimated time: under 30 seconds (markdown files are small; embeddings will be cache misses due to new chunk boundaries).

**Reuses existing infrastructure:** The DP segmenter (`segmentVarianceDp`) is already proven on conversations. The new code is only the markdown splitter and boundary scorer -- the optimization core is shared.

**No new dependencies.** Pure TypeScript, same as the conversation semantic chunker.

**Risk:** Low. The old `chunkMarkdown()` is preserved as fallback. If the new chunker produces degenerate output for some file, the failure mode is suboptimal chunk boundaries (same as today), not data loss. The existing integration test (`src/integration.test.ts`) covers the indexing pipeline end-to-end and will validate the new chunker's output shape.
