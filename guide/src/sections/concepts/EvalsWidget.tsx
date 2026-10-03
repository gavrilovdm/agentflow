import { Card } from '../../components/ui'
import { retrieval } from '../../lib/data'
import { MODE_LABEL, MODES } from './modes'

export function EvalsWidget() {
  const s = retrieval.summary
  return (
    <Card>
      <div className="text-[13px] font-medium">Retrieval eval — {retrieval.cases.length} questions about this codebase, recall@{retrieval.k}</div>
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
        Recall@5: “is the right file in the top 5?”. MRR: “how high up, on average?”. With placeholder embeddings the
        vector half is noise, which is why CI gates on the keyword half offline and on fused search once real embeddings
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
