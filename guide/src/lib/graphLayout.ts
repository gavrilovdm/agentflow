// Explicit layout for the workflow graph. Every edge gets its own exit/entry point
// (handle) and a short label, so lines between the same nodes never merge, and the
// delivery column reads top-down. graphLayout.test.ts checks this table against
// graph.json, which is exported from the compiled LangGraph graph.

export type Side = 'top' | 'bottom' | 'left' | 'right'
export type HandleId = `${Side}-${25 | 50 | 75}`

const COL = { L: 0, C: 200, R: 400, RR: 600 } as const
const row = (n: number) => n * 78

export const POSITIONS: Record<string, [number, number]> = {
  __start__: [COL.C, row(0)],
  prepare_workspace: [COL.C, row(1)],
  generate_spec: [COL.C, row(2)],
  await_spec_approval: [COL.C, row(3)],
  generate_tasks: [COL.C, row(4)],
  await_task_approval: [COL.C, row(5)],
  select_next_task: [COL.C, row(6)],
  generate_task_test: [COL.C, row(7)],
  run_coder: [COL.C, row(8)],
  run_review: [COL.C, row(9)],
  complete_task: [COL.R, row(9)],
  create_pr: [COL.RR, row(6)],
  notify: [COL.RR, row(7)],
  finalize: [COL.RR, row(8)],
  __end__: [COL.RR, row(9)],
  __end_stopped__: [COL.L, row(5)], // visual alias of __end__ for the "run stopped" path
  handle_failure: [COL.L, row(6)],
  replan: [COL.L, row(7)],
  escalate: [COL.L, row(8)],
  adjudicate: [COL.L, row(9)],
  // Invisible spacer: fitView frames nodes only, and the skip/stop edge loops out to the
  // left of the failure column — without this its label was cut off.
  __pad_left__: [COL.L - 80, row(7)],
}

export const isSpacer = (id: string) => id.startsWith('__pad')

export interface EdgeSpec {
  source: HandleId
  target: HandleId
  label?: string
  /** Draw to this node instead of the real target (visual alias). */
  targetAlias?: string
}

export const EDGES: Record<string, EdgeSpec> = {
  '__start__->prepare_workspace': { source: 'bottom-50', target: 'top-50' },
  'prepare_workspace->generate_spec': { source: 'bottom-50', target: 'top-50' },
  'generate_spec->await_spec_approval': { source: 'bottom-50', target: 'top-50' },
  'await_spec_approval->generate_tasks': { source: 'bottom-50', target: 'top-50', label: 'approve' },
  'await_spec_approval->generate_spec': { source: 'right-50', target: 'right-50', label: 'reject + feedback' },
  'generate_tasks->await_task_approval': { source: 'bottom-50', target: 'top-50' },
  'await_task_approval->select_next_task': { source: 'bottom-50', target: 'top-50', label: 'approve' },
  'await_task_approval->generate_tasks': { source: 'right-50', target: 'right-50', label: 'reject + feedback' },
  'select_next_task->generate_task_test': { source: 'bottom-50', target: 'top-50', label: 'next task' },
  'select_next_task->create_pr': { source: 'right-25', target: 'left-50', label: 'all done' },
  'generate_task_test->run_coder': { source: 'bottom-50', target: 'top-50' },
  'run_coder->run_review': { source: 'bottom-25', target: 'top-25', label: 'code written' },
  'run_coder->escalate': { source: 'left-50', target: 'right-50', label: 'crashed, budget spent' },
  'run_review->run_coder': { source: 'top-75', target: 'bottom-75', label: 'rejected, budget left' },
  'run_review->complete_task': { source: 'right-50', target: 'left-50', label: 'approved' },
  'run_review->adjudicate': { source: 'left-75', target: 'right-75', label: 'same failure 3×' },
  'run_review->escalate': { source: 'left-25', target: 'right-75', label: 'budget spent' },
  'adjudicate->run_coder': { source: 'right-25', target: 'left-75', label: 'test was wrong' },
  'adjudicate->escalate': { source: 'top-50', target: 'bottom-50', label: 'code is wrong' },
  'escalate->run_coder': { source: 'right-25', target: 'left-25', label: 'retry + hint' },
  'escalate->replan': { source: 'top-50', target: 'bottom-50', label: 're-plan' },
  'escalate->handle_failure': { source: 'left-50', target: 'left-50', label: 'skip / stop' },
  'replan->select_next_task': { source: 'right-50', target: 'left-75' },
  'handle_failure->select_next_task': { source: 'right-50', target: 'left-25', label: 'skip' },
  'handle_failure->__end__': { source: 'top-50', target: 'bottom-50', label: 'stop', targetAlias: '__end_stopped__' },
  'complete_task->select_next_task': { source: 'top-50', target: 'right-75', label: 'commit, next' },
  'create_pr->notify': { source: 'bottom-50', target: 'top-50' },
  'notify->finalize': { source: 'bottom-50', target: 'top-50' },
  'finalize->__end__': { source: 'bottom-50', target: 'top-50' },
}

/** Self-loops are shown as a badge on the node rather than a line. */
export const SELF_LOOPS: Record<string, string> = { run_coder: '↻ retry after crash' }

export const HANDLE_IDS: HandleId[] = (['top', 'bottom', 'left', 'right'] as Side[]).flatMap((s) =>
  ([25, 50, 75] as const).map((p) => `${s}-${p}` as HandleId),
)
