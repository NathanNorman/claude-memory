import type { SearchResult } from './types.js';

/**
 * Sanitize a raw query string for FTS5 MATCH.
 * Strips special characters, wraps each word in double quotes,
 * and joins with OR for broad recall. BM25 naturally ranks
 * documents with more matching terms higher.
 */
export function buildFtsQuery(query: string): string {
  const tokens =
    query
      .match(/[A-Za-z0-9_]+/g)
      ?.map((t) => t.trim())
      .filter(Boolean) ?? [];
  if (tokens.length === 0) {
    return '';
  }
  const quoted = tokens.map((t) => `"${t.replaceAll('"', '')}"`);
  return quoted.join(' OR ');
}

/**
 * Convert a negative BM25 rank (from SQLite FTS5) to a 0–1 score.
 * FTS5 bm25() returns negative values where more negative = better match.
 * Formula: 1 / (1 - rank), so rank=-5 → 1/6 ≈ 0.167.
 */
export function bm25RankToScore(rank: number): number {
  if (!Number.isFinite(rank)) {
    return 0;
  }
  return 1 / (1 - rank);
}

/**
 * Merge vector and keyword search results using Reciprocal Rank Fusion (RRF).
 * RRF scores each result as 1/(k + rank) summed across retrieval systems.
 * k=60 is the standard constant from the original RRF paper (Cormack et al. 2009).
 *
 * This avoids the score-magnitude suppression problem of weighted merging,
 * where keyword-only hits with small BM25 scores get multiplied down below thresholds.
 */
export function mergeHybridResults(
  vectorHits: SearchResult[],
  keywordHits: SearchResult[],
): SearchResult[] {
  const k = 60;

  const byId = new Map<
    string,
    { result: SearchResult; rrfScore: number; inVector: boolean; inKeyword: boolean }
  >();

  // Score vector hits by rank position
  for (let rank = 0; rank < vectorHits.length; rank++) {
    const r = vectorHits[rank];
    byId.set(r.chunk.id, {
      result: r,
      rrfScore: 1 / (k + rank + 1),
      inVector: true,
      inKeyword: false,
    });
  }

  // Score keyword hits by rank position, accumulate if already seen
  for (let rank = 0; rank < keywordHits.length; rank++) {
    const r = keywordHits[rank];
    const existing = byId.get(r.chunk.id);
    if (existing) {
      existing.rrfScore += 1 / (k + rank + 1);
      existing.inKeyword = true;
    } else {
      byId.set(r.chunk.id, {
        result: r,
        rrfScore: 1 / (k + rank + 1),
        inVector: false,
        inKeyword: true,
      });
    }
  }

  const merged: SearchResult[] = Array.from(byId.values()).map((entry) => ({
    chunk: entry.result.chunk,
    score: entry.rrfScore,
    matchType: entry.inVector && entry.inKeyword
      ? 'hybrid' as const
      : entry.inVector
        ? 'vector' as const
        : 'keyword' as const,
  }));

  return merged.sort((a, b) => b.score - a.score);
}
