import { Card, FileLink, Pill, actorColor } from '../../components/ui'
import { NODES } from '../../lib/content'
import type { Step } from '../../lib/data'
import { callsDuring, durationSeconds, type RunRecord } from '../../lib/run'
import { Artifact } from './artifacts'
import { CallList } from './CallList'

/** Details of the current step: who acted, what it means, what it produced, which calls it made. */
export function StepPanel({ run, step, task }: { run: RunRecord; step: Step; task: string | null }) {
  const info = NODES[step.node]
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <Pill color={actorColor(info?.who ?? '')}>{info?.who}</Pill>
        <Pill>{durationSeconds(step).toFixed(1)} s</Pill>
        {task && step.node !== '__end__' && <Pill>{task}</Pill>}
        {step.delta.simulated === true && <Pill color="var(--human)">simulated</Pill>}
        {info?.concept && (
          <a href={`#concept-${info.concept}`} className="ml-auto text-[12px] text-accent hover:underline">
            concept: {info.concept} →
          </a>
        )}
      </div>
      <h3 className="mt-3 text-lg font-semibold">{info?.title ?? step.node}</h3>
      <p className="mt-1 text-[14px] leading-relaxed text-muted">{info?.what}</p>
      {info?.file && (
        <div className="mt-1">
          <FileLink path={info.file} label={`node: ${step.node}`} />
        </div>
      )}
      <div className="mt-4">
        <Artifact step={step} task={task} />
      </div>
      <CallList calls={callsDuring(run, step)} />
    </Card>
  )
}

/** Explore mode: what a node does, and which scenarios reach it. */
export function NodePanel({ node, reachedIn, onClose }: { node: string; reachedIn: string[]; onClose: () => void }) {
  const info = NODES[node]
  return (
    <Card>
      <div className="flex items-center gap-2">
        <Pill color={actorColor(info?.who ?? '')}>{info?.who}</Pill>
        <span className="text-[12px] text-muted">exploring</span>
        <button type="button" onClick={onClose} className="ml-auto rounded-md border border-line px-2 py-0.5 text-[12px] text-muted hover:text-ink">
          back to the replay
        </button>
      </div>
      <h3 className="mt-3 text-lg font-semibold">{info?.title ?? node}</h3>
      <p className="mt-1 text-[14px] leading-relaxed text-muted">{info?.what}</p>
      {info?.file && (
        <div className="mt-1">
          <FileLink path={info.file} label={`node: ${node}`} />
        </div>
      )}
      <p className="mt-4 text-[13px]">
        {reachedIn.length ? (
          <>Reached in: {reachedIn.join(', ')}. Pick that scenario above to see it happen.</>
        ) : (
          <>Not reached in the current scenario.</>
        )}
      </p>
    </Card>
  )
}
