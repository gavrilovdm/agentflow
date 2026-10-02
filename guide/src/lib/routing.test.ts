// Same cases as tests/test_routing.py (CFG = max_review_cycles=2, max_gate_failures=3, max_coder_fix_attempts=2).
import { describe, expect, it } from 'vitest'
import { afterCoder, afterReview, dependentsOf, type Budgets, type PlanTask, type TaskCounters } from './routing'

const CFG: Budgets = { maxCoderFixAttempts: 2, maxReviewCycles: 2, maxGateFailures: 3, maxReviewerMalfunctions: 3 }
const task = (o: Partial<TaskCounters> = {}): TaskCounters => ({
  coderFixAttempts: 0, reviewCycles: 0, gateFailures: 0, reviewerMalfunctions: 0, ...o,
})
const review = (o: Partial<{ approved: boolean; stallRepeats: number; adjudicated: boolean }> = {}) => ({
  approved: false, stallRepeats: 0, adjudicated: false, ...o,
})

describe('routing parity with graph/routing.py', () => {
  it('approved review completes', () => {
    expect(afterReview(task(), review({ approved: true }), CFG).next).toBe('complete_task')
  })
  it('budgets are separate', () => {
    expect(afterReview(task({ gateFailures: 2 }), review(), CFG).next).toBe('run_coder')
    expect(afterReview(task({ gateFailures: 3 }), review(), CFG).next).toBe('handle_failure')
    expect(afterReview(task({ reviewCycles: 2 }), review(), CFG).next).toBe('handle_failure')
  })
  it('stall goes to the referee once', () => {
    expect(afterReview(task(), review({ stallRepeats: 2 }), CFG).next).toBe('adjudicate')
    expect(afterReview(task(), review({ stallRepeats: 2, adjudicated: true }), CFG).next).toBe('handle_failure')
  })
  it('reviewer malfunctions are capped', () => {
    expect(afterReview(task({ reviewerMalfunctions: 2 }), review(), CFG).next).toBe('run_coder')
    expect(afterReview(task({ reviewerMalfunctions: 3 }), review(), CFG).next).toBe('handle_failure')
  })
  it('coder attempt budget', () => {
    expect(afterCoder(false, task({ coderFixAttempts: 1 }), CFG).next).toBe('run_coder')
    expect(afterCoder(false, task({ coderFixAttempts: 2 }), CFG).next).toBe('handle_failure')
  })
  it('dependents are transitive and skip closed tasks', () => {
    const t = (id: string, deps: string[] = [], status: PlanTask['status'] = 'pending'): PlanTask => ({ id, dependsOn: deps, status })
    const tasks = [t('a'), t('b', ['a']), t('c', ['b']), t('d'), t('e', ['a'], 'completed')]
    expect(dependentsOf('a', tasks)).toEqual(['b', 'c'])
  })
})
