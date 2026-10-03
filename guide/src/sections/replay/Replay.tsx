import { useCallback, useEffect, useMemo, useState, type KeyboardEvent } from 'react'
import { GraphView } from '../../components/GraphView'
import { Section } from '../../components/ui'
import { taskPerStep, type RunRecord } from '../../lib/run'
import { SCENARIOS, type Scenario } from '../../lib/scenarios'
import { NodePanel, StepPanel } from './StepPanel'
import { Timeline } from './Timeline'

const STEP_MS = 1800

function initialFromUrl(): { scenario: string; step: number } {
  const q = new URLSearchParams(window.location.search)
  const scenario = SCENARIOS.some((s) => s.id === q.get('scenario')) ? (q.get('scenario') as string) : SCENARIOS[0].id
  const n = Number(q.get('step'))
  return { scenario, step: Number.isInteger(n) && n >= 1 ? n - 1 : 0 }
}

/** Replays a run step by step over the graph. Scenarios are injected (lib/scenarios), so
 * the player itself knows nothing about any particular run. */
export function Replay() {
  const init = useMemo(initialFromUrl, [])
  const [scenarioId, setScenarioId] = useState(init.scenario)
  const scenario = SCENARIOS.find((s) => s.id === scenarioId) as Scenario
  const [run, setRun] = useState<RunRecord | null>(null)
  const [index, setIndex] = useState(init.step)
  const [playing, setPlaying] = useState(false)
  const [exploring, setExploring] = useState<string | null>(null)
  const [reach, setReach] = useState<Record<string, string[]>>({})

  useEffect(() => {
    let alive = true
    setRun(null)
    scenario.load().then((r) => {
      if (!alive) return
      setRun(r)
      setIndex((i) => Math.min(i, r.steps.length - 1))
    })
    return () => {
      alive = false
    }
  }, [scenario])

  // Which scenarios reach which node — for explore mode. Loaded on first use only: it
  // needs every scenario, which would otherwise download ~800 KB up front.
  const wantsReach = exploring !== null && Object.keys(reach).length === 0
  useEffect(() => {
    if (!wantsReach) return
    Promise.all(SCENARIOS.map(async (s) => [s.title, (await s.load()).steps.map((st) => st.node)] as const)).then((all) => {
      const map: Record<string, string[]> = {}
      for (const [title, nodes] of all) for (const n of new Set(nodes)) (map[n] ??= []).push(title)
      setReach(map)
    })
  }, [wantsReach])

  const last = (run?.steps.length ?? 1) - 1
  useEffect(() => {
    if (!playing) return
    if (index >= last) {
      setPlaying(false)
      return
    }
    const t = setTimeout(() => setIndex((x) => x + 1), STEP_MS)
    return () => clearTimeout(t)
  }, [playing, index, last])

  const go = useCallback((i: number) => {
    setExploring(null)
    setIndex(Math.max(0, Math.min(i, last)))
  }, [last])

  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'ArrowRight') go(index + 1)
    else if (e.key === 'ArrowLeft') go(index - 1)
    else if (e.key === ' ') {
      e.preventDefault()
      setPlaying((p) => !p)
    }
  }

  const tasks = useMemo(() => (run ? taskPerStep(run.steps) : []), [run])
  const step = run?.steps[index]
  // A run that stopped (handle_failure → END) is drawn ending at the "stopped" end node.
  const stepNode = step?.node === '__end__' && step.delta.via === 'handle_failure' ? '__end_stopped__' : step?.node

  return (
    <Section
      id="replay"
      kicker="1 · Watch it work"
      title="Replay a run"
      intro="Each step is a saved snapshot (checkpoint) of a real run, with the model and tool calls made during it. Pick a scenario, step through with ← →, or press play. Hover the graph to see a node's connections; click a node to read about it."
    >
      <div role="radiogroup" aria-label="Scenario" className="mb-3 flex flex-wrap gap-2">
        {SCENARIOS.map((s) => (
          <button
            key={s.id}
            type="button"
            role="radio"
            aria-checked={s.id === scenarioId}
            onClick={() => {
              setScenarioId(s.id)
              setIndex(0)
              setPlaying(false)
              setExploring(null)
            }}
            className={`rounded-full border px-3 py-1 text-[13px] ${s.id === scenarioId ? 'border-accent bg-accent-soft text-accent' : 'border-line text-muted hover:text-ink'}`}
          >
            {s.title}
            {s.kind === 'simulated' && <span className="ml-1 text-[11px] opacity-70">(simulated)</span>}
          </button>
        ))}
      </div>
      <p className="mb-4 max-w-3xl text-[14px] text-muted">
        {scenario.summary}
        {scenario.legacyGraph && ' Recorded before test-first, so here the code comes before its test.'}
        {run && !scenario.legacyGraph && scenario.kind === 'real' && <> Thread <code>{run.thread_id}</code>.</>}
      </p>

      <div tabIndex={0} onKeyDown={onKey} className="rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-accent">
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <button type="button" className="rounded-lg border border-line bg-panel px-3 py-1.5 text-sm disabled:opacity-40" disabled={index === 0} onClick={() => go(index - 1)}>
            ← Prev
          </button>
          <button
            type="button"
            className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white"
            disabled={!run}
            onClick={() => (index >= last ? (go(0), setPlaying(true)) : setPlaying(!playing))}
          >
            {playing ? 'Pause' : index >= last ? 'Replay' : 'Play'}
          </button>
          <button type="button" className="rounded-lg border border-line bg-panel px-3 py-1.5 text-sm disabled:opacity-40" disabled={index >= last} onClick={() => go(index + 1)}>
            Next →
          </button>
          <span className="ml-2 text-sm text-muted tabular-nums" aria-live="polite">
            {run ? `step ${index + 1} / ${run.steps.length}` : 'loading…'}
          </span>
        </div>

        {run && <Timeline steps={run.steps} index={index} onSelect={go} />}

        <div className="grid gap-6 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
          <GraphView
            active={exploring ?? stepNode}
            previous={exploring ? undefined : index > 0 ? run?.steps[index - 1].node : undefined}
            onSelect={setExploring}
          />
          <div className="min-w-0">
            {exploring ? (
              <NodePanel node={exploring} reachedIn={reach[exploring] ?? []} onClose={() => setExploring(null)} />
            ) : run && step ? (
              <StepPanel run={run} step={step} task={tasks[index]} />
            ) : null}
          </div>
        </div>
      </div>
    </Section>
  )
}
