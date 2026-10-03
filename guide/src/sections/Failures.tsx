import { Card, Section } from '../components/ui'
import { BUGS, NODES } from '../lib/content'
import { failedRuns } from '../lib/data'

export function Failures() {
  return (
    <Section
      id="what-broke"
      kicker="4 · Reality check"
      title="What broke the first time it ran for real"
      intro={`Every offline test was green. Then the first live runs found 7 bugs; building this guide and switching to test-first found ${BUGS.length - 7} more. Each one is fixed and has a test. Where the failing run is still in the database, you see its actual steps.`}
    >
      <div className="grid gap-4 md:grid-cols-2">
        {BUGS.map((b, n) => {
          const r = b.run ? failedRuns[b.run] : undefined
          return (
            <Card key={b.id}>
              <div className="flex items-baseline gap-2">
                <span className="text-[12px] font-semibold text-bad">#{n + 1}</span>
                <h3 className="font-semibold">{b.title}</h3>
              </div>
              <dl className="mt-3 space-y-2 text-[14px] leading-relaxed">
                <div><dt className="inline font-medium">Symptom: </dt><dd className="inline text-muted">{b.symptom}</dd></div>
                <div><dt className="inline font-medium">Cause: </dt><dd className="inline text-muted">{b.cause}</dd></div>
                <div><dt className="inline font-medium">Fix: </dt><dd className="inline text-muted">{b.fix}</dd></div>
              </dl>
              {r && r.steps.length > 0 && <RunStrip steps={r.steps} />}
              <p className="mt-3 rounded-lg bg-accent-soft p-3 text-[13px] text-accent">{b.lesson}</p>
              {/^[0-9a-f]{7}$/.test(b.commit) ? (
                <a className="mt-2 inline-block font-mono text-[12px] text-muted hover:text-accent" href={`https://github.com/gavrilovdm/agentflow/commit/${b.commit}`} target="_blank" rel="noreferrer">
                  commit {b.commit}
                </a>
              ) : (
                <span className="mt-2 inline-block font-mono text-[12px] text-muted">{b.commit}</span>
              )}
            </Card>
          )
        })}
      </div>
    </Section>
  )
}

function RunStrip({ steps }: { steps: { node: string; verdict: { verdict: string; malfunction: boolean } | null }[] }) {
  const shown = steps.slice(0, 40)
  return (
    <div className="mt-3">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-muted">the actual run, step by step ({steps.length} steps)</div>
      <div className="mt-1.5 flex flex-wrap gap-1">
        {shown.map((s, i) => {
          const v = s.verdict
          const color = v ? (v.verdict === 'approved' ? 'var(--ok)' : 'var(--bad)') : 'var(--line)'
          return (
            <span
              key={i}
              title={`${NODES[s.node]?.title ?? s.node}${v ? ` → ${v.verdict}${v.malfunction ? ' (reviewer malfunction)' : ''}` : ''}`}
              className="rounded px-1.5 py-0.5 font-mono text-[10px]"
              style={{ border: `1px solid ${color}`, color: v ? color : 'var(--muted)' }}
            >
              {s.node.replace('generate_', 'gen_').replace('_task', '')}
            </span>
          )
        })}
      </div>
    </div>
  )
}
