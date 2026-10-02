import { Card, CopyCode, FileLink, Section } from '../components/ui'
import { BUGS, CODE_MAP, INTERVIEW, NODES, USE_CASES } from '../lib/content'
import { failedRuns } from '../lib/data'

export function UseCases() {
  return (
    <Section
      id="use-cases"
      kicker="3 · How people use it"
      title="Four ways in"
      intro="Same graph, same state, different front doors. Every write path only enqueues a job; a worker does the long-running part."
    >
      <div className="grid gap-4 md:grid-cols-2">
        {USE_CASES.map((u) => (
          <Card key={u.id}>
            <div className="text-[12px] text-muted">{u.who}</div>
            <h3 className="mt-0.5 text-lg font-semibold">{u.title}</h3>
            <ol className="mt-3 list-decimal space-y-1.5 pl-5 text-[14px] leading-relaxed">
              {u.steps.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ol>
            <div className="mt-4">
              <CopyCode>{u.code}</CopyCode>
            </div>
          </Card>
        ))}
      </div>
    </Section>
  )
}

export function Failures() {
  return (
    <Section
      id="what-broke"
      kicker="4 · Reality check"
      title="What broke the first time it ran for real"
      intro={`Every offline test was green. Then the first live runs found 7 bugs, and building this guide from their data found ${BUGS.length - 7} more. Each one is fixed and has a test. Where the failing run is still in the database, you see its actual steps.`}
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
              <a className="mt-2 inline-block font-mono text-[12px] text-muted hover:text-accent" href={`https://github.com/gavrilovdm/agentflow/commit/${b.commit}`} target="_blank" rel="noreferrer">
                commit {b.commit}
              </a>
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

export function CodeMap() {
  return (
    <Section id="code-map" kicker="5 · Find your way" title="Code map" intro="Where each idea lives. All links open the file on GitHub.">
      <div className="overflow-hidden rounded-xl border border-line">
        <table className="w-full text-left text-[14px]">
          <tbody>
            {CODE_MAP.map((r) => (
              <tr key={r.concept} className="border-b border-line last:border-0 odd:bg-panel even:bg-panel-2/50">
                <td className="w-2/5 px-4 py-2.5 align-top font-medium">{r.concept}</td>
                <td className="px-4 py-2.5">
                  <div className="flex flex-col gap-0.5">
                    {r.files.map((f) => (
                      <FileLink key={f} path={f} />
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  )
}

export function Interview() {
  return (
    <Section
      id="interview"
      kicker="6 · Explaining it"
      title="If someone asks you about it"
      intro="Short, honest answers grounded in what the code actually does. Open the ones you want to rehearse."
    >
      <div className="space-y-2">
        {INTERVIEW.map((x) => (
          <details key={x.q} className="group rounded-xl border border-line bg-panel px-5 py-3">
            <summary className="cursor-pointer list-none font-medium">
              <span className="mr-2 text-accent transition group-open:rotate-90 inline-block">›</span>
              {x.q}
            </summary>
            <p className="mt-2 pl-5 text-[14px] leading-relaxed text-muted">{x.a}</p>
          </details>
        ))}
      </div>
    </Section>
  )
}
