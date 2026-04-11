/**
 * Semantic markdown chunker.
 *
 * Three-stage pipeline mirroring the conversation semantic chunker:
 *   1. Parse markdown into atomic units (headings, code blocks, lists, etc.)
 *   2. Score boundaries between adjacent units using structural signals
 *   3. Segment via DP (reusing segmentVarianceDp from semantic-chunker.ts)
 *
 * Drop-in replacement for chunkMarkdown() — returns the same RawChunk[] type.
 */

import { hashText } from './types.js';
import { segmentVarianceDp } from './semantic-chunker.js';
import type { ConversationExchange } from './types.js';
import type { RawChunk } from './chunker.js';

// ---------------------------------------------------------------------------
// Stage 1: Parse markdown into atomic units
// ---------------------------------------------------------------------------

type UnitType = 'heading' | 'code_block' | 'list' | 'thematic_break' | 'frontmatter' | 'table' | 'paragraph';

export interface MarkdownUnit {
  type: UnitType;
  lines: string[];
  startLine: number; // 1-indexed
  endLine: number;   // 1-indexed, inclusive
  text: string;
  tokenCount: number;
  /** For heading units, the heading level (1-6). Undefined for others. */
  headingLevel?: number;
}

function roughTokenCount(text: string): number {
  return Math.ceil(text.length / 4);
}

export function parseMarkdownUnits(content: string): MarkdownUnit[] {
  if (!content || content.trim().length === 0) return [];

  const lines = content.split('\n');
  const units: MarkdownUnit[] = [];

  let i = 0;

  function makeUnit(type: UnitType, unitLines: string[], startIdx: number, headingLevel?: number): MarkdownUnit {
    const text = unitLines.join('\n');
    const unit: MarkdownUnit = {
      type,
      lines: unitLines,
      startLine: startIdx + 1,
      endLine: startIdx + unitLines.length,
      text,
      tokenCount: roughTokenCount(text),
    };
    if (headingLevel !== undefined) unit.headingLevel = headingLevel;
    return unit;
  }

  // YAML frontmatter at file start
  if (lines[0] === '---' && lines.length > 1) {
    let endIdx = -1;
    for (let j = 1; j < lines.length; j++) {
      if (lines[j] === '---') {
        endIdx = j;
        break;
      }
    }
    if (endIdx > 0) {
      units.push(makeUnit('frontmatter', lines.slice(0, endIdx + 1), 0));
      i = endIdx + 1;
    }
  }

  while (i < lines.length) {
    const line = lines[i]!;

    // Skip blank lines between units
    if (line.trim() === '') {
      i++;
      continue;
    }

    // Thematic break: ---, ***, ___
    if (/^(\s{0,3})(---+|\*\*\*+|___+)\s*$/.test(line)) {
      units.push(makeUnit('thematic_break', [line], i));
      i++;
      continue;
    }

    // Fenced code block
    const fenceMatch = line.match(/^(\s{0,3})(```+|~~~+)/);
    if (fenceMatch) {
      const fence = fenceMatch[2]!;
      const fenceChar = fence[0]!;
      const fenceLen = fence.length;
      const startIdx = i;
      const blockLines = [line];
      i++;
      let closed = false;
      while (i < lines.length) {
        blockLines.push(lines[i]!);
        const closingMatch = lines[i]!.match(/^(\s{0,3})(```+|~~~+)\s*$/);
        if (closingMatch && closingMatch[2]![0] === fenceChar && closingMatch[2]!.length >= fenceLen) {
          closed = true;
          i++;
          break;
        }
        i++;
      }
      // Unclosed fence: treat as paragraph (edge case per task 1.3)
      units.push(makeUnit(closed ? 'code_block' : 'paragraph', blockLines, startIdx));
      continue;
    }

    // Table: consecutive lines starting with |
    if (line.trimStart().startsWith('|')) {
      const startIdx = i;
      const tableLines: string[] = [];
      while (i < lines.length && lines[i]!.trimStart().startsWith('|')) {
        tableLines.push(lines[i]!);
        i++;
      }
      units.push(makeUnit('table', tableLines, startIdx));
      continue;
    }

    // List run: consecutive lines starting with -, *, or N.
    const listMatch = line.match(/^(\s*)([-*]|\d+\.)\s/);
    if (listMatch) {
      const startIdx = i;
      const listLines: string[] = [];
      while (i < lines.length) {
        const l = lines[i]!;
        if (l.trim() === '') {
          // Blank line: check if next non-blank line continues the list
          const nextNonBlank = lines.slice(i + 1).findIndex(ll => ll.trim() !== '');
          if (nextNonBlank >= 0) {
            const nextLine = lines[i + 1 + nextNonBlank]!;
            // Continuation if indented or is another list item
            if (/^\s+([-*]|\d+\.)\s/.test(nextLine) || /^([-*]|\d+\.)\s/.test(nextLine) || /^\s{2,}/.test(nextLine)) {
              listLines.push(l);
              i++;
              continue;
            }
          }
          break;
        }
        // List item or indented continuation
        if (/^(\s*)([-*]|\d+\.)\s/.test(l) || (listLines.length > 0 && /^\s{2,}/.test(l))) {
          listLines.push(l);
          i++;
        } else {
          break;
        }
      }
      units.push(makeUnit('list', listLines, startIdx));
      continue;
    }

    // Heading: # through ######
    const headingMatch = line.match(/^(#{1,6})\s+(.*)$/);
    if (headingMatch) {
      const level = headingMatch[1]!.length;
      const startIdx = i;
      const headingLines = [line];
      i++;
      // Collect content lines until next heading, blank-line-separated paragraph break, or thematic break
      while (i < lines.length) {
        const nextLine = lines[i]!;
        // Stop at next heading
        if (/^#{1,6}\s+/.test(nextLine)) break;
        // Stop at thematic break
        if (/^(\s{0,3})(---+|\*\*\*+|___+)\s*$/.test(nextLine)) break;
        // Stop at double blank line (paragraph break)
        if (nextLine.trim() === '' && i + 1 < lines.length && lines[i + 1]!.trim() === '') break;
        // Stop at single blank line followed by non-continuation content
        if (nextLine.trim() === '') {
          // Look ahead: if next non-blank line is a heading, list, code fence, table, stop here
          let peek = i + 1;
          while (peek < lines.length && lines[peek]!.trim() === '') peek++;
          if (peek < lines.length) {
            const peekLine = lines[peek]!;
            if (/^#{1,6}\s+/.test(peekLine) || /^(\s{0,3})(```+|~~~+)/.test(peekLine) ||
                /^(\s*)([-*]|\d+\.)\s/.test(peekLine) || peekLine.trimStart().startsWith('|') ||
                /^(\s{0,3})(---+|\*\*\*+|___+)\s*$/.test(peekLine)) {
              break;
            }
          }
          // Include the blank line and continue (prose continuation under heading)
          headingLines.push(nextLine);
          i++;
          continue;
        }
        headingLines.push(nextLine);
        i++;
      }
      units.push(makeUnit('heading', headingLines, startIdx, level));
      continue;
    }

    // Paragraph: contiguous non-blank lines that don't match any special type
    {
      const startIdx = i;
      const paraLines: string[] = [];
      while (i < lines.length) {
        const l = lines[i]!;
        if (l.trim() === '') break;
        if (/^#{1,6}\s+/.test(l)) break;
        if (/^(\s{0,3})(```+|~~~+)/.test(l)) break;
        if (/^(\s{0,3})(---+|\*\*\*+|___+)\s*$/.test(l)) break;
        if (l.trimStart().startsWith('|') && paraLines.length > 0) break;
        if (/^(\s*)([-*]|\d+\.)\s/.test(l) && paraLines.length > 0) break;
        paraLines.push(l);
        i++;
      }
      if (paraLines.length > 0) {
        units.push(makeUnit('paragraph', paraLines, startIdx));
      }
    }
  }

  return units;
}

// ---------------------------------------------------------------------------
// Stage 2: Score boundaries between adjacent units
// ---------------------------------------------------------------------------

export function scoreMarkdownBoundary(prev: MarkdownUnit, curr: MarkdownUnit): number {
  let score = 0;

  // Heading boundary: current unit is a heading
  if (curr.type === 'heading') {
    score += 1.5;
  }

  // Thematic break: either unit is a thematic break
  if (curr.type === 'thematic_break' || prev.type === 'thematic_break') {
    score += 1.5;
  }

  // Heading level change (decrease = wider scope change)
  if (prev.type === 'heading' && curr.type === 'heading' && prev.headingLevel !== undefined && curr.headingLevel !== undefined) {
    if (curr.headingLevel < prev.headingLevel) {
      score += 1.0; // Going to a higher-level heading
    } else if (curr.headingLevel !== prev.headingLevel) {
      score += 0.5; // Any heading level change
    }
  }

  // Content type shift
  if (prev.type !== curr.type && curr.type !== 'thematic_break' && prev.type !== 'thematic_break') {
    score += 0.5;
  }

  // Blank line separation: check if there's a gap between prev.endLine and curr.startLine
  const gap = curr.startLine - prev.endLine;
  if (gap > 2) {
    score += 0.5; // Double+ blank line
  } else if (gap > 1) {
    score += 0.25; // Single blank line
  }

  return Math.min(3.0, score);
}

export function scoreAllMarkdownBoundaries(units: MarkdownUnit[]): number[] {
  if (units.length <= 1) return [];
  const scores: number[] = [];
  for (let i = 0; i < units.length - 1; i++) {
    scores.push(scoreMarkdownBoundary(units[i]!, units[i + 1]!));
  }
  return scores;
}

// ---------------------------------------------------------------------------
// Stage 3: DP segmentation adapter
// ---------------------------------------------------------------------------

/**
 * Create adapter objects that satisfy what segmentVarianceDp reads from
 * ConversationExchange: userMessage (for token counting) and assistantMessage.
 * The DP internally calls roughTokenCount(ex.userMessage) + roughTokenCount(ex.assistantMessage).
 */
function unitToFakeExchange(unit: MarkdownUnit): ConversationExchange {
  return {
    userMessage: unit.text,
    assistantMessage: '',
    lineStart: unit.startLine,
    lineEnd: unit.endLine,
    toolCalls: [],
  };
}

// ---------------------------------------------------------------------------
// Stage 4: Public API
// ---------------------------------------------------------------------------

export function chunkMarkdownSemantic(content: string): RawChunk[] {
  if (!content || content.trim().length === 0) return [];

  // Stage 1: Parse into atomic units
  const units = parseMarkdownUnits(content);
  if (units.length === 0) return [];

  // Single unit → single chunk
  if (units.length === 1) {
    const text = units[0]!.text;
    return [{
      startLine: units[0]!.startLine,
      endLine: units[0]!.endLine,
      text,
      hash: hashText(text),
    }];
  }

  // Stage 2: Score boundaries
  const scores = scoreAllMarkdownBoundaries(units);

  // Stage 3: DP segmentation
  const fakeExchanges = units.map(unitToFakeExchange);
  const segments = segmentVarianceDp(fakeExchanges, scores, {
    minChunkTokens: 100,
    maxChunkTokens: 2000,
    varianceWeight: 0.3,
  });

  // Stage 4: Map segments back to RawChunk[]
  const chunks: RawChunk[] = [];
  for (const [startIdx, endIdx] of segments) {
    const segUnits = units.slice(startIdx, endIdx + 1);
    const text = segUnits.map(u => u.text).join('\n\n');
    chunks.push({
      startLine: segUnits[0]!.startLine,
      endLine: segUnits[segUnits.length - 1]!.endLine,
      text,
      hash: hashText(text),
    });
  }

  return chunks;
}
