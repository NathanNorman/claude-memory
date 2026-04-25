## Why

Three independent audits (avoid-ai-writing, humanizer, technical-writing-best-practices) found that the benchmark explainer reads as a data dashboard rather than a piece of technical writing. The document has strong data but weak narrative: no thesis, no conclusion, abrupt chapter transitions, unexplained results, and pervasive AI formatting tells (16+ em dashes, mechanical bold patterns). Chapter 5 (MemPalace audit) shifts into adversarial competitor critique that undermines the measured tone of the rest. The blog post at nathan-norman shares some of these issues but is lower priority.

## What Changes

- Add a thesis statement in the opening paragraph
- Add a real conclusion that extends rather than summarizes
- Reframe or remove Chapter 5 (MemPalace methodology comparison)
- Add explanatory paragraphs after data tables (why temporal is hard, why cross-encoder hurts, etc.)
- Add one concrete worked example (question, retrieved chunks, answer)
- Replace 14-16 em dashes with commas, periods, or split sentences
- Remove mechanical bold-label pattern from callouts
- Replace 5x "The [noun]:" authority trope with direct statements
- Cut "standout strength" (2x), bare "best-in-class", "crown jewels"
- Fix tailing negations, subjectless fragment clusters
- Add transition sentences between chapters
- Seed production-pipeline concern earlier in the document
- Propagate significant changes to the blog post

## Capabilities

### New Capabilities
- `editorial-overhaul`: Substantive editorial improvements to the explainer (thesis, conclusion, narrative arc, explanatory depth, concrete example)
- `ai-writing-cleanup`: Formatting pass to remove AI writing patterns (em dashes, bold overuse, authority tropes, promotional language)

### Modified Capabilities

## Impact

- Primary: `~/explainers/longmemeval-benchmark-results.html` (extensive edits)
- Secondary: `~/nathan-norman/blog/claude-memory-benchmarks.html` (propagate thesis/conclusion if significant)
- Both deploy automatically on push (GitHub Pages + Vercel)
