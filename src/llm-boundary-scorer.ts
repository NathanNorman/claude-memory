/**
 * LLM-based boundary scorer for conversation exchanges.
 *
 * Adapts Memento's score.py pattern: two-pass coprime windows (16, 11)
 * with per-pair caching and RRF-style averaging.
 *
 * Drop-in alternative to scoreAllBoundaries() — returns number[] on 0-3 scale.
 */

import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Database as DatabaseType } from 'better-sqlite3';
import { hashText } from './types.js';
import type { ConversationExchange } from './types.js';
import {
  callChatCompletion,
  retryWithBackoff,
  stripCodeFences,
  type LlmConfig,
  type ChatMessage,
} from './llm-client.js';
import {
  getCachedBoundaryScore,
  setCachedBoundaryScore,
} from './db.js';

// --- Prompt Loading ---

const __dirname = dirname(fileURLToPath(import.meta.url));

let systemPrompt: string | null = null;
let userTemplate: string | null = null;

function loadPrompts(): { system: string; user: string } {
  if (!systemPrompt || !userTemplate) {
    const promptDir = join(__dirname, 'prompts');
    systemPrompt = readFileSync(join(promptDir, 'boundary-score-system.txt'), 'utf-8').trim();
    userTemplate = readFileSync(join(promptDir, 'boundary-score-user.txt'), 'utf-8').trim();
  }
  return { system: systemPrompt, user: userTemplate };
}

// --- Scorer Version ---

const SCORER_VERSION = 'llm-v1';

// --- Exchange Pair Hashing ---

function hashExchangePair(a: ConversationExchange, b: ConversationExchange): string {
  return hashText(a.userMessage + a.assistantMessage + b.userMessage + b.assistantMessage);
}

// --- Window Formatting ---

function formatExchangesWithBoundaries(
  exchanges: ConversationExchange[],
  startIdx: number,
): string {
  const parts: string[] = [];
  for (let i = 0; i < exchanges.length; i++) {
    const ex = exchanges[i]!;
    parts.push(`[User]: ${ex.userMessage}`);
    if (ex.assistantMessage) {
      parts.push(`[Assistant]: ${ex.assistantMessage}`);
    }
    if (i < exchanges.length - 1) {
      parts.push(`\n<<<BOUNDARY_${startIdx + i}>>>\n`);
    }
  }
  return parts.join('\n');
}

// --- LLM Boundary Scorer Class ---

export interface LlmScorerConfig {
  llmConfig: LlmConfig;
  singlePass?: boolean;
}

export class LlmBoundaryScorer {
  private config: LlmConfig;
  private singlePass: boolean;
  private db: DatabaseType | null;
  public cacheHits = 0;
  public cacheMisses = 0;

  constructor(config: LlmScorerConfig, db?: DatabaseType) {
    this.config = config.llmConfig;
    this.singlePass = config.singlePass ?? false;
    this.db = db ?? null;
  }

  /**
   * Score a single window of exchanges via LLM.
   * Returns array of boundary scores for this window.
   */
  async scoreWindow(
    exchanges: ConversationExchange[],
    globalStartIdx: number,
  ): Promise<number[]> {
    const boundaryCount = exchanges.length - 1;
    if (boundaryCount <= 0) return [];

    const prompts = loadPrompts();
    const text = formatExchangesWithBoundaries(exchanges, globalStartIdx);
    const userContent = prompts.user
      .replace(/\{count\}/g, String(boundaryCount))
      .replace(/\{text\}/g, text);

    const messages: ChatMessage[] = [
      { role: 'system', content: prompts.system.replace(/\{count\}/g, String(boundaryCount)) },
      { role: 'user', content: userContent },
    ];

    const response = await callChatCompletion(this.config, messages, {
      temperature: 0.0,
      maxTokens: 1024,
    });

    // Parse JSON response
    const cleaned = stripCodeFences(response);
    let parsed: { scores?: number[] };
    try {
      parsed = JSON.parse(cleaned);
    } catch {
      throw new Error(`Failed to parse LLM response as JSON: ${cleaned.slice(0, 200)}`);
    }

    if (!parsed.scores || !Array.isArray(parsed.scores)) {
      throw new Error(`LLM response missing scores array: ${cleaned.slice(0, 200)}`);
    }

    // Validate and clamp scores
    let scores = parsed.scores.map((s) => Math.min(3.0, Math.max(0.0, Number(s) || 0)));

    // Handle count mismatch
    if (scores.length !== boundaryCount) {
      if (scores.length > boundaryCount) {
        scores = scores.slice(0, boundaryCount);
      } else {
        while (scores.length < boundaryCount) {
          scores.push(0);
        }
      }
    }

    return scores;
  }

  /**
   * Score all boundaries using a specific window size.
   * Uses cache for each exchange pair.
   */
  async scoreWithWindow(
    exchanges: ConversationExchange[],
    windowSize: number,
  ): Promise<number[] | null> {
    const n = exchanges.length;
    if (n <= 1) return [];

    const allScores = new Array<number>(n - 1).fill(0);
    const scoreCounts = new Array<number>(n - 1).fill(0);
    let windowCount = 0;
    let failedWindows = 0;

    for (let start = 0; start < n - 1; start += windowSize - 1) {
      const end = Math.min(start + windowSize, n);
      const windowExchanges = exchanges.slice(start, end);
      if (windowExchanges.length < 2) break;

      windowCount++;
      const boundaryCount = windowExchanges.length - 1;

      // Check cache for all pairs in this window
      let allCached = true;
      const cachedScores: (number | null)[] = [];

      if (this.db) {
        for (let i = 0; i < boundaryCount; i++) {
          const pairHash = hashExchangePair(windowExchanges[i]!, windowExchanges[i + 1]!);
          const cached = getCachedBoundaryScore(this.db, pairHash, SCORER_VERSION);
          cachedScores.push(cached);
          if (cached === null) {
            allCached = false;
          }
        }
      } else {
        allCached = false;
      }

      let windowScores: number[];

      if (allCached) {
        windowScores = cachedScores.map((s) => s!);
        this.cacheHits += boundaryCount;
      } else {
        this.cacheMisses += boundaryCount;
        try {
          windowScores = await retryWithBackoff(
            () => this.scoreWindow(windowExchanges, start),
            5,
            1000,
          );

          // Write to cache
          if (this.db) {
            for (let i = 0; i < windowScores.length; i++) {
              const pairHash = hashExchangePair(windowExchanges[i]!, windowExchanges[i + 1]!);
              setCachedBoundaryScore(this.db, pairHash, SCORER_VERSION, windowScores[i]!);
            }
          }
        } catch (err) {
          process.stderr.write(
            `[llm-scorer] Window ${start}-${end} failed after retries: ${err instanceof Error ? err.message : err}\n`,
          );
          failedWindows++;
          windowScores = new Array(boundaryCount).fill(0);
        }
      }

      // Merge window scores into global array
      for (let i = 0; i < windowScores.length; i++) {
        const globalIdx = start + i;
        if (globalIdx < allScores.length) {
          allScores[globalIdx] += windowScores[i]!;
          scoreCounts[globalIdx]++;
        }
      }
    }

    // If ALL windows failed, signal total failure
    if (failedWindows > 0 && failedWindows === windowCount) {
      return null;
    }

    // Average where multiple windows covered the same boundary
    return allScores.map((s, i) => (scoreCounts[i]! > 0 ? s / scoreCounts[i]! : 0));
  }

  /**
   * Score all boundaries using two-pass coprime windows (16, 11).
   * Returns number[] of length exchanges.length - 1, or null on total failure.
   */
  async scoreAll(exchanges: ConversationExchange[]): Promise<number[] | null> {
    if (exchanges.length <= 1) return [];
    if (exchanges.length === 2) {
      // Single boundary — use one window
      try {
        const scores = await retryWithBackoff(
          () => this.scoreWindow(exchanges, 0),
          5,
          1000,
        );
        return scores;
      } catch {
        return null;
      }
    }

    try {
      const pass1 = await this.scoreWithWindow(exchanges, 16);
      if (pass1 === null) return null;

      if (this.singlePass) {
        return pass1;
      }

      const pass2 = await this.scoreWithWindow(exchanges, 11);
      if (pass2 === null) return pass1; // Use pass1 if pass2 fails entirely

      // Average the two passes
      const averaged = pass1.map((s, i) => (s + pass2[i]!) / 2);
      return averaged;
    } catch (err) {
      process.stderr.write(
        `[llm-scorer] Total failure for file: ${err instanceof Error ? err.message : err}\n`,
      );
      return null;
    }
  }
}
