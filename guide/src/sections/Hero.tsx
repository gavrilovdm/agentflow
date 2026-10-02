import { calls, finalTasks, pr, run, runEnd, runStart } from '../lib/data'
import { Term } from '../components/ui'

const llm = calls.filter((c) => c.type === 'llm')
const tokens = llm.reduce((s, c) => s + (c.type === 'llm' ? (c.tokens ?? 0) : 0), 0)
const toolRuns = calls.filter((c) => c.type === 'tool').length

const PIPE = [
  { k: 'Ask', d: 'one sentence, an issue, or a chat message' },
  { k: 'Spec + plan', d: 'Opus writes them; you approve' },
  { k: 'Test → code', d: 'a failing test first, then DeepSeek makes it pass' },
  { k: 'Check', d: 'tests & lint, then an AI reviewer' },
  { k: 'PR', d: 'commits, verification, pull request' },
]

export function Hero() {
  const seconds = Math.round((runEnd - runStart) / 1000)
  return (
    <header className="mx-auto max-w-6xl px-4 pb-6 pt-14 sm:px-6 sm:pt-20">
      <p className="text-xs font-semibold uppercase tracking-widest text-accent">agentflow, explained</p>
      <h1 className="mt-2 max-w-3xl text-3xl font-bold leading-tight tracking-tight sm:text-5xl">
        From a feature request to a reviewed pull request — and every step in between.
      </h1>
      <p className="mt-5 max-w-2xl text-[16px] leading-relaxed text-muted">
        agentflow is an <Term t="agent">AI agent</Term> system that actually does work: it plans, writes code, runs the
        tests, gets reviewed, and opens a PR — pausing for a human where it matters. This guide walks through{' '}
        <b className="text-ink">one real run</b>, then explains each idea with something you can poke at. Dotted words
        have a one-line definition on hover.
      </p>

      <div className="mt-9 grid grid-cols-2 gap-3 sm:grid-cols-5">
        {[
          [`${seconds}s`, 'request → PR'],
          [String(finalTasks.length), 'tasks, all passed first try'],
          [String(llm.length), 'model calls'],
          [String(toolRuns), 'tool calls'],
          [`${(tokens / 1000).toFixed(0)}k`, 'tokens'],
        ].map(([v, l]) => (
          <div key={l} className="rounded-xl border border-line bg-panel px-4 py-3">
            <div className="text-2xl font-bold tabular-nums">{v}</div>
            <div className="text-[12px] text-muted">{l}</div>
          </div>
        ))}
      </div>
      <p className="mt-2 text-[12px] text-muted">
        Real numbers from run <code>{run.thread_id}</code>, which opened{' '}
        <a className="text-accent hover:underline" href={pr.url} target="_blank" rel="noreferrer">
          PR #{pr.number}
        </a>{' '}
        on a sandbox repo.
      </p>

      <ol className="mt-10 grid gap-3 sm:grid-cols-5">
        {PIPE.map((p, i) => (
          <li key={p.k} className="relative rounded-xl border border-line bg-panel p-4">
            <span className="text-[11px] font-semibold text-accent">0{i + 1}</span>
            <div className="mt-1 font-semibold">{p.k}</div>
            <div className="mt-1 text-[13px] leading-snug text-muted">{p.d}</div>
            {i < PIPE.length - 1 && <span className="flowing absolute -right-3 top-1/2 hidden h-0.5 w-3 sm:block" />}
          </li>
        ))}
      </ol>
    </header>
  )
}
