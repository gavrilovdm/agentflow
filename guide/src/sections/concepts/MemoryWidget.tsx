import { useState } from 'react'
import { Card, Code } from '../../components/ui'

export function MemoryWidget() {
  const [step, setStep] = useState(0)
  const lesson = 'calc/ops.py: public functions need a docstring'
  const stages = [
    { t: 'Run 1 · reviewer rejects', body: `{\n  "verdict": "changes_requested",\n  "change_requests": ["${lesson}"]\n}` },
    { t: 'Run 1 · coder fixes it, reviewer approves → saved', body: `store.put(("lessons", "<repo>"), key, {\n  "text": "${lesson}",\n  "task": "add()"\n})` },
    { t: 'Run 2 · a new task starts → recalled by meaning', body: `## Lessons from past reviews of this repository\n- ${lesson} (from task: add())` },
  ]
  return (
    <Card>
      <div className="flex gap-1">
        {stages.map((_, i) => (
          <button key={i} onClick={() => setStep(i)} className={`flex-1 rounded-md border px-2 py-1 text-[12px] ${i === step ? 'border-accent bg-accent-soft text-accent' : 'border-line text-muted'}`}>
            {i + 1}
          </button>
        ))}
      </div>
      <div className="mt-3 text-[13px] font-medium">{stages[step].t}</div>
      <Code maxH="max-h-48">{stages[step].body}</Code>
      <p className="mt-3 text-[12px] text-muted">
        This exact flow is an end-to-end test (<code>test_review_feedback_becomes_lesson</code>). In the real run above
        nothing was rejected, so no lesson was stored. Only feedback that was <i>followed by an approval</i> is kept —
        that filters out reviewer noise.
      </p>
    </Card>
  )
}
