import { useEffect, useMemo, useState } from 'react'
import { GraphView } from '../components/GraphView'
import { Card, Code, FileLink, Pill, Section, actorColor } from '../components/ui'
import { NODES } from '../lib/content'
import { addedLines, callsDuring, pr, run, ts, type Call, type ReviewCycle, type Spec, type Task } from '../lib/data'

// Which task each step belongs to (current_task_id is only present in the deltas that change it).
const STEP_TASK: (string | null)[] = (() => {
  let cur: string | null = null
  return run.steps.map((s) => {
    if ('current_task_id' in s.delta) cur = (s.delta.current_task_id as string | null) ?? null
    return cur
  })
})()

const dur = (i: number) => (ts(run.steps[i].ended) - ts(run.steps[i].started)) / 1000

export function Replay() {
  // ?step=N deep-links into the replay (1-based, like the counter).
  const [i, setI] = useState(() => {
    const n = Number(new URLSearchParams(window.location.search).get('step'))
    return Number.isInteger(n) && n >= 1 && n <= run.steps.length ? n - 1 : 0
  })
  const [playing, setPlaying] = useState(false)
  const step = run.steps[i]
  const info = NODES[step.node]

  useEffect(() => {
    if (!playing) return
    if (i >= run.steps.length - 1) {
      setPlaying(false)
      return
    }
    const t = setTimeout(() => setI((x) => x + 1), 1800)
    return () => clearTimeout(t)
  }, [playing, i])

  const total = useMemo(() => run.steps.reduce((s, _, k) => s + Math.max(dur(k), 0.6), 0), [])

  return (
    <Section
      id="replay"
      kicker="1 · Watch it work"
      title="Replay of a real run"
      intro={
        <>
          The request: <i>“{run.steps[0].delta.user_prompt as string}”</i>. Each step below is a real saved snapshot
          (checkpoint) of the run, with the actual model and tool calls that happened during it. Step through, or press
          play. This recording predates the switch to test-first, so here the code comes before its test.
        </>
      }
    >
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <button className="rounded-lg border border-line bg-panel px-3 py-1.5 text-sm disabled:opacity-40" disabled={i === 0} onClick={() => setI(i - 1)}>
          ← Prev
        </button>
        <button className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white" onClick={() => (i >= run.steps.length - 1 ? (setI(0), setPlaying(true)) : setPlaying(!playing))}>
          {playing ? 'Pause' : i >= run.steps.length - 1 ? 'Replay' : 'Play'}
        </button>
        <button className="rounded-lg border border-line bg-panel px-3 py-1.5 text-sm disabled:opacity-40" disabled={i === run.steps.length - 1} onClick={() => setI(i + 1)}>
          Next →
        </button>
        <span className="ml-2 text-sm text-muted tabular-nums">
          step {i + 1} / {run.steps.length}
        </span>
      </div>

      {/* Timeline: segment width ∝ real duration, colour = who did the work */}
      <div className="mb-6 flex h-3 w-full overflow-hidden rounded-full border border-line" role="tablist" aria-label="Run timeline">
        {run.steps.map((s, k) => (
          <button
            key={k}
            role="tab"
            aria-selected={k === i}
            title={`${NODES[s.node]?.title ?? s.node} · ${dur(k).toFixed(1)}s`}
            onClick={() => setI(k)}
            style={{
              width: `${(Math.max(dur(k), 0.6) / total) * 100}%`,
              background: actorColor(NODES[s.node]?.who ?? ''),
              opacity: k === i ? 1 : k < i ? 0.55 : 0.18,
            }}
            className="h-full border-r border-panel last:border-0"
          />
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <GraphView active={step.node} previous={i > 0 ? run.steps[i - 1].node : undefined} />
        <Card className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Pill color={actorColor(info?.who ?? '')}>{info?.who}</Pill>
            <Pill>{dur(i).toFixed(1)} s</Pill>
            {STEP_TASK[i] && <Pill>{STEP_TASK[i]}</Pill>}
            {info?.concept && (
              <a href={`#concept-${info.concept}`} className="ml-auto text-[12px] text-accent hover:underline">
                concept: {info.concept} →
              </a>
            )}
          </div>
          <h3 className="mt-3 text-lg font-semibold">{info?.title ?? step.node}</h3>
          <p className="mt-1 text-[14px] leading-relaxed text-muted">{info?.what}</p>
          <div className="mt-1">
            <FileLink path={info?.file ?? ''} label={`node: ${step.node}`} />
          </div>
          <div className="mt-4">
            <Artifact i={i} />
          </div>
          <CallList calls={callsDuring(step)} />
        </Card>
      </div>
    </Section>
  )
}

function Artifact({ i }: { i: number }) {
  const s = run.steps[i]
  const d = s.delta
  const task = STEP_TASK[i]
  switch (s.node) {
    case '__start__':
      return <Code>{d.user_prompt as string}</Code>
    case 'prepare_workspace': {
      const st = d.index_stats as Record<string, number>
      return (
        <p className="text-[14px]">
          Cloned <b>{d.repo_id as string}</b> onto branch <code>{d.branch as string}</code>. Index: {st.files_seen} files
          seen, <b>{st.files_embedded}</b> re-embedded ({st.chunks_embedded} chunks) — everything else was unchanged since
          the previous run, so it was skipped.
        </p>
      )
    }
    case 'generate_spec':
      return <SpecCard spec={d.spec as Spec} />
    case 'await_spec_approval':
    case 'await_task_approval':
      return (
        <p className="text-[14px]">
          The run paused here (an <b>interrupt</b>) and was resumed by an approval through the REST API. In production
          this is a Telegram button or an MCP call. Nothing is held in memory while waiting — the state lives in Postgres.
        </p>
      )
    case 'generate_tasks':
      return <TaskList tasks={d.tasks as Task[]} />
    case 'select_next_task':
      return task ? (
        <p className="text-[14px]">
          Next runnable task: <b>{task}</b> — its dependencies are complete.
        </p>
      ) : (
        <p className="text-[14px]">No pending tasks left → route to <b>create_pr</b>.</p>
      )
    case 'run_coder': {
      const res = (d.task_results as Record<string, { written_files: Record<string, string> }>)[task ?? '']
      const files = Object.entries(res?.written_files ?? {})
      return (
        <div>
          <p className="mb-2 text-[14px]">The coder wrote {files.length} file(s) — the full file as it saved it:</p>
          {files.map(([p, c]) => (
            <div key={p} className="mb-2">
              <div className="mb-1 font-mono text-[12px] text-muted">{p}</div>
              <Code lang="py">{c}</Code>
            </div>
          ))}
        </div>
      )
    }
    case 'generate_task_test': {
      const path = (d.tests as Record<string, string>)[task ?? '']
      return (
        <div>
          <p className="mb-2 text-[14px]">
            Acceptance test written to <code>{path}</code>, derived from the task’s Definition of Done:
          </p>
          <Code lang="py">{addedLines(path)}</Code>
        </div>
      )
    }
    case 'run_review': {
      const r = (d.review_results as Record<string, { cycles: ReviewCycle[] }>)[task ?? '']
      const c = r?.cycles.at(-1)
      if (!c) return null
      return (
        <div className="text-[14px]">
          <p>
            Gate (pytest + ruff): <b className="text-ok">passed</b>. Reviewer verdict:{' '}
            <b className={c.verdict === 'approved' ? 'text-ok' : 'text-bad'}>{c.verdict}</b>
          </p>
          <blockquote className="mt-2 border-l-2 border-line pl-3 text-muted">{c.comments}</blockquote>
        </div>
      )
    }
    case 'complete_task': {
      const commit = pr.commits.find((c) => c.messageHeadline.includes(task ?? '~'))
      return (
        <p className="text-[14px]">
          Committed code + test as <code>{commit?.messageHeadline ?? `feat(${task})`}</code>, re-indexed the repo, and
          checked review feedback for lessons worth remembering (none this time — nothing was rejected).
        </p>
      )
    }
    case 'create_pr':
      return (
        <div className="text-[14px]">
          <p>
            Full test suite re-run, branch pushed, PR opened:{' '}
            <a className="text-accent hover:underline" href={pr.url} target="_blank" rel="noreferrer">
              {pr.title}
            </a>
          </p>
          <ul className="mt-2 space-y-0.5 font-mono text-[12px] text-muted">
            {pr.files.map((f) => (
              <li key={f.path}>
                +{f.additions} {f.path}
              </li>
            ))}
          </ul>
        </div>
      )
    default:
      return null
  }
}

function SpecCard({ spec }: { spec: Spec }) {
  return (
    <div className="text-[14px]">
      <div className="font-semibold">{spec.title}</div>
      <p className="mt-1 text-muted">{spec.goal}</p>
      <div className="mt-2 text-[12px] font-semibold uppercase tracking-wide text-muted">Acceptance criteria</div>
      <ul className="mt-1 max-h-48 list-disc space-y-1 overflow-auto pl-5">
        {spec.acceptance_criteria.map((c) => (
          <li key={c}>{c}</li>
        ))}
      </ul>
    </div>
  )
}

function TaskList({ tasks }: { tasks: Task[] }) {
  return (
    <ol className="space-y-2 text-[14px]">
      {tasks.map((t) => (
        <li key={t.id} className="rounded-lg border border-line p-3">
          <div className="font-mono text-[12px] text-accent">{t.id}</div>
          <div className="font-medium">{t.title}</div>
          <div className="mt-1 text-[13px] text-muted">
            files: {t.target_files.join(', ')} {t.depends_on.length > 0 && <>· after: {t.depends_on.join(', ')}</>}
          </div>
        </li>
      ))}
    </ol>
  )
}

function CallList({ calls }: { calls: Call[] }) {
  if (calls.length === 0) return null
  return (
    <div className="mt-5 border-t border-line pt-4">
      <div className="text-[12px] font-semibold uppercase tracking-wide text-muted">Model & tool calls in this step (from the trace)</div>
      <ul className="mt-2 space-y-1.5">
        {calls.map((c, k) =>
          c.type === 'llm' ? (
            <li key={k}>
              <details className="rounded-lg border border-line px-3 py-1.5 text-[13px]">
                <summary className="cursor-pointer">
                  <b style={{ color: c.model?.startsWith('deepseek') ? 'var(--coder)' : 'var(--opus)' }}>{c.model}</b>{' '}
                  <span className="text-muted">
                    {c.latency_s}s · {c.tokens} tok →{' '}
                  </span>
                  {c.output.tool_calls.length ? (
                    c.output.tool_calls.map((t) => <code key={t.name + k} className="mr-1">{t.name}()</code>)
                  ) : (
                    <span className="text-muted">text answer</span>
                  )}
                </summary>
                <Code maxH="max-h-60">
                  {c.output.tool_calls.length
                    ? JSON.stringify(c.output.tool_calls, null, 2)
                    : c.output.content || '(empty)'}
                </Code>
              </details>
            </li>
          ) : (
            <li key={k} className="pl-3 text-[13px] text-muted">
              ↳ ran <code className="text-ink">{c.name}</code> → {c.output.slice(0, 90)}
              {c.output.length > 90 ? '…' : ''}
            </li>
          ),
        )}
      </ul>
    </div>
  )
}
