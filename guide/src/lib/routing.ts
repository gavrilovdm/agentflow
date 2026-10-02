// A faithful TypeScript port of src/agentflow/graph/routing.py — the rules that decide
// which edge the graph takes after the coder and after review. routing.test.ts runs the
// same cases as tests/test_routing.py, so this simulator cannot drift from the real code.

export interface Budgets {
  maxCoderFixAttempts: number
  maxReviewCycles: number
  maxGateFailures: number
  maxReviewerMalfunctions: number
}

export const DEFAULT_BUDGETS: Budgets = {
  maxCoderFixAttempts: 2,
  maxReviewCycles: 4,
  maxGateFailures: 5,
  maxReviewerMalfunctions: 3,
}

export const STALL_REPEATS = 2 // three identical failures in a row

export interface TaskCounters {
  coderFixAttempts: number
  reviewCycles: number
  gateFailures: number
  reviewerMalfunctions: number
}

export interface ReviewState {
  approved: boolean
  stallRepeats: number
  adjudicated: boolean
}

export type AfterReview = 'complete_task' | 'run_coder' | 'handle_failure' | 'adjudicate'

export interface Decision<T extends string> {
  next: T
  reason: string
}

export function afterReview(task: TaskCounters, review: ReviewState, b: Budgets): Decision<AfterReview> {
  if (review.approved) return { next: 'complete_task', reason: 'Reviewer approved the change.' }
  if (task.reviewCycles >= b.maxReviewCycles)
    return { next: 'handle_failure', reason: `Review budget spent (${task.reviewCycles}/${b.maxReviewCycles}).` }
  if (task.gateFailures >= b.maxGateFailures)
    return { next: 'handle_failure', reason: `Gate budget spent (${task.gateFailures}/${b.maxGateFailures}).` }
  if (task.reviewerMalfunctions >= b.maxReviewerMalfunctions)
    return {
      next: 'handle_failure',
      reason: `The reviewer itself kept failing (${task.reviewerMalfunctions}/${b.maxReviewerMalfunctions}) — e.g. an API outage.`,
    }
  if (review.stallRepeats >= STALL_REPEATS)
    return review.adjudicated
      ? { next: 'handle_failure', reason: 'Same failure 3× in a row and the referee already ruled once.' }
      : { next: 'adjudicate', reason: 'Same failure 3× in a row — ask a referee whether the test itself is wrong.' }
  return { next: 'run_coder', reason: 'Budget left — send the feedback back to the coder for another attempt.' }
}

export type AfterCoder = 'run_review' | 'run_coder' | 'handle_failure'

export function afterCoder(success: boolean, task: TaskCounters, b: Budgets): Decision<AfterCoder> {
  if (success) return { next: 'run_review', reason: 'Code written against the existing test — run the gate.' }
  if (task.coderFixAttempts >= b.maxCoderFixAttempts)
    return { next: 'handle_failure', reason: `Coder crashed ${task.coderFixAttempts}× — out of attempts.` }
  return { next: 'run_coder', reason: 'Coder crashed; retry with the error as feedback.' }
}

export interface PlanTask {
  id: string
  dependsOn: string[]
  status: 'pending' | 'coding' | 'completed' | 'failed'
}

export function afterTaskSelection(hasTask: boolean): Decision<string> {
  return hasTask
    ? { next: 'generate_task_test', reason: 'Write the failing acceptance test before any code.' }
    : { next: 'create_pr', reason: 'No runnable tasks left.' }
}

/** Every open task that needs `taskId`, directly or transitively (port of dependents_of). */
export function dependentsOf(taskId: string, tasks: PlanTask[]): string[] {
  const blocked = new Set<string>()
  let grew = true
  while (grew) {
    grew = false
    for (const t of tasks) {
      if (blocked.has(t.id) || t.id === taskId || t.status === 'completed' || t.status === 'failed') continue
      if (t.dependsOn.some((d) => d === taskId || blocked.has(d))) {
        blocked.add(t.id)
        grew = true
      }
    }
  }
  return [...blocked].sort()
}
