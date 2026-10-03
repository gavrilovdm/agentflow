// Replay scenarios. Real ones are captured from Postgres checkpoints + LangSmith traces by
// scripts/export_guide_data.py and loaded lazily (they are large); the referee path has no
// real run yet, so it is simulated — and labelled as such — following the graph's own edges.
import type { Step } from './data'
import { withEndStep, type RunRecord } from './run'

export interface Scenario {
  id: string
  title: string
  kind: 'real' | 'simulated'
  summary: string
  /** Recorded on an earlier version of the graph (before test-first), so some of its
   * transitions no longer exist as edges. */
  legacyGraph?: boolean
  load: () => Promise<RunRecord>
}

const realRuns = import.meta.glob<RunRecord>('../data/runs/*.json', { import: 'default' })
const real = (file: string) => async () => withEndStep(await realRuns[`../data/runs/${file}.json`]())

export const SCENARIOS: Scenario[] = [
  {
    id: 'happy',
    title: 'Happy path',
    kind: 'real',
    summary: 'Two tasks, both pass gate and review first time, PR opened.',
    legacyGraph: true,
    load: real('happy'),
  },
  {
    id: 'escalate-skip',
    title: 'Escalation → skip',
    kind: 'real',
    summary: 'A deliberately contradictory feature with a review budget of 1. The task runs out, the run asks a human, the human skips it, nothing is salvageable — the run stops.',
    load: real('escalate-skip'),
  },
  {
    id: 'escalate-replan',
    title: 'Escalation → re-plan',
    kind: 'real',
    summary: 'Same contradiction, default budgets. The task escalates, a human chooses re-plan with guidance, the plan is rewritten — then the new task escalates and is skipped. (Both escalations were caused by a reviewer fallback bug, fixed afterwards.)',
    load: real('escalate-replan'),
  },
  {
    id: 'referee',
    title: 'Referee fixes a bad test',
    kind: 'simulated',
    summary: 'Simulated: the same test failure comes back three times, the referee rules that the test is wrong and rewrites it, and the task then passes. Follows the real graph edges; no live run has hit this path yet.',
    load: async () => withEndStep(simulatedReferee()),
  },
]

/** Simulated referee scenario. Timestamps are synthetic and evenly spaced. */
export function simulatedReferee(): RunRecord {
  const path: [string, Record<string, unknown>][] = [
    ['__start__', { user_prompt: 'Add slugify(title) to shop.text: lowercase, spaces to hyphens, strip punctuation.' }],
    ['prepare_workspace', { note: 'Workspace cloned and indexed.' }],
    ['generate_spec', { note: 'Spec written.' }],
    ['await_spec_approval', { note: 'Approved.' }],
    ['generate_tasks', { note: 'One task: task-slugify, interface shop.text.slugify(title: str) -> str.' }],
    ['await_task_approval', { note: 'Approved.' }],
    ['select_next_task', { current_task_id: 'task-slugify' }],
    ['generate_task_test', { note: 'The test asserts slugify("Hello, World!") == "hello-world-" — a trailing hyphen the spec never asked for. It fails (red): good.' }],
    ['run_coder', { note: 'Coder implements slugify per the spec: "hello-world".' }],
    ['run_review', { note: 'Gate fails: expected "hello-world-", got "hello-world". (failure A, 1st time)' }],
    ['run_coder', { note: 'Coder tries again, still following the spec.' }],
    ['run_review', { note: 'Gate fails with the same assertion. (A, 2nd time)' }],
    ['run_coder', { note: 'Third attempt.' }],
    ['run_review', { note: 'Same failure a 3rd time within the window → stall → referee.' }],
    ['adjudicate', { note: 'Referee: the test contradicts the spec (no trailing hyphen is required). Culprit: test. The test is regenerated from the spec and the coder gets a fresh run.' }],
    ['run_coder', { note: 'Coder re-runs against the corrected test.' }],
    ['run_review', { note: 'Gate green, reviewer approves.' }],
    ['complete_task', { note: 'Code + corrected test committed.' }],
    ['select_next_task', { current_task_id: null }],
    ['create_pr', { note: 'PR opened.' }],
    ['notify', {}],
    ['finalize', { status: 'completed' }],
  ]
  const t0 = Date.parse('2026-01-01T00:00:00Z')
  const steps: Step[] = path.map(([node, delta], i) => ({
    node,
    started: new Date(t0 + i * 8000).toISOString(),
    ended: new Date(t0 + (i + 1) * 8000).toISOString(),
    delta: { ...delta, simulated: true },
  }))
  return { thread_id: 'simulated-referee', steps, final: { status: 'completed' }, calls: [] }
}
