import { describe, expect, it } from 'vitest'
import corpus from '../data/rag/corpus.json'
import { retrieval } from './data'
import { files, rrfMerge, searchText, termCounts, tokens, type Chunk } from './rag'

const chunks = corpus as Chunk[]
const counts = termCounts(chunks)

describe('rag port', () => {
  it('tokenises like store._tokens', () => {
    expect(tokens('resolvePath in_workspace passwords hashed a')).toEqual(['resolve', 'path', 'in', 'workspace', 'password', 'hash'])
  })

  // retrieval.json holds keyword rankings computed by the Python store over the same corpus.
  it.each(retrieval.cases.map((c) => [c.query, c.lexical] as const))('keyword ranking matches Python: %s', (query, expected) => {
    expect(files(searchText(chunks, counts, query, 20)).slice(0, retrieval.k)).toEqual(expected)
  })

  it('rrf rewards agreement between rankings', () => {
    const [a, b, c] = chunks
    const merged = rrfMerge([[{ ...a, score: 1 }, { ...b, score: 1 }], [{ ...b, score: 1 }, { ...c, score: 1 }]], 3)
    expect(merged[0].path + merged[0].index).toBe(b.path + b.index)
  })
})
