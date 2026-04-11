import { pipeline, type FeatureExtractionPipeline } from '@xenova/transformers';

// --- Lazy-loaded Embedding Pipeline ---

let embedder: FeatureExtractionPipeline | null = null;

const MAX_INPUT_CHARS = 2000;

async function getEmbedder(): Promise<FeatureExtractionPipeline> {
  if (!embedder) {
    embedder = await pipeline('feature-extraction', 'Xenova/bge-base-en-v1.5') as FeatureExtractionPipeline;
  }
  return embedder;
}

// --- Public API ---

export async function embedText(text: string): Promise<Float32Array> {
  const model = await getEmbedder();
  const truncated = text.substring(0, MAX_INPUT_CHARS);
  const output = await model(truncated, { pooling: 'mean', normalize: true });
  return new Float32Array(output.data as ArrayLike<number>);
}

