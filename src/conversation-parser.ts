import { readFileSync, statSync } from 'node:fs';
import type { ConversationExchange, ToolCallInfo } from './types.js';

// --- Constants ---

/** Skip files larger than 20MB */
export const MAX_FILE_BYTES = 20 * 1024 * 1024;

/** Record types to skip entirely (no useful text) */
const SKIP_TYPES = new Set([
  'progress',
  'queue-operation',
  'file-history-snapshot',
]);

/** Content block types to skip within assistant messages */
const SKIP_BLOCK_TYPES = new Set([
  'tool_use',
  'tool_result',
  'thinking',
]);

// --- Types ---

interface JsonlRecord {
  type?: string;
  sessionId?: string;
  cwd?: string;
  timestamp?: string;
  message?: {
    role?: string;
    content?: string | ContentBlock[];
  };
}

interface ContentBlock {
  type: string;
  text?: string;
}

// --- Parser ---

/**
 * Parse a conversation archive JSONL file and extract user/assistant text
 * into structured markdown suitable for chunking and embedding.
 *
 * @param absolutePath - Full path to the .jsonl file
 * @param projectDir  - The project directory name (e.g. "-Users-jdoe-my-project")
 * @returns Structured markdown text, or null if file is empty/unparseable
 */
export function parseConversationJsonl(absolutePath: string, projectDir: string): string | null {
  // Size guard
  const stat = statSync(absolutePath);
  if (stat.size > MAX_FILE_BYTES || stat.size === 0) {
    return null;
  }

  const raw = readFileSync(absolutePath, 'utf-8');
  const lines = raw.split('\n');

  let sessionId: string | undefined;
  let cwd: string | undefined;
  let timestamp: string | undefined;
  const segments: string[] = [];

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    let rec: JsonlRecord;
    try {
      rec = JSON.parse(trimmed) as JsonlRecord;
    } catch {
      continue; // skip malformed lines
    }

    const recType = rec.type;
    if (!recType || SKIP_TYPES.has(recType)) continue;

    // Extract metadata from first record that has it
    if (!sessionId && rec.sessionId) sessionId = rec.sessionId;
    if (!cwd && rec.cwd) cwd = rec.cwd;
    if (!timestamp && rec.timestamp) timestamp = rec.timestamp;

    // Extract text from user messages
    if (recType === 'user') {
      const text = extractMessageText(rec.message);
      if (text) {
        segments.push(`[User]: ${text}`);
      }
      continue;
    }

    // Extract text from assistant messages
    if (recType === 'assistant') {
      const text = extractMessageText(rec.message);
      if (text) {
        segments.push(`[Assistant]: ${text}`);
      }
      continue;
    }

    // Extract text from summary records
    if (recType === 'summary') {
      const text = extractMessageText(rec.message);
      if (text) {
        segments.push(`[Summary]: ${text}`);
      }
      continue;
    }
  }

  if (segments.length === 0) {
    return null;
  }

  // Build structured header
  const header = [
    `# Session: ${sessionId ?? 'unknown'}`,
    `Project: ${projectDir}${cwd ? ` | CWD: ${cwd}` : ''}`,
    timestamp ? `Date: ${timestamp.slice(0, 10)}` : '',
  ].filter(Boolean).join('\n');

  return `${header}\n\n${segments.join('\n\n')}`;
}

/**
 * Extract plain text from a message's content field.
 * Handles both string content and array-of-blocks content.
 */
function extractMessageText(message: JsonlRecord['message']): string | null {
  if (!message?.content) return null;

  const content = message.content;

  // Simple string content (user messages)
  if (typeof content === 'string') {
    const trimmed = content.trim();
    return trimmed.length > 0 ? trimmed : null;
  }

  // Array of content blocks (assistant messages)
  if (Array.isArray(content)) {
    const textParts: string[] = [];
    for (const block of content) {
      if (typeof block !== 'object' || !block) continue;
      if (SKIP_BLOCK_TYPES.has(block.type)) continue;
      if (block.type === 'text' && typeof block.text === 'string') {
        const trimmed = block.text.trim();
        if (trimmed.length > 0) {
          textParts.push(trimmed);
        }
      }
    }
    return textParts.length > 0 ? textParts.join('\n\n') : null;
  }

  return null;
}

// --- Exchange-Level Parser ---

interface ToolUseBlock {
  type: 'tool_use';
  name?: string;
}

/**
 * Extract tool_use block names from an assistant message's content array.
 */
function extractToolCalls(content: string | ContentBlock[] | undefined): ToolCallInfo[] {
  if (!content || typeof content === 'string' || !Array.isArray(content)) return [];
  const tools: ToolCallInfo[] = [];
  for (const block of content) {
    if (typeof block === 'object' && block && block.type === 'tool_use') {
      const name = (block as unknown as ToolUseBlock).name;
      if (name) {
        tools.push({ toolName: name });
      }
    }
  }
  return tools;
}

/**
 * Parse a conversation JSONL file into structured exchanges (user+assistant pairs).
 * Each exchange groups a user message with its subsequent assistant response(s).
 * Tool calls are extracted from assistant messages.
 *
 * @param absolutePath - Full path to the .jsonl file
 * @returns Structured exchanges + metadata, or null if empty/unparseable
 */
export function parseConversationExchanges(absolutePath: string): {
  exchanges: ConversationExchange[];
  metadata: { sessionId?: string; cwd?: string; timestamp?: string };
} | null {
  const stat = statSync(absolutePath);
  if (stat.size > MAX_FILE_BYTES || stat.size === 0) {
    return null;
  }

  const raw = readFileSync(absolutePath, 'utf-8');
  const lines = raw.split('\n');

  let sessionId: string | undefined;
  let cwd: string | undefined;
  let timestamp: string | undefined;

  const exchanges: ConversationExchange[] = [];

  // Accumulator for current exchange being built
  let currentUserMsg = '';
  let currentUserLine = 0;
  let currentAssistantParts: string[] = [];
  let currentToolCalls: ToolCallInfo[] = [];
  let currentTimestamp: string | undefined;
  let lastLineNo = 0;
  let hasUser = false;

  function finalizeExchange(): void {
    if (!hasUser) return;
    const assistantMessage = currentAssistantParts.join('\n\n').trim();
    // Only emit exchanges that have at least a user message
    if (currentUserMsg.trim()) {
      exchanges.push({
        userMessage: currentUserMsg.trim(),
        assistantMessage,
        lineStart: currentUserLine,
        lineEnd: lastLineNo,
        timestamp: currentTimestamp,
        toolCalls: currentToolCalls,
      });
    }
    // Reset
    currentUserMsg = '';
    currentAssistantParts = [];
    currentToolCalls = [];
    currentTimestamp = undefined;
    hasUser = false;
  }

  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i]!.trim();
    if (!trimmed) continue;

    let rec: JsonlRecord;
    try {
      rec = JSON.parse(trimmed) as JsonlRecord;
    } catch {
      continue;
    }

    const recType = rec.type;
    if (!recType || SKIP_TYPES.has(recType)) continue;
    lastLineNo = i + 1;

    // Extract metadata from first record that has it
    if (!sessionId && rec.sessionId) sessionId = rec.sessionId;
    if (!cwd && rec.cwd) cwd = rec.cwd;
    if (!timestamp && rec.timestamp) timestamp = rec.timestamp;

    if (recType === 'user') {
      // Finalize previous exchange before starting new one
      finalizeExchange();
      currentUserMsg = extractMessageText(rec.message) ?? '';
      currentUserLine = i + 1;
      currentTimestamp = rec.timestamp;
      hasUser = true;
      continue;
    }

    if (recType === 'assistant') {
      const text = extractMessageText(rec.message);
      if (text) {
        currentAssistantParts.push(text);
      }
      // Extract tool calls from this assistant message
      if (rec.message?.content) {
        const tools = extractToolCalls(rec.message.content);
        currentToolCalls.push(...tools);
      }
      continue;
    }

    if (recType === 'summary') {
      const text = extractMessageText(rec.message);
      if (text) {
        currentAssistantParts.push(text);
      }
      continue;
    }
  }

  // Finalize last exchange
  finalizeExchange();

  if (exchanges.length === 0) {
    return null;
  }

  return {
    exchanges,
    metadata: { sessionId, cwd, timestamp },
  };
}
