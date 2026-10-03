import {
  Background,
  ConnectionMode,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useMemo, useState } from 'react'
import { NODES } from '../lib/content'
import { graph } from '../lib/data'
import { EDGES, HANDLE_IDS, POSITIONS, SELF_LOOPS, isSpacer, type HandleId } from '../lib/graphLayout'
import { actorColor } from './ui'

const SIDE: Record<string, Position> = { top: Position.Top, bottom: Position.Bottom, left: Position.Left, right: Position.Right }
const LABEL: Record<string, string> = { __start__: 'start', __end__: 'end', __end_stopped__: 'end · stopped' }

type StepNodeData = { label: string; color: string; active: boolean; dim: boolean; badge?: string }

/** A node with 12 invisible handles (3 per side) so every edge has its own attachment point. */
function StepNode({ data }: NodeProps<Node<StepNodeData>>) {
  return (
    <div
      className="relative rounded-[10px] px-2 py-1.5 text-center text-[12px] transition-all"
      style={{
        width: 150,
        border: `${data.active ? 2 : 1}px solid ${data.active ? data.color : 'var(--line)'}`,
        background: data.active ? 'var(--accent-soft)' : 'var(--panel)',
        boxShadow: data.active ? `0 0 0 4px color-mix(in srgb, ${data.color} 25%, transparent)` : 'none',
        fontWeight: data.active ? 600 : 400,
        opacity: data.dim ? 0.35 : 1,
        color: 'var(--ink)',
      }}
    >
      {HANDLE_IDS.map((id) => {
        const [side, pct] = id.split('-')
        const horizontal = side === 'top' || side === 'bottom'
        return (
          <Handle
            key={id}
            id={id}
            type="source"
            position={SIDE[side]}
            style={horizontal ? { left: `${pct}%` } : { top: `${pct}%` }}
          />
        )
      })}
      {data.label}
      {data.badge && <div className="text-[9px] text-muted">{data.badge}</div>}
    </div>
  )
}

const nodeTypes = { step: StepNode }

export function GraphView({
  active,
  previous,
  onSelect,
}: {
  active?: string
  previous?: string
  onSelect?: (node: string) => void
}) {
  const [hovered, setHovered] = useState<string | null>(null)
  const focus = hovered ?? active

  const edgeList = useMemo(
    () =>
      graph.edges
        .filter((e) => e.source !== e.target)
        .map((e) => ({ ...e, spec: EDGES[`${e.source}->${e.target}`] }))
        .filter((e) => e.spec),
    [],
  )
  const neighbours = useMemo(() => {
    const set = new Set<string>()
    if (!hovered) return set
    for (const e of edgeList) {
      const t = e.spec.targetAlias ?? e.target
      if (e.source === hovered) set.add(t)
      if (t === hovered) set.add(e.source)
    }
    return set.add(hovered)
  }, [hovered, edgeList])

  const nodes: Node<StepNodeData>[] = useMemo(
    () =>
      Object.entries(POSITIONS).map(([id, [x, y]]) => {
        const info = NODES[id === '__end_stopped__' ? '__end__' : id]
        return {
          id,
          type: 'step',
          position: { x, y },
          draggable: false,
          selectable: false,
          hidden: false,
          style: isSpacer(id) ? { opacity: 0, pointerEvents: 'none', width: 10 } : undefined,
          data: {
            label: isSpacer(id) ? '' : (LABEL[id] ?? info?.title ?? id),
            color: info ? actorColor(info.who) : 'var(--muted)',
            active: id === active,
            dim: hovered !== null && !neighbours.has(id),
            badge: SELF_LOOPS[id],
          },
        }
      }),
    [active, hovered, neighbours],
  )

  const edges: Edge[] = useMemo(
    () =>
      edgeList.map((e) => {
        const target = e.spec.targetAlias ?? e.target
        const hot = e.source === previous && (e.target === active || target === active)
        const touchesFocus = focus !== undefined && (e.source === focus || target === focus)
        const dim = hovered !== null && !touchesFocus
        const stroke = hot ? 'var(--accent)' : touchesFocus ? 'var(--ink)' : 'var(--muted)'
        return {
          id: `${e.source}->${e.target}`,
          source: e.source,
          target,
          sourceHandle: e.spec.source as HandleId,
          targetHandle: e.spec.target as HandleId,
          type: 'smoothstep',
          pathOptions: { borderRadius: 10, offset: 18 },
          animated: hot,
          // Only the focused node's outgoing edges are labelled — those are its choices.
          // Labelling incoming edges too piled several labels onto busy nodes.
          label: e.source === focus || hot ? e.spec.label : undefined,
          labelStyle: { fontSize: 10, fill: 'var(--ink)' },
          labelBgStyle: { fill: 'var(--panel)' },
          labelBgPadding: [4, 2] as [number, number],
          style: {
            stroke,
            strokeWidth: hot ? 2.5 : touchesFocus ? 1.8 : 1,
            strokeDasharray: e.conditional && !hot && !touchesFocus ? '4 4' : undefined,
            opacity: dim ? 0.15 : 1,
          },
          markerEnd: { type: MarkerType.ArrowClosed, color: stroke, width: 14, height: 14 },
        }
      }),
    [edgeList, active, previous, focus, hovered],
  )

  return (
    // Height follows width at the layout's aspect ratio, so the diagram fills its box.
    <div style={{ aspectRatio: '780 / 800' }} className="w-full overflow-hidden rounded-xl border border-line bg-panel">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        connectionMode={ConnectionMode.Loose}
        fitView
        fitViewOptions={{ padding: 0.1 }}
        nodesConnectable={false}
        panOnScroll={false}
        zoomOnScroll={false}
        preventScrolling={false}
        onNodeMouseEnter={(_, n) => setHovered(n.id)}
        onNodeMouseLeave={() => setHovered(null)}
        onNodeClick={(_, n) => onSelect?.(n.id === '__end_stopped__' ? '__end__' : n.id)}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={20} size={1} color="var(--line)" />
      </ReactFlow>
    </div>
  )
}
