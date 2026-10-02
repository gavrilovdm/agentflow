import { Background, MarkerType, ReactFlow, type Edge, type Node } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useMemo } from 'react'
import { NODES } from '../lib/content'
import { graph } from '../lib/data'
import { actorColor } from './ui'

// Hand-placed layout: the happy path runs down the middle, the per-task loop on the
// left/right, delivery on the far right. Edges come from the compiled graph (graph.json).
const POS: Record<string, [number, number]> = {
  __start__: [175, 0],
  prepare_workspace: [175, 62],
  generate_spec: [175, 124],
  await_spec_approval: [175, 186],
  generate_tasks: [175, 248],
  await_task_approval: [175, 310],
  select_next_task: [175, 384],
  generate_task_test: [175, 458],
  run_coder: [175, 532],
  run_review: [175, 606],
  adjudicate: [0, 606],
  escalate: [0, 532],
  replan: [0, 458],
  handle_failure: [0, 384],
  complete_task: [350, 606],
  create_pr: [350, 384],
  notify: [350, 310],
  finalize: [350, 248],
  __end__: [350, 186],
}

const LABEL: Record<string, string> = { __start__: 'start', __end__: 'end' }

export function GraphView({ active, previous, height = 600 }: { active?: string; previous?: string; height?: number }) {
  const nodes: Node[] = useMemo(
    () =>
      graph.nodes
        .filter((n) => POS[n])
        .map((n) => {
          const info = NODES[n]
          const isActive = n === active
          const color = info ? actorColor(info.who) : 'var(--muted)'
          return {
            id: n,
            position: { x: POS[n][0], y: POS[n][1] },
            data: { label: LABEL[n] ?? info?.title ?? n },
            draggable: false,
            selectable: false,
            style: {
              width: 150,
              fontSize: 12,
              padding: '6px 8px',
              borderRadius: 10,
              border: `${isActive ? 2 : 1}px solid ${isActive ? color : 'var(--line)'}`,
              background: isActive ? 'var(--accent-soft)' : 'var(--panel)',
              color: 'var(--ink)',
              boxShadow: isActive ? `0 0 0 4px color-mix(in srgb, ${color} 25%, transparent)` : 'none',
              fontWeight: isActive ? 600 : 400,
              transition: 'all .25s',
            },
          }
        }),
    [active],
  )

  const edges: Edge[] = useMemo(
    () =>
      graph.edges
        .filter((e) => e.source !== e.target && POS[e.source] && POS[e.target])
        .map((e) => {
          const hot = e.source === previous && e.target === active
          return {
            id: `${e.source}->${e.target}`,
            source: e.source,
            target: e.target,
            type: 'smoothstep',
            animated: hot,
            style: {
              stroke: hot ? 'var(--accent)' : 'var(--line)',
              strokeWidth: hot ? 2.5 : 1.2,
              strokeDasharray: e.conditional && !hot ? '4 4' : undefined,
            },
            markerEnd: { type: MarkerType.ArrowClosed, color: hot ? 'var(--accent)' : 'var(--muted)', width: 14, height: 14 },
          }
        }),
    [active, previous],
  )

  return (
    <div style={{ height }} className="overflow-hidden rounded-xl border border-line bg-panel">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        fitViewOptions={{ padding: 0.04 }}
        nodesConnectable={false}
        panOnScroll={false}
        zoomOnScroll={false}
        preventScrolling={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={20} size={1} color="var(--line)" />
      </ReactFlow>
    </div>
  )
}
