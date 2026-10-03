import { describe, expect, it } from 'vitest'
import graph from '../data/graph.json'
import { EDGES, POSITIONS, SELF_LOOPS } from './graphLayout'

const key = (e: { source: string; target: string }) => `${e.source}->${e.target}`

describe('graph layout stays in sync with the compiled graph', () => {
  it('positions every node', () => {
    for (const n of graph.nodes) expect(POSITIONS[n], n).toBeDefined()
  })
  it('has an explicit edge spec for every edge, and no stale specs', () => {
    const real = graph.edges.filter((e) => e.source !== e.target).map(key)
    for (const k of real) expect(EDGES[k], k).toBeDefined()
    for (const k of Object.keys(EDGES)) expect(real, k).toContain(k)
    for (const e of graph.edges.filter((e) => e.source === e.target)) expect(SELF_LOOPS[e.source]).toBeDefined()
  })
  it('never reuses a handle for two edges of the same node (lines would merge)', () => {
    const used = new Map<string, string>()
    for (const [k, spec] of Object.entries(EDGES)) {
      const [s, t] = k.split('->')
      for (const slot of [`${s}:${spec.source}`, `${spec.targetAlias ?? t}:${spec.target}`]) {
        expect(used.get(slot), `${slot} used by ${used.get(slot)} and ${k}`).toBeUndefined()
        used.set(slot, k)
      }
    }
  })
})
