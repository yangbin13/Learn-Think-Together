import { memo, useEffect, useMemo, useRef, useState } from 'react'
import dagre from '@dagrejs/dagre'
import { Background, Controls, Handle, MarkerType, Position, ReactFlow } from '@xyflow/react'
import type { Edge, Node, NodeProps } from '@xyflow/react'
import type { StructureNode } from '../api'
import type { LocalRelations } from '../lib/knowledge-map'
import { kindLabel } from '../lib/knowledge-map'
import '@xyflow/react/dist/style.css'

type ConceptData = { label: string; kind: StructureNode['kind']; focused: boolean; vertical: boolean; select: (id: string) => void }
type ConceptNode = Node<ConceptData, 'concept'>
const Concept = memo(function Concept({ id, data }: NodeProps<ConceptNode>) {
  return <button className={`relation-node${data.focused ? ' relation-node-focused' : ''}`} title={data.label} onClick={() => data.select(id)}>
    <Handle type="target" position={data.vertical ? Position.Top : Position.Left} isConnectable={false}/>
    <span>{kindLabel(data.kind)}</span><strong>{data.label}</strong>
    <Handle type="source" position={data.vertical ? Position.Bottom : Position.Right} isConnectable={false}/>
  </button>
})
const nodeTypes = { concept: Concept }

export default function RelationshipGraph({ relations, selectedId, select }: { relations: LocalRelations; selectedId: string; select: (id: string) => void }) {
  const [size, setSize] = useState({ width: window.matchMedia('(max-width: 700px)').matches ? 300 : 800, height: 390 })
  const narrow = size.width < 640
  const canvas = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const observer = new ResizeObserver(entries => {
      const next = { width: Math.round(entries[0].contentRect.width), height: Math.round(entries[0].contentRect.height) }
      setSize(current => current.width === next.width && current.height === next.height ? current : next)
    })
    if (canvas.current) observer.observe(canvas.current)
    return () => observer.disconnect()
  }, [])
  const { nodes, edges, vertical } = useMemo(() => {
    // Keep two readable nodes on a narrow canvas. When order is requested,
    // show the selected item and one order neighbour; retain every relation
    // in the text list rather than cropping the graph at a fixed zoom.
    const sequence = relations.edges.find(edge => edge.kind === 'sequence' && (edge.source === selectedId || edge.target === selectedId))
    const neighbour = sequence ? (sequence.source === selectedId ? sequence.target : sequence.source) : relations.nodes.find(node => node.id !== selectedId)?.id
    const shownNodes = narrow ? relations.nodes.filter(node => node.id === selectedId || node.id === neighbour) : relations.nodes
    const shownIds = new Set(shownNodes.map(node => node.id))
    const shownEdges = relations.edges.filter(edge => shownIds.has(edge.source) && shownIds.has(edge.target))
    const engine = new dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}))
    for (const node of shownNodes) engine.setNode(node.id, { width: 220, height: 104 })
    for (const edge of shownEdges) engine.setEdge(edge.source, edge.target)
    const layout = (vertical: boolean) => {
      engine.setGraph({ rankdir: vertical ? 'TB' : 'LR', nodesep: 36, ranksep: vertical ? 68 : 112, marginx: 16, marginy: 16 })
      dagre.layout(engine)
      return Math.min(size.width / (engine.graph().width || 1), size.height / (engine.graph().height || 1), 1)
    }
    let vertical = narrow
    if (narrow) layout(true)
    else {
      const horizontalScale = layout(false)
      const verticalScale = layout(true)
      vertical = verticalScale > horizontalScale
      if (!vertical) layout(false)
    }
    const nodes: ConceptNode[] = shownNodes.map(node => ({ id: node.id, type: 'concept', position: { x: engine.node(node.id).x - 110, y: engine.node(node.id).y - 52 }, data: { label: node.label, kind: node.kind, focused: node.id === selectedId, vertical, select } }))
    const edges: Edge[] = shownEdges.map((edge, index) => ({ id: `edge-${index}`, source: edge.source, target: edge.target, type: 'smoothstep', label: edge.relation, markerEnd: { type: MarkerType.ArrowClosed, color: '#668172' }, style: { stroke: edge.kind === 'sequence' ? '#8d8d83' : '#668172', strokeWidth: 1.5, strokeDasharray: edge.kind === 'sequence' ? '5 4' : undefined }, labelStyle: { fill: '#33483c', fontSize: 12 }, labelBgStyle: { fill: '#fffefa' }, labelBgPadding: [6, 4], labelBgBorderRadius: 3 }))
    return { nodes, edges, vertical }
  }, [relations, selectedId, narrow, size, select])
  return <><div ref={canvas} className="relation-canvas" style={{ height: narrow ? 380 : relations.nodes.length > 2 ? 480 : 390 }} aria-label="局部知识关系图">
    <ReactFlow key={`${selectedId}-${relations.edges.length}-${vertical}-${nodes.length}`} nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView fitViewOptions={{ padding: .1, maxZoom: 1 }} minZoom={.3} maxZoom={1.5} nodesDraggable={false} nodesConnectable={false} nodesFocusable={false} proOptions={{ hideAttribution: true }} ariaLabelConfig={{ 'controls.zoomIn.ariaLabel': '放大关系图', 'controls.zoomOut.ariaLabel': '缩小关系图', 'controls.fitView.ariaLabel': '适应关系图', 'node.a11yDescription.default': '按 Enter 选择节点。' }}>
      <Background color="#e5e9df" gap={24}/><Controls showInteractive={false}/>
    </ReactFlow>
  </div>{relations.nodes.length > nodes.length && <p className="relation-overflow">图中展示 {nodes.length} 个条目，全部 {relations.nodes.length} 个条目的关系见下方说明。</p>}</>
}
