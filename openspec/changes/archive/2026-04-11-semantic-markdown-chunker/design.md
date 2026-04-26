## Context

The markdown chunker (`chunkMarkdown()` in `src/chunker.ts`) uses a budget-fill strategy: accumulate lines until ~1600 chars, flush with overlap. This produces chunks that split mid-paragraph, mid-list, mid-code-block, or between a heading and its content. The semantic chunker for conversations (`src/semantic-chunker.ts`) already proved that heuristic boundary scoring + DP segmentation produces superior chunks -- 100% on LongMemEval vs 99.6% for budget chunking. The same approach should be applied to the ~80 markdown files (MEMORY.md + memory/*.md daily logs).

**Current state:** `indexFile()` in `src/indexer.ts` (line 311) calls `chunkMarkdown(content)` for all markdown files. The returned `RawChunk[]` feeds into embedding and SQLite insertion unchanged.

**Content patterns in memory files:**
- **MEMORY.md**: Long-term knowledge organized by `##` sections (project, system, pattern). Each section is 3-15 lines. Sections separated by `---` thematic breaks.
- **Daily logs** (`memory/YYYY-MM-DD.md`): Date heading, then `##` session blocks with bullet lists. Typical file has 2-8 sessions, each 3-20 lines. Some contain code fences, inline code, and nested lists.

**Constraint:** The `RawChunk` interface (`startLine`, `endLine`, `text`, `hash`) and the downstream embedding/insertion pipeline must remain unchanged. The new chunker is a drop-in replacement.

## Goals / Non-Goals

**Goals:**
- Chunk boundaries align with markdown structure (headings, code blocks, lists, thematic breaks) rather than arbitrary character budgets
- Reuse the proven `segmentVarianceDp()` from `src/semantic-chunker.ts` for optimal boundary placement
- Drop-in replacement: same `RawChunk[]` return type, single call-site change in `indexFile()`
- No new dependencies -- pure TypeScript, same as the conversation semantic chunker

**Non-Goals:**
- LLM-based scoring (Memento's `score.py` uses GPT-4; we use heuristics only for zero-cost, zero-latency operation)
- Changing the embedding model, SQLite schema, or MCP tool interfaces
- Chunking conversations differently (that is already handled by `chunkExchangesSemantic()`)
- Supporting non-markdown file types

## Decisions

### 1. Three-stage pipeline mirroring the conversation chunker

**Decision:** Split markdown into atomic units, score boundaries heuristically, segment with DP.

**Rationale:** This is the exact pattern proven in `src/semantic-chunker.ts` for conversations and in Memento's `sentence_split.py` + `segment.py` for CoT traces. The DP segmenter (`segmentVarianceDp`) is already generic -- it operates on item arrays with token counts and boundary scores, not conversation-specific data. Only stages 1 (splitting) and 2 (scoring) need markdown-specific logic.

**Alternative considered:** Regex-based chunking at heading boundaries only. Rejected because MEMORY.md sections vary from 3 to 50+ lines; heading-only splitting produces wildly uneven chunks that the DP optimizer exists to prevent.

### 2. Adapt `segmentVarianceDp` via a thin wrapper, not a fork

**Decision:** Import and call `segmentVarianceDp()` from `src/semantic-chunker.ts` directly. Create a lightweight `MarkdownUnit` interface that provides the token count the DP needs, and map the DP's `[startIdx, endIdx]` output back to line numbers for `RawChunk`.

**Rationale:** The DP function's signature accepts `ConversationExchange[]` but only reads token counts and boundary scores. Rather than refactoring the DP to be generic (which would change the proven conversation path), we create adapter objects that satisfy the interface. If the DP is later generalized, the adapter simplifies to direct calls.

**Alternative considered:** Extract a generic `segmentGeneric()` function. This would be cleaner but requires modifying `semantic-chunker.ts` and re-validating the conversation chunking path. Deferred to a follow-up refactor.

### 3. Markdown-native boundary signals (not conversation signals)

**Decision:** Score boundaries 0-3 using markdown structural signals:

| Signal | Score contribution | Rationale |
|--------|-------------------|-----------|
| `##` heading boundary | +1.5 | Strongest structural signal in daily logs |
| `---` thematic break | +1.5 | Explicit section separator in MEMORY.md |
| Heading level decrease (h2 after h3) | +1.0 | Topic scope change |
| Content type shift (prose to code, code to list) | +0.5 | Semantic transition |
| Double blank line | +0.5 | Paragraph break convention |
| Single blank line | +0.25 | Weak separation |

Cap at 3.0, same as conversation scorer. These signals are drawn directly from the proposal and tuned to the actual content patterns in `~/.claude-memory/`.

**Alternative considered:** Reusing conversation signals (tool shifts, file path changes, time gaps). These don't exist in markdown content.

### 4. Atomic unit types for stage 1

**Decision:** Parse markdown into these atomic (never-split) units:

1. **Heading + immediate content**: A `#`-prefixed line and all lines until the next heading or blank-line-separated paragraph break
2. **Fenced code blocks**: `` ``` `` through closing `` ``` ``, inclusive
3. **List runs**: Consecutive lines starting with `- `, `* `, or `N. ` (including nested/indented continuations)
4. **Thematic breaks**: `---`, `***`, `___` lines (standalone units, act as boundary signals)
5. **YAML frontmatter**: `---` delimited blocks at file start
6. **Tables**: Consecutive `|`-prefixed lines
7. **Paragraphs**: Remaining contiguous non-blank lines

**Rationale:** These match the actual structures in the memory files. The key insight from Memento's `sentence_split.py` is that protected blocks (code, math) must be identified first before splitting prose. Here, markdown structure serves the same role.

### 5. DP parameters tuned for markdown

**Decision:** `minChunkTokens: 100`, `maxChunkTokens: 2000`, `varianceWeight: 0.3`.

**Rationale:** Memory markdown files are smaller and more structured than conversations. MEMORY.md sections average ~50-200 tokens. Daily log sessions average ~100-400 tokens. Lower minimum (100 vs 150 for conversations) allows small but semantically complete sections to stand alone. Higher maximum (2000 vs 1600) accommodates longer MEMORY.md entries with code examples. Variance weight of 0.3 (same as conversations) balances size uniformity against respecting natural boundaries.

### 6. Trigger reindex via CHUNK_TOKENS bump

**Decision:** Change `CHUNK_TOKENS` constant from `'400-v3-semantic'` to `'400-v4-semantic-md'` to force a full reindex on next run.

**Rationale:** This is the existing mechanism in `indexAll()` (line 512). When `CHUNK_TOKENS` doesn't match the stored value, all chunks are wiped and rebuilt. The reindex covers ~80 markdown files and takes under 30 seconds.

## Risks / Trade-offs

**[Risk] DP adapter complexity** -- Wrapping `MarkdownUnit` objects to satisfy `ConversationExchange` interface adds indirection.
  Mitigation: The adapter is ~20 lines. If it becomes unwieldy, extract a generic DP in a follow-up.

**[Risk] Boundary scorer undertrained** -- Heuristic weights are based on manual inspection of a few files, not systematic evaluation.
  Mitigation: The current chunker has zero semantic awareness, so any structure-aware scoring is an improvement. Weights can be tuned later with A/B search quality comparison.

**[Risk] Reindex cost on deploy** -- Full reindex wipes and rebuilds all ~80 markdown files plus embedding cache misses.
  Mitigation: Estimated under 30 seconds. Conversation chunks are unaffected (different code path). Reindex happens automatically via SessionEnd hook or cron.

**[Risk] Edge cases in markdown parsing** -- Malformed fences, nested lists, mixed content.
  Mitigation: The splitter is best-effort. Unparseable content falls through to plain paragraph units, which is no worse than the current line-accumulation chunker.

## Migration Plan

1. Add `src/semantic-markdown-chunker.ts` with `chunkMarkdownSemantic()` function
2. In `src/indexer.ts`, change the import and call on line 311 from `chunkMarkdown(content)` to `chunkMarkdownSemantic(content)`
3. Bump `CHUNK_TOKENS` to `'400-v4-semantic-md'`
4. Run `npm run build` and `npm test` to validate
5. Next SessionEnd hook or cron trigger runs `indexAll()`, detects the config change, and reindexes all files

**Rollback:** Revert the import in `indexer.ts` back to `chunkMarkdown`, restore `CHUNK_TOKENS` to previous value. Next reindex rebuilds with old chunker.

## Open Questions

- Should the DP segmenter be generalized into a shared function that both conversation and markdown chunkers call? Deferred to follow-up refactor to avoid changing the proven conversation path.
- Are the boundary score weights optimal? Can be tuned empirically after deployment by comparing search result quality on known queries.
