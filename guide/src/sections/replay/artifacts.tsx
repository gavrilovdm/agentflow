// What to show for a step, per node. A registry instead of a switch: supporting a new node
// means adding an entry here, not editing the replay. Unknown nodes fall back to a note.
import type { ReactNode } from 'react'
import { Code } from '../../components/ui'
import { addedLines, pr, type ReviewCycle, type Spec, type Step, type Task } from '../../lib/data'

export interface ArtifactProps {
  step: Step
  task: string | null
}

type Renderer = (p: ArtifactProps) => ReactNode

const P = ({ children }: { children: ReactNode }) => <p className="text-[14px] leading-relaxed">{children}</p>

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
          {t.interface && <pre className="mt-2 overflow-auto rounded bg-panel-2 p-2 text-[11px]">{t.interface}</pre>}
        </li>
      ))}
    </ol>
  )
}

interface Dossier {
  task: { id: string; title: string }
  attempts: Record<string, number | boolean>
  recent_failures: string[]
  open_change_requests: string[]
  dependents: string[]
  files_written: string[]
}

function Escalation({ step, task }: ArtifactProps) {
  const ask = step.interrupt as { dossier?: Dossier; options?: string[] } | undefined
  const d = ask?.dossier
  const decision = (step.delta.failure_decisions as Record<string, { action: string; hint?: string | null; auto?: boolean }> | undefined)?.[task ?? '']
  return (
    <div className="space-y-3 text-[14px]">
      {d && (
        <div className="rounded-lg border border-line bg-panel-2 p-3 text-[13px]">
          <div className="font-semibold">Dossier sent to the human</div>
          <div className="mt-1 text-muted">
            {Object.entries(d.attempts)
              .map(([k, v]) => `${k.replaceAll('_', ' ')}: ${v}`)
              .join(' · ')}
          </div>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {d.recent_failures.map((f) => (
              <li key={f}>{f.split('\n')[0].slice(0, 220)}</li>
            ))}
          </ul>
          {d.dependents.length > 0 && <div className="mt-2 text-muted">Blocked if skipped: {d.dependents.join(', ')}</div>}
          <div className="mt-1 text-muted">Options: {ask?.options?.join(' · ')}</div>
        </div>
      )}
      {decision && (
        <P>
          Decision: <b>{decision.action}</b>
          {decision.auto ? ' (automatic — unattended mode or escalation cap)' : ''}
          {decision.hint ? (
            <>
              {' '}
              with guidance: <i>“{decision.hint}”</i>
            </>
          ) : null}
        </P>
      )}
    </div>
  )
}

const RENDERERS: Record<string, Renderer> = {
  __start__: ({ step }) => <Code>{step.delta.user_prompt as string}</Code>,
  prepare_workspace: ({ step }) => {
    const st = step.delta.index_stats as Record<string, number> | undefined
    if (!st) return null
    return (
      <P>
        Cloned <b>{step.delta.repo_id as string}</b> onto branch <code>{step.delta.branch as string}</code>. Index:{' '}
        {st.files_seen} files seen, <b>{st.files_embedded}</b> re-embedded ({st.chunks_embedded} chunks); unchanged files
        were skipped.
      </P>
    )
  },
  generate_spec: ({ step }) => (step.delta.spec ? <SpecCard spec={step.delta.spec as Spec} /> : null),
  await_spec_approval: () => <P>Paused (an <b>interrupt</b>) until a human approved — here via the REST API. Nothing is held in memory while waiting; the state lives in Postgres.</P>,
  await_task_approval: () => <P>Second checkpoint: the plan was approved the same way.</P>,
  generate_tasks: ({ step }) => (step.delta.tasks ? <TaskList tasks={step.delta.tasks as Task[]} /> : null),
  select_next_task: ({ task }) => (task ? <P>Next runnable task: <b>{task}</b> — its dependencies are complete.</P> : <P>No pending tasks left → <b>create_pr</b> (or the end of a stopped run).</P>),
  run_coder: ({ step, task }) => {
    const res = (step.delta.task_results as Record<string, { written_files: Record<string, string>; error?: string | null }> | undefined)?.[task ?? '']
    if (!res) return null
    const files = Object.entries(res.written_files ?? {})
    return (
      <div>
        <P>The coder wrote {files.length} file(s):</P>
        {files.map(([p, c]) => (
          <div key={p} className="mt-2">
            <div className="mb-1 font-mono text-[12px] text-muted">{p}</div>
            <Code lang="py">{c}</Code>
          </div>
        ))}
      </div>
    )
  },
  generate_task_test: ({ step, task }) => {
    const path = (step.delta.tests as Record<string, string> | undefined)?.[task ?? '']
    if (!path) return null
    const body = addedLines(path)
    return (
      <div>
        <P>
          Acceptance test written to <code>{path}</code>.
        </P>
        {body && <Code lang="py">{body}</Code>}
      </div>
    )
  },
  run_review: ({ step, task }) => {
    const c = (step.delta.review_results as Record<string, { cycles: ReviewCycle[] }> | undefined)?.[task ?? '']?.cycles.at(-1)
    if (!c) return null
    const verdict = c.reviewer_malfunction ? 'reviewer malfunction' : c.verdict
    return (
      <div className="text-[14px]">
        <p>
          {c.verdict === 'failed' ? 'Gate failed' : 'Gate passed'} · verdict:{' '}
          <b className={c.verdict === 'approved' ? 'text-ok' : 'text-bad'}>{verdict}</b>
        </p>
        <blockquote className="mt-2 max-h-56 overflow-auto border-l-2 border-line pl-3 text-muted">{c.comments}</blockquote>
      </div>
    )
  },
  complete_task: ({ task }) => {
    const commit = pr.commits.find((c) => c.messageHeadline.includes(task ?? '~'))
    return <P>Committed code + test{commit ? <> as <code>{commit.messageHeadline}</code></> : ''}, re-indexed the repo, and stored any validated review feedback as lessons.</P>
  },
  escalate: Escalation,
  replan: ({ step }) => {
    const tasks = ((step.delta.tasks as Task[] | undefined) ?? []).filter((t) => t.status === 'pending')
    return (
      <div>
        <P>The remaining plan was rewritten around the failure. New tasks:</P>
        <div className="mt-2">
          <TaskList tasks={tasks} />
        </div>
      </div>
    )
  },
  handle_failure: ({ step }) => {
    const failed = Object.entries((step.delta.failed_tasks as Record<string, string> | undefined) ?? {})
    return (
      <div className="text-[14px]">
        <ul className="space-y-1">
          {failed.map(([id, why]) => (
            <li key={id}>
              <code>{id}</code> — <span className="text-muted">{why.split('\n')[0].slice(0, 200)}</span>
            </li>
          ))}
        </ul>
        {step.delta.status === 'failed' && <P>Nothing salvageable was left, so the run ends without a PR.</P>}
      </div>
    )
  },
  create_pr: () => (
    <P>
      Full test suite re-run, branch pushed, PR opened:{' '}
      <a className="text-accent hover:underline" href={pr.url} target="_blank" rel="noreferrer">
        {pr.title}
      </a>
    </P>
  ),
  __end__: ({ step }) => (
    <P>
      The run ended <b>{step.delta.status as string}</b>
      {step.delta.via === 'handle_failure' ? ' on the stop path.' : '.'}
    </P>
  ),
}

export function Artifact(props: ArtifactProps) {
  const note = props.step.delta.note as string | undefined
  if (props.step.delta.simulated) return note ? <P>{note}</P> : null
  const render = RENDERERS[props.step.node]
  return <>{render ? render(props) : note ? <P>{note}</P> : null}</>
}
