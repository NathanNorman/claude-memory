/**
 * Lightweight LLM client for OpenAI-compatible /v1/chat/completions endpoints.
 * Uses native fetch() — no npm dependencies required.
 */

export interface ChatMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

export interface LlmConfig {
  baseUrl: string;
  model: string;
  apiKey?: string;
}

/**
 * Call an OpenAI-compatible chat completions endpoint.
 * Returns the assistant message content string.
 */
export async function callChatCompletion(
  config: LlmConfig,
  messages: ChatMessage[],
  options?: { temperature?: number; maxTokens?: number },
): Promise<string> {
  const url = `${config.baseUrl.replace(/\/$/, '')}/v1/chat/completions`;
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (config.apiKey) {
    headers['Authorization'] = `Bearer ${config.apiKey}`;
  }

  const body = JSON.stringify({
    model: config.model,
    messages,
    temperature: options?.temperature ?? 0.0,
    max_tokens: options?.maxTokens ?? 2048,
  });

  const response = await fetch(url, {
    method: 'POST',
    headers,
    body,
  });

  if (!response.ok) {
    const text = await response.text().catch(() => '');
    throw new Error(`LLM API error ${response.status}: ${text.slice(0, 200)}`);
  }

  const json = await response.json() as {
    choices?: Array<{ message?: { content?: string } }>;
  };

  const content = json.choices?.[0]?.message?.content;
  if (!content) {
    throw new Error('LLM response missing content');
  }
  return content;
}

/**
 * Retry a function with exponential backoff.
 * Max 5 retries starting at 1s delay.
 */
export async function retryWithBackoff<T>(
  fn: () => Promise<T>,
  maxRetries: number = 5,
  initialDelay: number = 1000,
): Promise<T> {
  let lastError: Error | undefined;
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastError = err instanceof Error ? err : new Error(String(err));
      if (attempt < maxRetries - 1) {
        const delay = initialDelay * Math.pow(2, attempt) + Math.random() * 500;
        process.stderr.write(
          `[llm-client] Retry ${attempt + 1}/${maxRetries} after ${Math.round(delay)}ms: ${lastError.message.slice(0, 100)}\n`,
        );
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
    }
  }
  throw lastError!;
}

/**
 * Strip markdown code fences from LLM response (models sometimes wrap JSON in ```).
 */
export function stripCodeFences(text: string): string {
  const fenceMatch = text.match(/```(?:json)?\s*\n?([\s\S]*?)\n?\s*```/);
  if (fenceMatch) {
    return fenceMatch[1]!.trim();
  }
  return text.trim();
}

/**
 * Validate LLM configuration from environment variables.
 * Returns config object or throws descriptive error.
 */
export function validateLlmConfig(): LlmConfig {
  const baseUrl = process.env.MEMORY_LLM_BASE_URL;
  const model = process.env.MEMORY_LLM_MODEL;
  const apiKey = process.env.MEMORY_LLM_API_KEY;

  if (!baseUrl) {
    throw new Error(
      'MEMORY_LLM_BASE_URL is required for LLM scoring. ' +
      'Set it to an OpenAI-compatible endpoint (e.g., http://localhost:8000)',
    );
  }
  if (!model) {
    throw new Error(
      'MEMORY_LLM_MODEL is required for LLM scoring. ' +
      'Set it to the model name (e.g., Qwen/Qwen2.5-7B-Instruct)',
    );
  }

  return { baseUrl, model, apiKey };
}
