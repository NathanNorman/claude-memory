## Context

The explainer (`~/explainers/longmemeval-benchmark-results.html`) is a single-file static HTML document with ~1,600 lines. It was generated via the `/explainer` skill and iteratively refined. Three independent audits identified 9 issues: 5 substantive (narrative structure) and 4 formatting (AI writing patterns). The document is deployed to Toast GitHub Pages on push. A companion blog post (`~/nathan-norman/blog/claude-memory-benchmarks.html`) shares some content and deploys to Vercel.

The document's audience is technical peers evaluating memory systems — engineers who will scrutinize both the numbers and the methodology. The current version presents strong data but reads as a dashboard rather than an argument.

## Goals / Non-Goals

**Goals:**
- Transform the document from a results dashboard into a coherent technical argument
- Add a thesis, real conclusion, and chapter transitions that create narrative flow
- Explain *why* results are what they are, not just *what* they are
- Add one concrete worked example to make the system tangible
- Remove AI writing tells (em dashes, bold patterns, authority tropes)
- Reframe Chapter 5 to preserve transparency points without adversarial tone
- Propagate thesis/conclusion changes to the blog post

**Non-Goals:**
- Redesigning the HTML/CSS layout or visual design
- Changing any benchmark numbers or re-running benchmarks
- Adding new benchmark results or chapters
- Rewriting the blog post from scratch (only propagate key changes)

## Decisions

### 1. Edit in place, not rewrite
The document structure (6 chapters, two-column cards, insight callouts) works well. The issues are in the prose, not the layout. Edit existing content rather than rebuilding.

**Alternative considered:** Full rewrite via `/explainer`. Rejected — the layout, SVG diagrams, and scroll animations are polished and would be lost.

### 2. Reframe Chapter 5, don't delete it
The transparency and reproducibility points in Chapter 5 are valuable. Reframe as "Our methodology practices" — what we do and why — rather than a competitive audit. Remove "Real vs Fake" language and direct MemPalace critique. Keep the methodology table but make it self-referential.

**Alternative considered:** Delete Chapter 5 entirely. Rejected — the reproducibility points strengthen the document's credibility.

### 3. Worked example from LoCoMo, not LongMemEval
LoCoMo questions test multi-hop reasoning over casual conversation, which is more interesting to show than simple needle-in-haystack retrieval. Pick a temporal or multi-hop question where the system succeeds, showing retrieved chunks and generated answer.

**Alternative considered:** LongMemEval example. Rejected — single-hop retrieval examples are less illustrative of the system's strengths.

### 4. Two-pass editing order: substantive first, then formatting
Do structural/narrative edits first (thesis, conclusion, transitions, explanatory paragraphs, Chapter 5 reframe, worked example). Then do the formatting pass (em dashes, bold, tropes). This avoids formatting text that gets rewritten.

### 5. Blog post gets thesis + conclusion only
The blog post is shorter and has a different structure. Only propagate the thesis statement and conclusion. Don't replicate all formatting fixes.

## Risks / Trade-offs

- **Over-editing removes voice** → Keep the document's existing direct, data-driven tone. The formatting pass should follow avoid-ai-writing's guidance: "If the original writing is already strong, say so and make only the necessary cuts."
- **Chapter 5 reframe weakens competitive positioning** → The reframed version should still communicate that we follow practices others don't. Just lead with what we do, not what they don't.
- **Worked example adds length** → Keep it to one compact example (question + 3 chunks + answer), not a full walkthrough. Aim for under 200 words.
- **Em dash removal creates flat prose** → Replace thoughtfully. Some em dashes should become periods (two sentences), some commas, some parentheses. Don't mechanically replace all with the same punctuation.
