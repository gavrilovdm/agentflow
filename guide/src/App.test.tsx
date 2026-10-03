// Smoke test: the whole guide renders (server-side) without throwing, and every section is present.
import { renderToString } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import App from './App'

describe('guide', () => {
  it('renders every section', () => {
    const html = renderToString(<App />)
    for (const id of ['replay', 'concepts', 'use-cases', 'what-broke', 'code-map', 'interview']) {
      expect(html).toContain(`id="${id}"`)
    }
    for (const c of ['tools', 'rag', 'state', 'routing', 'memory', 'fallbacks', 'evals']) {
      expect(html).toContain(`id="concept-${c}"`)
    }
  })
})
