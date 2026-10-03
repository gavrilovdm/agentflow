// Runs the RAG playground's search inside the browser: chunk vectors are prebuilt by
// scripts/build_rag_index.ts, the query is embedded here by the same ONNX model.
// Everything is imported lazily — the model is ~34 MB and the guide should not pay for it
// until someone actually searches.
import type { FeatureExtractionPipeline } from '@huggingface/transformers'
import { decodeVectors, rrfMerge, searchText, searchVector, termCounts, type Chunk, type RagHit } from './rag'

export interface BrowserIndex {
  model: string
  dtype: 'q8'
  pooling: 'mean' | 'cls'
  queryPrefix: string
  dim: number
  summary: Record<'dense' | 'lexical' | 'hybrid', { recall: number; mrr: number }>
}

export type Progress = (loadedMB: number, totalMB: number) => void

interface Loaded {
  meta: BrowserIndex
  chunks: Chunk[]
  counts: Map<string, number>[]
  vectors: Float32Array
  extract: FeatureExtractionPipeline
}

let loading: Promise<Loaded> | null = null

async function load(onProgress?: Progress): Promise<Loaded> {
  const [{ pipeline, env }, corpus, index] = await Promise.all([
    import('@huggingface/transformers'),
    import('../data/rag/corpus.json'),
    import('../data/rag/index.json'),
  ])
  // Without this the library first probes /models/… on our own host, and Vercel's SPA
  // fallback answers with index.html, which then fails to parse as a model config.
  env.allowLocalModels = false
  const meta = index.default as unknown as BrowserIndex & { vectors: string }
  const chunks = corpus.default as Chunk[]
  const sizes = new Map<string, [number, number]>()
  const extract = await pipeline('feature-extraction', meta.model, {
    dtype: meta.dtype,
    progress_callback: (p: { status: string; file?: string; loaded?: number; total?: number }) => {
      if (p.status !== 'progress' || !p.file || !p.total) return
      sizes.set(p.file, [p.loaded ?? 0, p.total])
      let loaded = 0
      let total = 0
      for (const [l, t] of sizes.values()) {
        loaded += l
        total += t
      }
      onProgress?.(loaded / 1e6, total / 1e6)
    },
  })
  return { meta, chunks, counts: termCounts(chunks), vectors: decodeVectors(meta.vectors), extract }
}

/** Loads once; a failed load (offline, CDN blocked) can be retried. */
export function loadBrowserRag(onProgress?: Progress): Promise<Loaded> {
  loading ??= load(onProgress).catch((e) => {
    loading = null
    throw e
  })
  return loading
}

export async function browserSearch(query: string, k = 5): Promise<Record<'dense' | 'lexical' | 'hybrid', RagHit[]>> {
  const { meta, chunks, counts, vectors, extract } = await loadBrowserRag()
  const t = await extract(meta.queryPrefix + query, { pooling: meta.pooling, normalize: true })
  const dense = searchVector(chunks, vectors, meta.dim, t.data as Float32Array, 20)
  const lexical = searchText(chunks, counts, query, 20)
  // Show chunks, as the live API does; unlike the eval, a file may appear twice.
  return { dense: dense.slice(0, k), lexical: lexical.slice(0, k), hybrid: rrfMerge([dense, lexical], k) }
}
