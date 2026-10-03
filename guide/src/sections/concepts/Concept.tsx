import type { ReactNode } from 'react'
import { FileLink } from '../../components/ui'
import type { ConceptId } from '../../lib/content'

/** Layout for one concept: plain-language explanation and code links on the left, widget on the right. */
export function Concept({ id, title, plain, where, children }: { id: ConceptId; title: string; plain: ReactNode; where: string[]; children: ReactNode }) {
  return (
    <article id={`concept-${id}`} className="grid gap-6 border-t border-line py-10 first:border-0 first:pt-0 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
      <div>
        <h3 className="text-xl font-semibold">{title}</h3>
        <div className="mt-2 space-y-3 text-[15px] leading-relaxed text-muted">{plain}</div>
        <div className="mt-4 flex flex-col gap-1">
          <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">Where in the code</span>
          {where.map((w) => (
            <FileLink key={w} path={w} />
          ))}
        </div>
      </div>
      <div className="min-w-0">{children}</div>
    </article>
  )
}
