import { hashText } from './types.js';
import type { ConversationExchange, ExchangeChunk } from './types.js';

export interface RawChunk {
  startLine: number;
  endLine: number;
  text: string;
  hash: string;
}

export function chunkMarkdown(
  content: string,
  options?: { tokens?: number; overlap?: number },
): RawChunk[] {
  const tokens = options?.tokens ?? 400;
  const overlap = options?.overlap ?? 80;

  const lines = content.split('\n');
  if (lines.length === 0) {
    return [];
  }

  const maxChars = Math.max(32, tokens * 4);
  const overlapChars = Math.max(0, overlap * 4);
  const chunks: RawChunk[] = [];

  let current: Array<{ line: string; lineNo: number }> = [];
  let currentChars = 0;

  const flush = (): void => {
    if (current.length === 0) {
      return;
    }
    const firstEntry = current[0];
    const lastEntry = current[current.length - 1];
    if (!firstEntry || !lastEntry) {
      return;
    }
    const text = current.map((entry) => entry.line).join('\n');
    const startLine = firstEntry.lineNo;
    const endLine = lastEntry.lineNo;
    chunks.push({
      startLine,
      endLine,
      text,
      hash: hashText(text),
    });
  };

  const carryOverlap = (): void => {
    if (overlapChars <= 0 || current.length === 0) {
      current = [];
      currentChars = 0;
      return;
    }
    let acc = 0;
    const kept: Array<{ line: string; lineNo: number }> = [];
    for (let i = current.length - 1; i >= 0; i -= 1) {
      const entry = current[i];
      if (!entry) {
        continue;
      }
      acc += entry.line.length + 1;
      kept.unshift(entry);
      if (acc >= overlapChars) {
        break;
      }
    }
    current = kept;
    currentChars = kept.reduce((sum, entry) => sum + entry.line.length + 1, 0);
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i] ?? '';
    const lineNo = i + 1;
    const segments: string[] = [];
    if (line.length === 0) {
      segments.push('');
    } else {
      for (let start = 0; start < line.length; start += maxChars) {
        segments.push(line.slice(start, start + maxChars));
      }
    }
    for (const segment of segments) {
      const lineSize = segment.length + 1;
      if (currentChars + lineSize > maxChars && current.length > 0) {
        flush();
        carryOverlap();
      }
      current.push({ line: segment, lineNo });
      currentChars += lineSize;
    }
  }
  flush();
  return chunks;
}

// --- Exchange-Aware Chunking ---

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
 * Chunk conversation exchanges into groups that never split mid-exchange.
 * Each chunk stays within ~maxTokens (estimated as chars/4).
 * If a single exchange exceeds the limit, it gets its own chunk.
 */
export function chunkExchanges(
  exchanges: ConversationExchange[],
  options?: { maxTokens?: number },
): ExchangeChunk[] {
  const maxTokens = options?.maxTokens ?? 400;
  const maxChars = Math.max(32, maxTokens * 4);
  const chunks: ExchangeChunk[] = [];

  let currentExchanges: ConversationExchange[] = [];
  let currentTexts: string[] = [];
  let currentChars = 0;

  function flush(): void {
    if (currentExchanges.length === 0) return;

    // Collect unique tool names across all exchanges in this chunk
    const toolSet = new Set<string>();
    for (const ex of currentExchanges) {
      for (const tc of ex.toolCalls) {
        toolSet.add(tc.toolName);
      }
    }
    const toolNames = Array.from(toolSet).sort();

    let text = currentTexts.join('\n\n---\n\n');
    if (toolNames.length > 0) {
      text += `\n\nTools: ${toolNames.join(', ')}`;
    }

    chunks.push({
      exchanges: currentExchanges,
      startLine: currentExchanges[0]!.lineStart,
      endLine: currentExchanges[currentExchanges.length - 1]!.lineEnd,
      text,
      toolNames,
      hash: hashText(text),
    });

    currentExchanges = [];
    currentTexts = [];
    currentChars = 0;
  }

  for (const ex of exchanges) {
    const formatted = formatExchange(ex);
    const exChars = formatted.length;

    // If adding this exchange would exceed limit and we have content, flush first
    if (currentChars + exChars > maxChars && currentExchanges.length > 0) {
      flush();
    }

    currentExchanges.push(ex);
    currentTexts.push(formatted);
    currentChars += exChars;
  }

  flush();
  return chunks;
}
