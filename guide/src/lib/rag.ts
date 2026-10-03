// In-browser hybrid retrieval: a TypeScript port of InMemoryStore.search_text/search_vector
// and rrf_merge (src/agentflow/rag/), so the deployed guide can search without the API.
// rag.test.ts checks the keyword half against rankings recorded by the Python code.

export interface Chunk {
  path: string
  index: number
  start: number
  end: number
  kind: string
  content: string
}

export interface RagHit extends Chunk {
  score: number
}

const RRF_K = 60

function stem(token: string): string {
  for (const suffix of ['ing', 'ed', 'es', 's']) {
    if (token.endsWith(suffix) && token.length - suffix.length >= 3) return token.slice(0, -suffix.length)
  }
  return token
}

/** Identifier-aware tokens, as store._tokens: split camelCase, snake_case and paths, stem lightly. */
export function tokens(text: string): string[] {
  const split = text.replace(/([a-z])([A-Z])/g, '$1 $2')
  return (split.match(/[A-Za-z0-9]+/g) ?? []).filter((t) => t.length > 1).map((t) => stem(t.toLowerCase()))
}

/** Per-chunk term counts, built once — the corpus is static. */
export function termCounts(chunks: Chunk[]): Map<string, number>[] {
  return chunks.map((ch) => {
    const counts = new Map<string, number>()
    for (const t of tokens(`${ch.path} ${ch.content}`)) counts.set(t, (counts.get(t) ?? 0) + 1)
    return counts
  })
}

// Python's sort is stable over insertion order; Array.prototype.sort is stable too, so ties
// keep corpus order on both sides.
function top(chunks: Chunk[], scores: number[], k: number): RagHit[] {
  return scores
    .map((score, i) => ({ score, i }))
    .sort((a, b) => b.score - a.score)
    .slice(0, k)
    .map(({ score, i }) => ({ ...chunks[i], score }))
}

export function searchText(chunks: Chunk[], counts: Map<string, number>[], query: string, k: number): RagHit[] {
  const q = new Set(tokens(query))
  const scores = counts.map((c) => {
    let s = 0
    for (const t of q) {
      const n = c.get(t)
      if (n) s += 1 + Math.log(n)
    }
    return s
  })
  // Filter before ranking, as Python only ranks chunks with a non-zero score.
  return top(chunks, scores, chunks.length).filter((h) => h.score > 0).slice(0, k)
}

/** Vectors are L2-normalised at build time, so cosine is a dot product. */
export function searchVector(chunks: Chunk[], vectors: Float32Array, dim: number, query: Float32Array, k: number): RagHit[] {
  const scores = chunks.map((_, i) => {
    let s = 0
    for (let d = 0; d < dim; d++) s += vectors[i * dim + d] * query[d]
    return s
  })
  return top(chunks, scores, k)
}

const id = (h: Chunk) => `${h.path}#${h.index}`

export function rrfMerge(rankings: RagHit[][], k: number): RagHit[] {
  const scores = new Map<string, number>()
  const byId = new Map<string, RagHit>()
  for (const ranking of rankings) {
    ranking.forEach((hit, rank) => {
      scores.set(id(hit), (scores.get(id(hit)) ?? 0) + 1 / (RRF_K + rank + 1))
      if (!byId.has(id(hit))) byId.set(id(hit), hit)
    })
  }
  return [...byId.values()]
    .sort((a, b) => (scores.get(id(b)) as number) - (scores.get(id(a)) as number))
    .slice(0, k)
    .map((h) => ({ ...h, score: scores.get(id(h)) as number }))
}

/** Unique files in rank order, as evals/retrieval_eval._files. */
export function files(hits: Chunk[]): string[] {
  return [...new Set(hits.map((h) => h.path))]
}

export function decodeVectors(b64: string): Float32Array {
  const bin = atob(b64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  return new Float32Array(bytes.buffer)
}
