import { createHash } from 'node:crypto';
import { mkdirSync } from 'node:fs';

// --- Interfaces ---

export interface MemoryChunk {
  id: string;
  filePath: string;
  chunkIndex: number;
  startLine: number;
  endLine: number;
  title: string;
  content: string;
  embedding: Float32Array;
  hash: string;
  updatedAt: number;
}

export interface FileEntry {
  filePath: string;
  contentHash: string;
  lastIndexed: number;
  chunkCount: number;
  summary?: string | null;
}

export interface ToolCallInfo {
  toolName: string;
  timestamp?: string;
}

export interface ConversationExchange {
  userMessage: string;
  assistantMessage: string;
  lineStart: number;
  lineEnd: number;
  timestamp?: string;
  toolCalls: ToolCallInfo[];
}

export interface ExchangeChunk {
  exchanges: ConversationExchange[];
  startLine: number;
  endLine: number;
  text: string;
  toolNames: string[];
  hash: string;
}

export interface SearchResult {
  chunk: MemoryChunk;
  score: number;
  matchType: 'vector' | 'keyword' | 'hybrid';
}

// --- Constants ---

export const EMBEDDING_MODEL = 'Xenova/all-MiniLM-L6-v2';
export const EMBEDDING_DIMS = 384;
export const EMBEDDING_PROVIDER = 'xenova-transformers';

// --- Utility Functions ---

export function hashText(text: string): string {
  return createHash('sha256').update(text).digest('hex');
}

export function ensureDir(dir: string): void {
  mkdirSync(dir, { recursive: true });
}
