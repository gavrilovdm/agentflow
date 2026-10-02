// Typed access to the real data captured by scripts/export_guide_data.py.
import failedRunsJson from '../data/failed_runs.json'
import graphJson from '../data/graph.json'
import llmCallsJson from '../data/llm_calls.json'
import prJson from '../data/pr.json'
import retrievalJson from '../data/retrieval.json'
import runJson from '../data/run.json'

export interface Spec {
  id: string
  version: number
  title: string
  goal: string
  constraints: string[]
  acceptance_criteria: string[]
  technical_notes: string
  out_of_scope: string[]
}

export interface Task {
  id: string
  title: string
  description: string
  target_files: string[]
  definition_of_done: string
  depends_on: string[]
  status: string
  coder_fix_attempts: number
  review_cycles: number
  gate_failures: number
}

export interface ReviewCycle {
  verdict: 'approved' | 'changes_requested' | 'failed'
  comments: string
  change_requests: string[]
  reviewer_malfunction: boolean
}

export interface Step {
  node: string
  started: string
  ended: string
  delta: Record<string, unknown>
}

export interface LlmCall {
  type: 'llm'
  t: string
  latency_s: number | null
  model: string | null
  tags: string[]
  tokens: number | null
  input_tail: { role: string; content: string; tool_calls: ToolCall[] }[]
  output: { role: string; content: string; tool_calls: ToolCall[] }
}

export interface ToolCall {
  name: string
  args: Record<string, unknown>
}

export interface ToolRun {
  type: 'tool'
  t: string
  name: string
  input: string
  output: string
}

export type Call = LlmCall | ToolRun

export const graph = graphJson as { nodes: string[]; edges: { source: string; target: string; conditional: boolean }[] }
export const run = runJson as unknown as { thread_id: string; steps: Step[]; final: Record<string, unknown> }
export const calls = llmCallsJson as unknown as Call[]
export const pr = prJson as {
  number: number
  title: string
  url: string
  body: string
  diff: string
  files: { path: string; additions: number; deletions: number }[]
  commits: { messageHeadline: string; oid: string }[]
}
export const retrieval = retrievalJson as {
  k: number
  embeddings: string
  summary: Record<'dense' | 'lexical' | 'hybrid', { recall: number; mrr: number }>
  cases: { query: string; relevant: string[]; dense: string[]; lexical: string[]; hybrid: string[] }[]
  chunk_demo: { path: string; chunks: { index: number; start: number; end: number; content: string }[] }
}
export const failedRuns = failedRunsJson as Record<
  string,
  {
    thread_id: string
    status: string | null
    error: string
    steps: { node: string; t: string; verdict: { verdict: string; malfunction: boolean; comment: string } | null }[]
    tasks: { id: string; gate_failures: number; review_cycles: number }[]
  }
>

export const finalSpec = run.final.spec as Spec
export const finalTasks = run.final.tasks as Task[]

/** Timestamps come from two systems (Postgres checkpoints, LangSmith); normalise to epoch ms. */
export function ts(s: string): number {
  return Date.parse(s.replace(' ', 'T'))
}

export const runStart = ts(run.steps[0].started)
export const runEnd = ts(run.steps[run.steps.length - 1].ended)

/** LLM and tool calls that happened while a given graph step was running. */
export function callsDuring(step: Step): Call[] {
  const a = ts(step.started)
  const b = ts(step.ended)
  return calls.filter((c) => {
    const t = ts(c.t)
    return t >= a && t < b
  })
}

/** Files of the PR diff, split per file, for showing real code. */
export function diffByFile(): Record<string, string> {
  const out: Record<string, string> = {}
  for (const part of pr.diff.split(/^diff --git /m).filter(Boolean)) {
    const m = part.match(/^a\/(\S+)/)
    if (m) out[m[1]] = 'diff --git ' + part
  }
  return out
}

/** Added lines of a file in the PR — i.e. what the agent wrote. */
export function addedLines(path: string): string {
  const d = diffByFile()[path] ?? ''
  return d
    .split('\n')
    .filter((l) => l.startsWith('+') && !l.startsWith('+++'))
    .map((l) => l.slice(1))
    .join('\n')
}

export const GITHUB = 'https://github.com/gavrilovdm/agentflow/blob/main/'
