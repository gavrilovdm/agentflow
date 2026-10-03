import { actorColor } from '../../components/ui'
import { NODES } from '../../lib/content'
import type { Step } from '../../lib/data'
import { durationSeconds } from '../../lib/run'

/** One segment per step; width ∝ real duration, colour = who did the work. */
export function Timeline({ steps, index, onSelect }: { steps: Step[]; index: number; onSelect: (i: number) => void }) {
  const weight = (s: Step) => Math.max(durationSeconds(s), 0.6)
  const total = steps.reduce((sum, s) => sum + weight(s), 0)
  return (
    <div className="mb-6 flex h-3 w-full overflow-hidden rounded-full border border-line" aria-label="Run timeline">
      {steps.map((s, k) => {
        const title = NODES[s.node]?.title ?? s.node
        return (
          <button
            key={`${k}-${s.node}`}
            type="button"
            aria-label={`Step ${k + 1}: ${title}, ${durationSeconds(s).toFixed(1)} seconds`}
            aria-current={k === index ? 'step' : undefined}
            title={`${title} · ${durationSeconds(s).toFixed(1)}s`}
            onClick={() => onSelect(k)}
            style={{
              width: `${(weight(s) / total) * 100}%`,
              background: actorColor(NODES[s.node]?.who ?? ''),
              opacity: k === index ? 1 : k < index ? 0.55 : 0.18,
            }}
            className="h-full border-r border-panel last:border-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
          />
        )
      })}
    </div>
  )
}
