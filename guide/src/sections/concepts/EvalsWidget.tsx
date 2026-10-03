import { useState } from 'react'
import { Card } from '../../components/ui'
import browserEval from '../../data/rag/summary.json'
import { retrieval } from '../../lib/data'
import { MODE_LABEL, MODES } from './modes'

const RUNS = {
  browser: { label: `${browserEval.model.split('/')[1]} (the playground's model)`, summary: browserEval.summary },
  placeholder: { label: 'placeholder embeddings (CI, offline)', summary: retrieval.summary },
}

export function EvalsWidget() {
  const [run, setRun] = useState<keyof typeof RUNS>('browser')
  const s = RUNS[run].summary
  return (
    <Card>
      <div className="text-[13px] font-medium">Retrieval eval — {retrieval.cases.length} questions about this codebase, recall@{retrieval.k}</div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {(Object.keys(RUNS) as (keyof typeof RUNS)[]).map((r) => (
          <button
            key={r}
            type="button"
            aria-pressed={r === run}
            onClick={() => setRun(r)}
            className={`rounded-full border px-2 py-0.5 text-[11px] ${r === run ? 'border-accent bg-accent-soft text-accent' : 'border-line text-muted hover:text-ink'}`}
          >
            {RUNS[r].label}
          </button>
        ))}
      </div>
      <div className="mt-3 space-y-2">
        {MODES.map((m) => (
          <div key={m} className="text-[13px]">
            <div className="flex justify-between">
              <span>{MODE_LABEL[m]}</span>
              <span className="tabular-nums text-muted">recall {s[m].recall.toFixed(2)} · MRR {s[m].mrr.toFixed(2)}</span>
            </div>
            <div className="mt-1 h-2 rounded-full bg-panel-2">
              <div className="h-2 rounded-full bg-accent" style={{ width: `${s[m].recall * 100}%` }} />
            </div>
          </div>
        ))}
      </div>
      <p className="mt-3 text-[12px] text-muted">
        Recall@5: “is the right file in the top 5?”. MRR: “how high up, on average?”. Even a 34 MB general-purpose model
        finds every answer here — the corpus is small (166 chunks) and the questions are phrased plainly, so treat these
        as a smoke test, not a benchmark. With placeholder embeddings the vector half is noise, which is why CI gates on the keyword half offline and on fused search once real embeddings
        are configured. Gating on the noisy number made CI flip with unrelated code changes — a real bug fixed while
        building this guide.
      </p>
      <ul className="mt-3 space-y-1 text-[13px]">
        <li>• <b>Spec eval</b>: an LLM judge scores specs 1–5 for testable / grounded / scoped.</li>
        <li>• <b>End-to-end eval</b>: real models on a fixture repo; tracks completion rate, gate failures, review cycles.</li>
        <li>• <b>Live smoke test</b>: one structured call per model, each provider on its own.</li>
      </ul>
    </Card>
  )
}
