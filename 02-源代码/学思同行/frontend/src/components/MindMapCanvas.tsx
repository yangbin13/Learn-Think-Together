import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { BookOpen, Download, Image, Maximize2, Minimize2, Minus, Plus, ZoomIn, ZoomOut } from 'lucide-react'
import { BaseEdge, Handle, Position, ReactFlow, ReactFlowProvider, useReactFlow, useViewport } from '@xyflow/react'
import type { Edge, EdgeProps, Node, NodeProps } from '@xyflow/react'
import type { MindMapLayout, MindMapModel, PlacedTopic } from '../lib/mind-map'
import { anchoredViewport, branchColors, layoutMindMap, mindMapSvg, topicNumber } from '../lib/mind-map'
import { downloadMindMap, mindMapPng } from '../lib/mind-map-export'
import '@xyflow/react/dist/style.css'

type TopicData = { placed: PlacedTopic; label: string; number: string; children: number; selected: boolean; open: boolean; hidden: boolean; figure: boolean; manual: boolean; select: (id: string) => void; toggle: (id: string) => void; focus: (id: string) => void }
type TopicNode = Node<TopicData, 'topic'>
const Topic = memo(function Topic({ id, data }: NodeProps<TopicNode>) {
  const { zoom } = useViewport()
  const root = data.placed.depth === 0
  const color = root ? '#244f40' : branchColors[data.placed.branch % branchColors.length]
  const label = data.hidden ? `回忆分支 ${data.placed.ordinal}` : data.label
  return <div className={`mindmap-topic${root ? ' mindmap-topic-root' : ''}${data.selected ? ' mindmap-topic-selected' : ''}${data.hidden ? ' mindmap-topic-hidden' : ''}`} style={{ width: data.placed.width, height: data.placed.height, '--branch-color': color } as React.CSSProperties}>
    <Handle type="target" id="in-left" position={Position.Left}/><Handle type="target" id="in-right" position={Position.Right}/>
    <Handle type="source" id="out-left" position={Position.Left}/><Handle type="source" id="out-right" position={Position.Right}/>
    <button className="mindmap-topic-button nodrag" aria-label={data.hidden ? `揭示第 ${data.placed.ordinal} 个分支` : `选择 ${label}`} aria-current={data.selected ? 'true' : undefined} onClick={() => data.select(id)} onDoubleClick={() => data.children && !data.hidden && data.focus(id)}>
      <strong>{data.hidden ? label : data.placed.lines.map((line, i) => <span key={i}>{line}</span>)}</strong>
      <small>{data.hidden ? '先回忆，再点击核对' : data.figure ? <><Image size={12}/>{data.manual ? '个人图示标注' : '教材图示'}</> : root ? '当前中心主题' : `${data.number || '知识分支'}${data.children ? ` · ${data.children} 个子项` : ' · 末级条目'}`}</small>
    </button>
    {data.children > 0 && (root || zoom >= .64) && <button className="mindmap-expand nodrag" style={{ transform: `translateY(-50%) scale(${1 / zoom})` }} aria-label={`${data.open ? '收起' : '展开'}${data.hidden ? `第 ${data.placed.ordinal} 个分支` : label}`} aria-expanded={data.open} onClick={() => data.toggle(id)}><span>{data.open ? <Minus size={14}/> : <Plus size={14}/>}</span></button>}
  </div>
})
const nodeTypes = { topic: Topic }
function BranchEdge({ id, data }: EdgeProps) {
  const edge = data as { path: string; color: string; depth: number }
  return <BaseEdge id={id} path={edge.path} style={{ stroke: edge.color, strokeWidth: edge.depth === 1 ? 3 : 1.7 }}/>
}
const edgeTypes = { branch: BranchEdge }

function CanvasControls({ layout, width, height, navigationKey, anchorId, exportMap, exportSvg, exporting, fullscreen, toggleFullscreen }: { layout: MindMapLayout; width: number; height: number; navigationKey: string; anchorId: string; exportMap: () => void; exportSvg: () => void; exporting: boolean; fullscreen: boolean; toggleFullscreen: () => void }) {
  const flow = useReactFlow()
  const previous = useRef<{ key: string; layout: MindMapLayout } | null>(null)
  const savedViews = useRef(new Map<string, { x: number; y: number; zoom: number }>())
  const fit = useCallback((duration = 0) => {
    // Layout geometry is authoritative; do not race asynchronous DOM measurement.
    // Reserve the footer so small-screen branches never sit behind the controls.
    const roomHeight = height - 36
    const zoom = Math.max(.25, Math.min((width - 44) / layout.width, width < 860 ? 1 : roomHeight / layout.height, 1))
    const x = (width - layout.width * zoom) / 2 - layout.minX * zoom
    const y = width < 860 ? 18 - layout.minY * zoom : 18 + (roomHeight - layout.height * zoom) / 2 - layout.minY * zoom
    return flow.setViewport({ x, y, zoom }, { duration })
  }, [flow, layout, width, height])
  useLayoutEffect(() => {
    if (!flow.viewportInitialized) return
    const before = previous.current
    if (!before || before.key !== navigationKey) {
      if (before) savedViews.current.set(before.key, flow.getViewport())
      const saved = savedViews.current.get(navigationKey)
      if (saved) void flow.setViewport(saved)
      else void fit()
    } else if (before.layout !== layout) {
      void flow.setViewport(anchoredViewport(before.layout, layout, anchorId, flow.getViewport()))
    }
    previous.current = { key: navigationKey, layout }
  }, [flow, flow.viewportInitialized, navigationKey, layout, anchorId, fit])
  return <div className="mindmap-canvas-controls" aria-label="导图画布操作">
    <button aria-label="放大思维导图" onClick={() => void flow.zoomIn({ duration: 160 })}><ZoomIn size={18}/></button>
    <button aria-label="缩小思维导图" onClick={() => void flow.zoomOut({ duration: 160 })}><ZoomOut size={18}/></button>
    <button aria-label={fullscreen ? '退出全屏' : '全屏查看思维导图'} title={fullscreen ? '退出全屏（Esc）' : '全屏查看思维导图'} aria-pressed={fullscreen} onClick={toggleFullscreen}>{fullscreen ? <Minimize2 size={18}/> : <Maximize2 size={18}/>}</button>
    <button className="mindmap-download" aria-label="下载当前导图 PNG 图片" title="下载当前展开的导图，手机可离线查看" disabled={exporting} onClick={exportMap}><Download size={18}/><span>{exporting ? '生成中…' : '下载图片'}</span></button>
    <button aria-label="导出当前导图 SVG" title="下载 SVG 矢量图" onClick={exportSvg}>SVG</button>
  </div>
}

export default function MindMapCanvas({ model, focusId, expanded, selectedId, recall, revealed, select, toggle, focus, anchorId, showDetails }: { model: MindMapModel; focusId: string; expanded: Set<string>; selectedId: string; recall: boolean; revealed: Set<string>; select: (id: string) => void; toggle: (id: string) => void; focus: (id: string) => void; anchorId: string; showDetails: () => void }) {
  const canvas = useRef<HTMLDivElement>(null)
  const [fullscreen, setFullscreen] = useState(false)
  const [size, setSize] = useState({ width: 1000, height: 600 })
  const width = size.width
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState('')
  useEffect(() => {
    const observer = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect
      setSize({ width: Math.round(width), height: Math.round(height) })
    })
    if (canvas.current) observer.observe(canvas.current)
    return () => observer.disconnect()
  }, [])
  useEffect(() => {
    const changed = () => setFullscreen(document.fullscreenElement === canvas.current)
    document.addEventListener('fullscreenchange', changed)
    return () => document.removeEventListener('fullscreenchange', changed)
  }, [])
  const exitFullscreen = useCallback(async () => {
    if (document.fullscreenElement === canvas.current) await document.exitFullscreen()
    setFullscreen(false)
  }, [])
  const toggleFullscreen = async () => {
    if (fullscreen) { await exitFullscreen(); return }
    if (!canvas.current) return
    try {
      await canvas.current.requestFullscreen()
    } catch {
      // Embedded browsers and some mobile browsers only support a viewport overlay.
    }
    setFullscreen(true)
  }
  useEffect(() => {
    if (!fullscreen) return
    const previous = document.activeElement as HTMLElement | null
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const listener = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); void exitFullscreen(); return }
      if (event.key !== 'Tab') return
      const buttons = [...(canvas.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? [])].filter(button => button.getClientRects().length)
      const first = buttons[0]; const last = buttons[buttons.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
    }
    document.addEventListener('keydown', listener)
    return () => {
      document.body.style.overflow = overflow
      document.removeEventListener('keydown', listener)
      if (previous?.isConnected) previous.focus({ preventScroll: true })
    }
  }, [fullscreen, exitFullscreen])
  const balanced = width >= 860
  const layout = useMemo(() => layoutMindMap(model, focusId, expanded, balanced), [model, focusId, expanded, balanced])
  const nodes: TopicNode[] = layout.nodes.map(placed => ({ id: placed.id, type: 'topic', position: { x: placed.x, y: placed.y }, data: { placed, label: placed.lines.join(''), number: topicNumber(model.index.get(placed.id)!.label), children: model.index.get(placed.id)!.children.length, selected: selectedId === placed.id, open: expanded.has(placed.id), hidden: recall && placed.depth > 0 && !revealed.has(placed.id), figure: model.index.get(placed.id)!.kind === 'figure', manual: model.index.get(placed.id)!.origin === 'manual', select, toggle, focus } }))
  const edges: Edge[] = layout.edges.map(edge => ({ id: `${edge.source}-${edge.target}`, type: 'branch', source: edge.source, target: edge.target, sourceHandle: layout.nodes.find(n => n.id === edge.target)?.side === 'right' ? 'out-right' : 'out-left', targetHandle: layout.nodes.find(n => n.id === edge.target)?.side === 'left' ? 'in-right' : 'in-left', data: edge }))
  const footerHeight = balanced ? 108 : 152
  const height = balanced ? 600 : Math.max(840, Math.min(1200, Math.ceil(layout.height) + footerHeight + 36))
  const flowHeight = Math.max(1, (fullscreen ? size.height : height) - footerHeight)
  const navigationKey = `${model.rootId}-${focusId}-${width}-${flowHeight}-${fullscreen ? 'fullscreen' : 'inline'}`
  const viewDetails = async () => {
    await exitFullscreen()
    // Restore the inline canvas and page scrolling before locating the details.
    requestAnimationFrame(() => showDetails())
  }
  const exportMap = async () => {
    if (exporting) return
    setExporting(true); setExportError('')
    try {
      const blob = await mindMapPng(model, layout, recall, revealed)
      const title = layout.nodes[0]?.lines.join('') || model.rootLabel
      downloadMindMap(blob, title, 'png', recall)
    } catch (error) {
      setExportError(error instanceof Error ? error.message : '导图图片生成失败，请重试。')
    } finally {
      setExporting(false)
    }
  }
  const exportSvg = () => downloadMindMap(new Blob([mindMapSvg(model, layout, recall, revealed)], { type: 'image/svg+xml;charset=utf-8' }), layout.nodes[0]?.lines.join('') || model.rootLabel, 'svg', recall)
  return <ReactFlowProvider><div ref={canvas} className={`mindmap-canvas${fullscreen ? ' mindmap-canvas-fullscreen' : ''}`} style={{ height: fullscreen ? '100dvh' : height }} role={fullscreen ? 'dialog' : undefined} aria-modal={fullscreen || undefined} aria-label={fullscreen ? '课程思维导图全屏查看' : '课程思维导图画布'}>
    <div className="mindmap-flow" style={{ height: flowHeight }}><ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} minZoom={.25} maxZoom={1.6} nodesDraggable={false} nodesConnectable={false} nodesFocusable={false} edgesFocusable={false} zoomOnDoubleClick={false} zoomOnScroll={false} panOnScroll={false} preventScrolling={false} zoomActivationKeyCode={['Control', 'Meta']} proOptions={{ hideAttribution: true }} ariaLabelConfig={{ 'node.a11yDescription.default': '选择节点，展开分支或进入本章。' }}/></div>
    <div className="mindmap-canvas-footer" style={{ height: footerHeight }}>
      <CanvasControls layout={layout} width={width} height={flowHeight} navigationKey={navigationKey} anchorId={anchorId} exportMap={() => void exportMap()} exportSvg={exportSvg} exporting={exporting} fullscreen={fullscreen} toggleFullscreen={() => void toggleFullscreen()}/>
      <div className="mindmap-canvas-hint">拖动画布 · 双击进入分支 · Ctrl / ⌘ + 滚轮缩放{fullscreen && ' · Esc 退出全屏'}</div>
      {selectedId && (!recall || selectedId === focusId || revealed.has(selectedId)) && <button className="mindmap-canvas-detail" onClick={() => void viewDetails()}><BookOpen size={15}/>查看已选节点</button>}
    </div>
    {exportError && <p className="mindmap-export-error" role="alert">{exportError}</p>}
  </div></ReactFlowProvider>
}
