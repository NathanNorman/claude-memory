#!/Users/nathan.norman/.pyenv/versions/3.12.11/bin/python3
"""
Integration test for summarize_with_refinement().

Requires `claude` CLI to be available. Skip in CI environments.
Run manually: python3 scripts/test_summary_refinement.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


SAMPLE_TRANSCRIPT = """\
[User]: I need to fix the chunker in src/chunker.ts. It's splitting code blocks in half.

[Assistant]: I can see the issue. The chunkMarkdown function in src/chunker.ts uses a simple \
character budget approach - it accumulates lines until hitting ~1600 chars, then flushes. \
It has no awareness of markdown structure, so fenced code blocks get split mid-block.

[User]: What's the best approach to fix this?

[Assistant]: I recommend creating a new semantic markdown chunker that:
1. Parses markdown into atomic units (headings, code blocks, lists, tables)
2. Scores boundaries between units using structural signals
3. Uses the existing segmentVarianceDp() from semantic-chunker.ts for optimal segmentation

This mirrors the approach we already proved works for conversations. The DP segmenter is \
generic - it just needs token counts and boundary scores.

[User]: Do it. Use minChunkTokens of 100 and maxChunkTokens of 2000.

[Assistant]: I created src/semantic-markdown-chunker.ts with chunkMarkdownSemantic(). \
Updated src/indexer.ts line 311 to use the new function instead of chunkMarkdown(). \
Bumped CHUNK_TOKENS to '400-v4-semantic-md' to trigger a full reindex. All 24 tests pass.
"""


def test_summarize_with_refinement():
    """Run the full summarize-judge-refine loop on sample transcript."""
    from summary_refinement import summarize_with_refinement

    result = summarize_with_refinement(
        SAMPLE_TRANSCRIPT,
        model='haiku',
        threshold=8.0,
        max_iter=2,
    )

    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert 'summary' in result, "Missing 'summary' key"
    assert 'score' in result, "Missing 'score' key"
    assert 'iterations' in result, "Missing 'iterations' key"
    assert 'refined' in result, "Missing 'refined' key"
    assert 'elapsed_seconds' in result, "Missing 'elapsed_seconds' key"

    assert isinstance(result['summary'], str) and len(result['summary']) > 50, \
        f"Summary too short: {len(result['summary'])} chars"
    assert 0 <= result['score'] <= 10, f"Score out of range: {result['score']}"
    assert result['iterations'] >= 1, f"Iterations should be >= 1: {result['iterations']}"
    assert isinstance(result['refined'], bool)
    assert result['elapsed_seconds'] > 0

    print(f"  Score: {result['score']}/10")
    print(f"  Iterations: {result['iterations']}")
    print(f"  Refined: {result['refined']}")
    print(f"  Time: {result['elapsed_seconds']:.1f}s")
    print(f"  Summary length: {len(result['summary'])} chars")
    print(f"  Summary preview: {result['summary'][:200]}...")


if __name__ == '__main__':
    # Skip in CI or when claude CLI is not available
    if os.environ.get('CI'):
        print("SKIP: CI environment, claude CLI not available")
        sys.exit(0)

    import shutil
    if not shutil.which('claude'):
        print("SKIP: claude CLI not found in PATH")
        sys.exit(0)

    print("Running integration test (requires claude CLI)...")
    try:
        test_summarize_with_refinement()
        print("\nPASS: summarize_with_refinement")
    except Exception as e:
        print(f"\nFAIL: {e}")
        sys.exit(1)
