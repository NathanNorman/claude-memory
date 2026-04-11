/**
 * Semantic chunker for conversation exchanges.
 *
 * Inspired by Memento's segmentation pipeline (segment.py), this module
 * replaces the budget-driven "fill until full" chunking with:
 *
 *   1. Heuristic boundary scoring (0–3) between exchanges
 *   2. Dynamic-programming segmentation that maximizes
 *      avg_boundary_score − λ × CV(chunk_sizes)
 *
 * The result: chunks that align with topic shifts rather than arbitrary
 * character budgets, while staying roughly equal-sized.
 */

import { hashText } from './types.js';
import type { ConversationExchange, ExchangeChunk } from './types.js';

// ---------------------------------------------------------------------------
// Heuristic boundary scorer
// ---------------------------------------------------------------------------

/** Signals extracted from a pair of adjacent exchanges. */
interface BoundarySignals {
  /** Did the set of tool types change? */
  toolTypeShift: boolean;
  /** Did the file paths being touched change? */
  filePathShift: boolean;
  /** Does the user message signal a new topic? */
  topicShiftPhrase: boolean;
  /** Time gap in seconds between the two exchanges (0 if unknown). */
  timeGapSeconds: number;
  /** Did the user ask a question (likely new intent)? */
  userAsksQuestion: boolean;
  /** Transition from reading to writing or vice versa. */
  readWriteTransition: boolean;
}

// Phrases that strongly signal the user is changing topics.
const TOPIC_SHIFT_PATTERNS = [
  /^(?:now\s+)?(?:let'?s|can you|please|could you)\s+(?:switch|move|look at|work on|focus on|do|try|change)/i,
  /^(?:different|another|new|next|unrelated)\s+(?:question|topic|task|thing|issue)/i,
  /^(?:ok(?:ay)?|alright|moving on|anyway)\s*[,.]?\s/i,
  /^(?:btw|by the way|also|separately|on a different note)/i,
  /^(?:forget|never\s*mind|scratch that|ignore)\s/i,
  /^(?:back to|returning to|going back to)/i,
];

// Tools that are "read" operations vs "write" operations.
const READ_TOOLS = new Set([
  'Read', 'Glob', 'Grep', 'Bash', 'WebFetch',
  'mcp__splunk__run_splunk_query', 'mcp__unified-memory__memory_search',
]);
const WRITE_TOOLS = new Set([
  'Write', 'Edit', 'NotebookEdit',
  'mcp__unified-memory__memory_write',
]);

/** Extract file paths mentioned in tool calls or message text. */
function extractFilePaths(ex: ConversationExchange): Set<string> {
  const paths = new Set<string>();
  // Match absolute paths in the combined text
  const combined = `${ex.userMessage}\n${ex.assistantMessage}`;
  const matches = combined.match(/(?:\/[\w./-]+){2,}/g);
  if (matches) {
    for (const m of matches) {
      // Normalize to directory level to reduce noise
      const dir = m.replace(/\/[^/]+$/, '');
      if (dir.length > 1) paths.add(dir);
    }
  }
  return paths;
}

function classifyToolMode(toolNames: string[]): 'read' | 'write' | 'mixed' | 'none' {
  let hasRead = false;
  let hasWrite = false;
  for (const t of toolNames) {
    if (READ_TOOLS.has(t)) hasRead = true;
    if (WRITE_TOOLS.has(t)) hasWrite = true;
  }
  if (hasRead && hasWrite) return 'mixed';
  if (hasRead) return 'read';
  if (hasWrite) return 'write';
  return 'none';
}

function extractSignals(
  prev: ConversationExchange,
  curr: ConversationExchange,
): BoundarySignals {
  // Tool type shift
  const prevTools = new Set(prev.toolCalls.map((t) => t.toolName));
  const currTools = new Set(curr.toolCalls.map((t) => t.toolName));
  const toolTypeShift =
    prevTools.size > 0 &&
    currTools.size > 0 &&
    ![...currTools].some((t) => prevTools.has(t));

  // File path shift
  const prevPaths = extractFilePaths(prev);
  const currPaths = extractFilePaths(curr);
  const filePathShift =
    prevPaths.size > 0 &&
    currPaths.size > 0 &&
    ![...currPaths].some((p) => prevPaths.has(p));

  // Topic shift phrase in user message
  const topicShiftPhrase = TOPIC_SHIFT_PATTERNS.some((p) =>
    p.test(curr.userMessage.trim()),
  );

  // Time gap
  let timeGapSeconds = 0;
  if (prev.timestamp && curr.timestamp) {
    const prevMs = new Date(prev.timestamp).getTime();
    const currMs = new Date(curr.timestamp).getTime();
    if (!isNaN(prevMs) && !isNaN(currMs)) {
      timeGapSeconds = Math.max(0, (currMs - prevMs) / 1000);
    }
  }

  // User asks a question
  const userAsksQuestion = /\?\s*$/.test(curr.userMessage.trim());

  // Read/write transition
  const prevMode = classifyToolMode(prev.toolCalls.map((t) => t.toolName));
  const currMode = classifyToolMode(curr.toolCalls.map((t) => t.toolName));
  const readWriteTransition =
    (prevMode === 'read' && currMode === 'write') ||
    (prevMode === 'write' && currMode === 'read');

  return {
    toolTypeShift,
    filePathShift,
    topicShiftPhrase,
    timeGapSeconds,
    userAsksQuestion,
    readWriteTransition,
  };
}

/**
 * Score the boundary between two adjacent exchanges on a 0–3 scale.
 *
 * - 0: No signal of topic change (continuation)
 * - 1: Weak signal (minor shift)
 * - 2: Good break (clear transition)
 * - 3: Strong break (major topic shift)
 */
export function scoreBoundary(
  prev: ConversationExchange,
  curr: ConversationExchange,
): number {
  const s = extractSignals(prev, curr);

  let score = 0;

  // Strong signals (each worth up to 1.0)
  if (s.topicShiftPhrase) score += 1.5;
  if (s.filePathShift) score += 1.0;
  if (s.timeGapSeconds > 300) score += 1.0; // >5 min gap
  else if (s.timeGapSeconds > 60) score += 0.5; // >1 min gap

  // Medium signals
  if (s.toolTypeShift) score += 0.5;
  if (s.readWriteTransition) score += 0.5;

  // Weak signal
  if (s.userAsksQuestion && score < 0.5) score += 0.25;

  return Math.min(3.0, score);
}

/**
 * Score all boundaries in a sequence of exchanges.
 * Returns array of length (exchanges.length - 1).
 * scores[i] = boundary score between exchanges[i] and exchanges[i+1].
 */
export function scoreAllBoundaries(
  exchanges: ConversationExchange[],
): number[] {
  if (exchanges.length <= 1) return [];
  const scores: number[] = [];
  for (let i = 0; i < exchanges.length - 1; i++) {
    scores.push(scoreBoundary(exchanges[i]!, exchanges[i + 1]!));
  }
  return scores;
}

// ---------------------------------------------------------------------------
// DP Segmenter (ported from Memento's segment_variance_dp)
// ---------------------------------------------------------------------------

/** Rough token count: chars / 4 (same heuristic as existing chunker). */
function roughTokenCount(text: string): number {
  return Math.ceil(text.length / 4);
}

function exchangeTokenCount(ex: ConversationExchange): number {
  return roughTokenCount(ex.userMessage) + roughTokenCount(ex.assistantMessage);
}

interface DpEntry {
  objective: number;
  totalScore: number;
  sumSizes: number;
  sumSizesSq: number;
  prevJ: number;
}

/**
 * Find the optimal segmentation of exchanges into chunks using DP.
 *
 * Maximizes: avg_boundary_score − varianceWeight × CV(chunk_token_sizes)
 *
 * @param exchanges    - The conversation exchanges
 * @param scores       - Boundary scores (length = exchanges.length - 1)
 * @param minChunkTokens - Minimum tokens per chunk (default 200)
 * @param maxChunkTokens - Maximum tokens per chunk (default 2400, ~6x current 400)
 * @param varianceWeight - Penalty for uneven chunk sizes (default 0.5)
 * @returns Array of [startIdx, endIdx] inclusive ranges into exchanges[]
 */
export function segmentVarianceDp(
  exchanges: ConversationExchange[],
  scores: number[],
  options?: {
    minChunkTokens?: number;
    maxChunkTokens?: number;
    varianceWeight?: number;
    minChunks?: number;
    maxChunks?: number;
  },
): Array<[number, number]> {
  const n = exchanges.length;
  if (n === 0) return [];
  if (n === 1) return [[0, 0]];

  const minChunkTokens = options?.minChunkTokens ?? 200;
  const maxChunkTokens = options?.maxChunkTokens ?? 2400;
  const varianceWeight = options?.varianceWeight ?? 0.5;
  const minChunks = options?.minChunks ?? 1;
  const maxChunks = options?.maxChunks ?? n;

  // Precompute token counts per exchange
  const tokenCounts = exchanges.map(exchangeTokenCount);

  // Precompute prefix sums for fast range queries
  const prefixTokens = new Array<number>(n + 1);
  prefixTokens[0] = 0;
  for (let i = 0; i < n; i++) {
    prefixTokens[i + 1] = prefixTokens[i]! + tokenCounts[i]!;
  }
  function rangeTokens(start: number, end: number): number {
    return prefixTokens[end + 1]! - prefixTokens[start]!;
  }

  // dp[i][k] = best way to partition exchanges[0..i-1] into k chunks
  const dp: Array<Array<DpEntry | null>> = new Array(n + 1);
  for (let i = 0; i <= n; i++) {
    dp[i] = new Array(maxChunks + 1).fill(null);
  }

  // Base case: 0 exchanges, 0 chunks
  dp[0]![0] = {
    objective: 0,
    totalScore: 0,
    sumSizes: 0,
    sumSizesSq: 0,
    prevJ: -1,
  };

  // Fill DP table
  for (let i = 1; i <= n; i++) {
    for (let k = 1; k <= Math.min(i, maxChunks); k++) {
      let best: DpEntry | null = null;

      // Try all possible previous chunk endings
      for (let j = Math.max(0, i - 15); j < i; j++) {
        // 15-exchange max chunk size cap
        const prev = dp[j]![k - 1];
        if (!prev) continue;

        // New chunk covers exchanges[j..i-1]
        const chunkTokens = rangeTokens(j, i - 1);

        // Enforce size constraints
        if (chunkTokens < minChunkTokens && i < n) continue; // allow small last chunk
        if (chunkTokens > maxChunkTokens && (i - j) > 1) continue;

        // Boundary score: the boundary crossed to enter this chunk
        // That's between exchange j-1 and exchange j
        let boundaryScore = 0;
        if (j > 0 && j - 1 < scores.length) {
          boundaryScore = scores[j - 1]!;
        }

        const newTotalScore = prev.totalScore + boundaryScore;
        const newSumSizes = prev.sumSizes + chunkTokens;
        const newSumSizesSq = prev.sumSizesSq + chunkTokens * chunkTokens;

        // Calculate objective
        const avgScore = newTotalScore / k;
        const meanSize = newSumSizes / k;
        const variance = newSumSizesSq / k - meanSize * meanSize;
        const stdDev = Math.sqrt(Math.max(0, variance));
        const cv = meanSize > 0 ? stdDev / meanSize : 0;

        const objective = avgScore - varianceWeight * cv;

        if (best === null || objective > best.objective) {
          best = {
            objective,
            totalScore: newTotalScore,
            sumSizes: newSumSizes,
            sumSizesSq: newSumSizesSq,
            prevJ: j,
          };
        }
      }

      if (best !== null) {
        dp[i]![k] = best;
      }
    }
  }

  // Find best k in [minChunks, maxChunks]
  let bestK = -1;
  let bestObj = -Infinity;
  for (let k = minChunks; k <= maxChunks; k++) {
    const entry = dp[n]![k];
    if (entry !== null && entry.objective > bestObj) {
      bestObj = entry.objective;
      bestK = k;
    }
  }

  if (bestK < 0) {
    // Fallback: entire conversation as one chunk
    return [[0, n - 1]];
  }

  // Reconstruct solution
  const segments: Array<[number, number]> = [];
  let i = n;
  let k = bestK;
  while (k > 0) {
    const entry = dp[i]![k]!;
    segments.push([entry.prevJ, i - 1]);
    i = entry.prevJ;
    k--;
  }
  segments.reverse();
  return segments;
}

// ---------------------------------------------------------------------------
// Public API: semantic exchange chunking
// ---------------------------------------------------------------------------

/**
 * Format a single exchange as text for embedding.
 */
function formatExchange(ex: ConversationExchange): string {
  let text = `User: ${ex.userMessage}`;
  if (ex.assistantMessage) {
    text += `\n\nAssistant: ${ex.assistantMessage}`;
  }
  return text;
}

/**
 * Chunk conversation exchanges using semantic boundary detection + DP segmentation.
 *
 * This is a drop-in replacement for chunkExchanges() from chunker.ts.
 * Instead of filling chunks to a budget, it:
 *   1. Scores every inter-exchange boundary (0–3) using heuristics
 *   2. Runs DP to find optimal segmentation balancing semantic coherence and size
 *   3. Returns ExchangeChunk[] in the same format as the original
 */
export function chunkExchangesSemantic(
  exchanges: ConversationExchange[],
  options?: {
    minChunkTokens?: number;
    maxChunkTokens?: number;
    varianceWeight?: number;
  },
): ExchangeChunk[] {
  if (exchanges.length === 0) return [];

  // Step 1: Score boundaries
  const scores = scoreAllBoundaries(exchanges);

  // Step 2: DP segmentation
  const segments = segmentVarianceDp(exchanges, scores, {
    minChunkTokens: options?.minChunkTokens ?? 150,
    maxChunkTokens: options?.maxChunkTokens ?? 1600,
    varianceWeight: options?.varianceWeight ?? 0.3,
  });

  // Step 3: Build ExchangeChunk objects
  const chunks: ExchangeChunk[] = [];
  for (const [start, end] of segments) {
    const segExchanges = exchanges.slice(start, end + 1);

    // Collect tool names
    const toolSet = new Set<string>();
    for (const ex of segExchanges) {
      for (const tc of ex.toolCalls) {
        toolSet.add(tc.toolName);
      }
    }
    const toolNames = Array.from(toolSet).sort();

    // Build text
    const textParts = segExchanges.map(formatExchange);
    let text = textParts.join('\n\n---\n\n');
    if (toolNames.length > 0) {
      text += `\n\nTools: ${toolNames.join(', ')}`;
    }

    chunks.push({
      exchanges: segExchanges,
      startLine: segExchanges[0]!.lineStart,
      endLine: segExchanges[segExchanges.length - 1]!.lineEnd,
      text,
      toolNames,
      hash: hashText(text),
    });
  }

  return chunks;
}
