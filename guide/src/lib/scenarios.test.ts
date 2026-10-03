import { describe, expect, it } from 'vitest'
import graph from '../data/graph.json'
import { SCENARIOS } from './scenarios'

const edges = new Set(graph.edges.map((e) => `${e.source}->${e.target}`))

describe('replay scenarios', () => {
  it.each(SCENARIOS.map((s) => [s.id, s] as const))('%s only walks real graph edges', async (_, s) => {
    const run = await s.load()
    const nodes = run.steps.map((st) => st.node)
    for (const n of nodes) expect(graph.nodes, n).toContain(n)
    if (s.legacyGraph) return // recorded before test-first; transitions differ by design
    for (let i = 1; i < nodes.length; i++) {
      const pair = `${nodes[i - 1]}->${nodes[i]}`
      // Interrupted nodes (approvals, escalate) resume into their own successor, still an edge.
      expect(edges.has(pair), pair).toBe(true)
    }
    expect(nodes.at(-1)).toBe('__end__')
  })

  it('together, the scenarios reach every node of the graph', async () => {
    const seen = new Set<string>()
    for (const s of SCENARIOS) for (const st of (await s.load()).steps) seen.add(st.node)
    expect([...graph.nodes].filter((n) => !seen.has(n))).toEqual([])
  })
})
