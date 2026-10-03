// A replayable run: steps (one per executed graph node) plus the model/tool calls made
// during it. Pure helpers only — no React, no global data — so any scenario (real or
// simulated) can be replayed by the same components.
import { ts, type Call, type Step } from './data'

export interface RunRecord {
  thread_id: string
  steps: Step[]
  final: Record<string, unknown>
  calls: Call[]
}

/** Which task each step belongs to (current_task_id appears only in deltas that change it). */
export function taskPerStep(steps: Step[]): (string | null)[] {
  let current: string | null = null
  return steps.map((s) => {
    if ('current_task_id' in s.delta) current = (s.delta.current_task_id as string | null) ?? null
    return current
  })
}

export function durationSeconds(step: Step): number {
  return Math.max(0, (ts(step.ended) - ts(step.started)) / 1000)
}

export function callsDuring(run: RunRecord, step: Step): Call[] {
  const a = ts(step.started)
  const b = ts(step.ended)
  return run.calls.filter((c) => {
    const t = ts(c.t)
    return t >= a && t < b
  })
}

/** END is not a node that runs, so it has no checkpoint step; append one so the replay
 * can land on it. A run that ended in handle_failure ended on the "stopped" path. */
export function withEndStep(run: RunRecord): RunRecord {
  const last = run.steps.at(-1)
  if (!last || last.node === '__end__') return run
  const status = run.final.status as string | undefined
  if (status !== 'completed' && status !== 'failed') return run
  return {
    ...run,
    steps: [
      ...run.steps,
      { node: '__end__', started: last.ended, ended: last.ended, delta: { status, via: last.node } },
    ],
  }
}

export function totalTokens(run: RunRecord): number {
  return run.calls.reduce((s, c) => s + (c.type === 'llm' ? (c.tokens ?? 0) : 0), 0)
}
