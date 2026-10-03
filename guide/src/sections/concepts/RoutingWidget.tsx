import { useState } from 'react'
import { Card } from '../../components/ui'
import { afterReview, DEFAULT_BUDGETS } from '../../lib/routing'

function Slider({ label, value, max, onChange }: { label: string; value: number; max: number; onChange: (v: number) => void }) {
  return (
    <label className="block text-[13px]">
      <span className="flex justify-between">
        <span>{label}</span>
        <b className="tabular-nums">
          {value}
          <span className="font-normal text-muted"> / {max}</span>
        </b>
      </span>
      <input type="range" min={0} max={max} value={value} onChange={(e) => onChange(Number(e.target.value))} className="w-full accent-[var(--accent)]" />
    </label>
  )
}

const NEXT_COLOR: Record<string, string> = { complete_task: 'var(--ok)', run_coder: 'var(--coder)', adjudicate: 'var(--opus)', escalate: 'var(--human)' }

export function RoutingWidget() {
  const b = DEFAULT_BUDGETS
  const [approved, setApproved] = useState(false)
  const [gate, setGate] = useState(1)
  const [rev, setRev] = useState(0)
  const [mal, setMal] = useState(0)
  const [rep, setRep] = useState(0)
  const [adj, setAdj] = useState(false)
  const d = afterReview(
    { coderFixAttempts: 0, gateFailures: gate, reviewCycles: rev, reviewerMalfunctions: mal },
    { approved, stallRepeats: rep, adjudicated: adj },
    b,
  )
  return (
    <Card>
      <p className="text-[13px] text-muted">
        A task just came back from <code>run_review</code>. Set its history and see which edge the graph takes — this is
        a line-for-line port of <code>routing.py</code>, tested against the same cases.
      </p>
      <label className="mt-3 flex items-center gap-2 text-[13px]">
        <input type="checkbox" checked={approved} onChange={(e) => setApproved(e.target.checked)} /> reviewer approved this attempt
      </label>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <Slider label="gate failures (tests/lint red)" value={gate} max={b.maxGateFailures} onChange={setGate} />
        <Slider label="review rejections" value={rev} max={b.maxReviewCycles} onChange={setRev} />
        <Slider label="reviewer malfunctions" value={mal} max={b.maxReviewerMalfunctions} onChange={setMal} />
        <Slider label="same failure seen before (recent attempts)" value={rep} max={3} onChange={setRep} />
      </div>
      <label className="mt-2 flex items-center gap-2 text-[13px]">
        <input type="checkbox" checked={adj} onChange={(e) => setAdj(e.target.checked)} /> the referee already ruled on this task
      </label>
      <div className="mt-4 rounded-lg border-2 p-3" style={{ borderColor: NEXT_COLOR[d.next] }}>
        <div className="text-[12px] uppercase tracking-wide text-muted">next node</div>
        <div className="font-mono text-lg font-semibold" style={{ color: NEXT_COLOR[d.next] }}>
          {d.next}
        </div>
        <div className="text-[13px]">{d.reason}</div>
      </div>
    </Card>
  )
}
