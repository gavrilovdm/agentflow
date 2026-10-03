import { Section } from '../components/ui'
import { INTERVIEW } from '../lib/content'

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
