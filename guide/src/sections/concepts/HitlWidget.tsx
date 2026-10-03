import { useState } from 'react'
import { Card, Code } from '../../components/ui'
import { finalSpec, run } from '../../lib/data'

export function HitlWidget() {
  const [phase, setPhase] = useState<'waiting' | 'approved' | 'rejected'>('waiting')
  const [feedback, setFeedback] = useState('Return a dataclass instead of a dict from Database.update')
  const checkpoints = run.steps.length + 1
  const state =
    phase === 'waiting'
      ? { spec_approved: false, spec_feedback: null, status: 'spec_review', next: ['await_spec_approval'] }
      : phase === 'approved'
        ? { spec_approved: true, spec_feedback: null, status: 'planning', next: ['generate_tasks'] }
        : { spec_approved: false, spec_feedback: feedback, status: 'spec_review', next: ['generate_spec'] }
  return (
    <Card>
      <div className="text-[13px] text-muted">The real spec from the run is waiting for you:</div>
      <div className="mt-2 rounded-lg border border-line bg-panel-2 p-3 text-[13px]">
        <b>{finalSpec.title}</b> <span className="text-muted">v{finalSpec.version}</span>
        <div className="mt-1 text-muted">
          {finalSpec.acceptance_criteria.length} acceptance criterion · {finalSpec.out_of_scope.length} out-of-scope item —{' '}
          <a href="#what-broke" className="text-accent hover:underline">a human should have rejected this one (bug #8)</a>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <button onClick={() => setPhase('approved')} className="rounded-lg bg-ok px-3 py-1.5 text-[13px] font-medium text-white">✅ Approve</button>
        <button onClick={() => setPhase('rejected')} className="rounded-lg bg-bad px-3 py-1.5 text-[13px] font-medium text-white">❌ Reject with feedback</button>
        <button onClick={() => setPhase('waiting')} className="rounded-lg border border-line px-3 py-1.5 text-[13px]">reset</button>
      </div>
      <input value={feedback} onChange={(e) => setFeedback(e.target.value)} className="mt-2 w-full rounded-lg border border-line bg-panel-2 px-3 py-1.5 text-[13px]" aria-label="Rejection feedback" />
      <div className="mt-4 text-[12px] font-semibold uppercase tracking-wide text-muted">What the resumed graph sees (simulated)</div>
      <Code maxH="max-h-48">{JSON.stringify(state, null, 2)}</Code>
      <p className="mt-2 text-[13px] text-muted">
        {phase === 'waiting' && 'Paused. Nothing runs and nothing is held in memory — just a row in Postgres.'}
        {phase === 'approved' && '→ routes to generate_tasks. This is exactly what happened in the real run.'}
        {phase === 'rejected' && '→ routes back to generate_spec; the feedback and the previous spec go into the prompt, and v2 comes back for approval.'}
      </p>
      <p className="mt-3 text-[12px] text-muted">
        Real numbers: the run above saved <b className="text-ink">{checkpoints}</b> checkpoints. The REST API, the Telegram
        button and the MCP tool all resume the same way, and a resume is keyed by checkpoint id, so a double-click can’t
        resume twice.
      </p>
    </Card>
  )
}
