import { Card, CopyCode, Section } from '../components/ui'
import { USE_CASES } from '../lib/content'

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
