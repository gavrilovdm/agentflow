import { useMemo, useState } from 'react'
import { Card, Code } from '../../components/ui'
import { calls, type LlmCall, type ToolRun } from '../../lib/data'

const SCHEMAS = new Set(['SpecDraft', 'TaskPlan', 'GeneratedTest', 'ReviewDecision', 'FailureRuling'])

export function ToolsWidget() {
  // Pair each model call that requested tools with the tool runs that followed it.
  const examples = useMemo(() => {
    const out: { call: LlmCall; runs: ToolRun[]; label: string }[] = []
    calls.forEach((c, k) => {
      if (c.type !== 'llm' || c.output.tool_calls.length === 0) return
      const runs: ToolRun[] = []
      for (let j = k + 1; j < calls.length && calls[j].type === 'tool'; j++) runs.push(calls[j] as ToolRun)
      const names = c.output.tool_calls.map((t) => t.name)
      const role = c.tags.find((t) => ['coder', 'test-generator', 'orchestrator', 'spec', 'tasks'].includes(t)) ?? 'reviewer'
      out.push({ call: c, runs, label: `${role}: ${names.join(', ')}` })
    })
    return out
  }, [])
  const [k, setK] = useState(() => Math.max(0, examples.findIndex((e) => e.label.includes('search_codebase'))))
  const ex = examples[k]
  const structured = ex.call.output.tool_calls.some((t) => SCHEMAS.has(t.name))
  return (
    <Card>
      <label className="text-[12px] font-semibold uppercase tracking-wide text-muted">
        Pick a real call from the run
        <select className="mt-1 block w-full rounded-lg border border-line bg-panel-2 px-2 py-1.5 text-[13px] font-normal normal-case tracking-normal text-ink" value={k} onChange={(e) => setK(Number(e.target.value))}>
          {examples.map((e, i) => (
            <option key={i} value={i}>
              {e.label}
            </option>
          ))}
        </select>
      </label>
      <ol className="mt-4 space-y-4 text-[13px]">
        <li>
          <b>1 · The model answered</b> <span className="text-muted">({ex.call.model}, {ex.call.latency_s}s) — not prose, but:</span>
          <Code maxH="max-h-56">{JSON.stringify(ex.call.output.tool_calls, null, 2)}</Code>
        </li>
        {structured ? (
          <li>
            <b>2 · This one is a structured output.</b>{' '}
            <span className="text-muted">
              The “tool” is a Pydantic schema. The arguments above are validated into a Python object, and the graph
              continues with real typed data — no parsing of free text.
            </span>
          </li>
        ) : (
          <li>
            <b>2 · Our code ran it</b> <span className="text-muted">and sent the result back to the model:</span>
            {ex.runs.length ? (
              ex.runs.map((r, i) => (
                <div key={i} className="mt-2">
                  <code className="text-accent">{r.name}</code>
                  <Code maxH="max-h-40">{r.output || '(empty)'}</Code>
                </div>
              ))
            ) : (
              <p className="text-muted">(tool output not captured)</p>
            )}
          </li>
        )}
        <li>
          <b>3 · Repeat</b> <span className="text-muted">until the model answers without a tool call — that’s the agent loop. A call limit stops runaway loops.</span>
        </li>
      </ol>
    </Card>
  )
}
