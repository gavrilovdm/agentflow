// Embeds guide/src/data/rag/corpus.json for the in-browser RAG playground.
//
//   node scripts/build_rag_index.ts           # build src/data/rag/index.json with MODEL
//   node scripts/build_rag_index.ts --bench   # compare CANDIDATES on the golden queries
//
// Runs the same transformers.js + ONNX weights the browser loads, so chunk vectors and
// query vectors come from one model. The corpus comes from scripts/export_guide_data.py.
import { pipeline, type FeatureExtractionPipeline } from '@huggingface/transformers'
import { readFileSync, writeFileSync } from 'node:fs'
import { files, rrfMerge, searchText, searchVector, termCounts, type Chunk } from '../src/lib/rag.ts'

interface ModelSpec {
  id: string
  pooling: 'mean' | 'cls'
  queryPrefix: string
}

const RETRIEVAL_PREFIX = 'Represent this sentence for searching relevant passages: '
export const CANDIDATES: ModelSpec[] = [
  { id: 'Xenova/all-MiniLM-L6-v2', pooling: 'mean', queryPrefix: '' },
  { id: 'Xenova/bge-small-en-v1.5', pooling: 'cls', queryPrefix: RETRIEVAL_PREFIX },
  { id: 'Xenova/gte-small', pooling: 'mean', queryPrefix: '' },
  { id: 'Snowflake/snowflake-arctic-embed-xs', pooling: 'cls', queryPrefix: RETRIEVAL_PREFIX },
]
const MODEL = CANDIDATES[2] // best dense MRR in --bench (0.975 vs 0.87–0.92)
const DTYPE = 'q8'
const K = 5

const data = new URL('../src/data/rag/', import.meta.url)
const chunks = JSON.parse(readFileSync(new URL('corpus.json', data), 'utf8')) as Chunk[]
const golden = JSON.parse(readFileSync(new URL('../../evals/datasets/retrieval_golden.json', import.meta.url), 'utf8')) as {
  query: string
  relevant: string[]
}[]
const counts = termCounts(chunks)

// Same text the Python indexer embeds (Chunk.embedding_text).
const passage = (c: Chunk) => `File: ${c.path} (lines ${c.start}-${c.end})\n\n${c.content}`

async function embed(extract: FeatureExtractionPipeline, spec: ModelSpec, texts: string[]): Promise<Float32Array> {
  const out: Float32Array[] = []
  for (let i = 0; i < texts.length; i += 16) {
    const t = await extract(texts.slice(i, i + 16), { pooling: spec.pooling, normalize: true })
    out.push(t.data as Float32Array)
  }
  const all = new Float32Array(out.reduce((n, a) => n + a.length, 0))
  let off = 0
  for (const a of out) all.set(a, (off += a.length) - a.length)
  return all
}

async function evaluate(spec: ModelSpec) {
  const extract = await pipeline('feature-extraction', spec.id, { dtype: DTYPE })
  const t0 = performance.now()
  const vectors = await embed(extract, spec, chunks.map(passage))
  const dim = vectors.length / chunks.length
  const embedMs = performance.now() - t0
  const totals = { dense: [0, 0], lexical: [0, 0], hybrid: [0, 0] }
  for (const c of golden) {
    const q = await embed(extract, spec, [spec.queryPrefix + c.query])
    const dense = searchVector(chunks, vectors, dim, q, 20)
    const lexical = searchText(chunks, counts, c.query, 20)
    const ranked = { dense: files(dense), lexical: files(lexical), hybrid: files(rrfMerge([dense, lexical], 20)) }
    for (const [m, r] of Object.entries(ranked) as [keyof typeof totals, string[]][]) {
      totals[m][0] += c.relevant.filter((p) => r.slice(0, K).includes(p)).length / c.relevant.length
      const hit = r.findIndex((p) => c.relevant.includes(p))
      totals[m][1] += hit < 0 ? 0 : 1 / (hit + 1)
    }
  }
  const round = (x: number) => Math.round((x / golden.length) * 1000) / 1000
  const summary = Object.fromEntries(Object.entries(totals).map(([m, [r, mrr]]) => [m, { recall: round(r), mrr: round(mrr) }]))
  return { vectors, dim, summary, embedMs }
}

if (process.argv.includes('--bench')) {
  for (const spec of CANDIDATES) {
    const { dim, summary, embedMs } = await evaluate(spec)
    const s = summary as Record<string, { recall: number; mrr: number }>
    console.log(
      `${spec.id.padEnd(40)} dim=${dim} dense r=${s.dense.recall} mrr=${s.dense.mrr} | hybrid r=${s.hybrid.recall} mrr=${s.hybrid.mrr} | ${Math.round(embedMs)}ms`,
    )
  }
} else {
  const { vectors, dim, summary } = await evaluate(MODEL)
  const b64 = Buffer.from(vectors.buffer, vectors.byteOffset, vectors.byteLength).toString('base64')
  writeFileSync(
    new URL('index.json', data),
    JSON.stringify({ model: MODEL.id, dtype: DTYPE, pooling: MODEL.pooling, queryPrefix: MODEL.queryPrefix, dim, k: K, summary, vectors: b64 }),
  )
  // Kept apart from index.json so the evals widget can show the scores without pulling in the vectors.
  writeFileSync(new URL('summary.json', data), JSON.stringify({ model: MODEL.id, summary }, null, 1) + '\n')
  console.log(`wrote rag/index.json: ${MODEL.id}, ${chunks.length} chunks × ${dim}`, summary)
}
