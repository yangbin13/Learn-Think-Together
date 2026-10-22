import type { GraphData, StructureNode } from '../api'

export const branchColors = ['#39775b', '#416c94', '#99633b', '#775e96', '#497f83', '#9b5859', '#6d793d', '#665f99', '#39758b', '#8c6950']
export type MindMapModel = { index: Map<string, StructureNode>; rootId: string; rootLabel: string }
export type PlacedTopic = { id: string; parentId: string | null; x: number; y: number; width: number; height: number; side: 'left' | 'right' | 'stack'; depth: number; branch: number; ordinal: number; lines: string[] }
export type MindMapLayout = { nodes: PlacedTopic[]; edges: { source: string; target: string; path: string; color: string; depth: number }[]; width: number; height: number; minX: number; minY: number }

export function createMindMap(data: GraphData, courseTitle: string, documentId = 'all', includeFigures = true): MindMapModel {
  const omitted = new Set(includeFigures ? [] : data.tree.nodes.filter(node => node.kind === 'figure').map(node => node.id))
  const index = new Map(data.tree.nodes.filter(node => !omitted.has(node.id)).map(node => [node.id, { ...node, children: node.children.filter(id => !omitted.has(id)) }]))
  const roots = data.tree.root_ids.filter(id => index.has(id) && (documentId === 'all' || documentId === id))
  if (roots.length === 1) return { index, rootId: roots[0], rootLabel: documentId === 'all' ? courseTitle : topicLabel(index.get(roots[0])!) }
  const rootId = 'mindmap:course'
  for (const id of roots) index.get(id)!.parent_id = rootId
  index.set(rootId, { id: rootId, label: courseTitle, heading: courseTitle, kind: 'document', depth: 0, order: 0, parent_id: null, children: roots, document_id: null, page_start: null, page_end: null, page_kind: null, source: '课程教材总览；各教材保持独立来源', concept_id: null, verified: false, mastery: null })
  return { index, rootId, rootLabel: courseTitle }
}

// Student annotations change display only; source lookup retains label/heading/ID.
export function topicLabel(node: StructureNode) { return node.display_label?.trim() || node.label }

export function shortTopic(label: string) {
  return label.replace(/^(?:第[零〇一二三四五六七八九十百千0-9]+[章节]\s*|\d+(?:\.\d+)*[.、]?\s*)/, '').trim() || label
}

export function topicNumber(label: string) {
  return label.match(/^(第[零〇一二三四五六七八九十百千0-9]+[章节]|\d+(?:\.\d+)*)/)?.[1] ?? ''
}

// This is display wrapping, never extraction or truncation of a textbook label.
export function wrapTopic(label: string, width = 206, fontSize = 16) {
  const lines: string[] = []; let line = ''; let used = 0
  for (const char of label) {
    const size = /[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]/u.test(char) ? fontSize : fontSize * .56
    if (line && used + size > width) { lines.push(line); line = ''; used = 0 }
    line += char; used += size
  }
  if (line) lines.push(line)
  return lines.length ? lines : ['']
}

export function visibleTopics(model: MindMapModel, focusId: string, expanded: Set<string>) {
  const result: { id: string; parentId: string | null; depth: number; branch: number; ordinal: number }[] = []
  const seen = new Set<string>()
  const visit = (id: string, parentId: string | null, depth: number, branch: number, ordinal: number) => {
    if (seen.has(id) || !model.index.has(id)) return
    seen.add(id); result.push({ id, parentId, depth, branch, ordinal })
    if (!expanded.has(id)) return
    model.index.get(id)!.children.forEach((child, order) => visit(child, id, depth + 1, depth === 0 ? order : branch, order + 1))
  }
  visit(focusId, null, 0, 0, 0)
  return result
}

export function visibleSelection(model: MindMapModel, focusId: string, expanded: Set<string>, selectedId: string) {
  const visible = new Set(visibleTopics(model, focusId, expanded).map(node => node.id))
  const seen = new Set<string>(); let id: string | null = selectedId
  while (id && !seen.has(id)) {
    if (visible.has(id)) return id
    seen.add(id); id = model.index.get(id)?.parent_id ?? null
  }
  return focusId
}

export function anchoredViewport(before: MindMapLayout, after: MindMapLayout, anchorId: string, viewport: { x: number; y: number; zoom: number }) {
  const previous = before.nodes.find(node => node.id === anchorId)
  const next = after.nodes.find(node => node.id === anchorId)
  if (!previous || !next) return viewport
  return { x: viewport.x + (previous.x - next.x) * viewport.zoom, y: viewport.y + (previous.y - next.y) * viewport.zoom, zoom: viewport.zoom }
}

export function descendantCount(id: string, index: Map<string, StructureNode>) {
  const seen = new Set([id]); const pending = [...(index.get(id)?.children ?? [])]
  while (pending.length) {
    const child = pending.pop()!
    if (seen.has(child) || !index.has(child)) continue
    seen.add(child); pending.push(...index.get(child)!.children)
  }
  return seen.size - 1
}

export function layoutMindMap(model: MindMapModel, focusId: string, expanded: Set<string>, balanced: boolean): MindMapLayout {
  const visible = visibleTopics(model, focusId, expanded)
  const nodes: PlacedTopic[] = visible.map(node => {
    const label = node.id === model.rootId ? model.rootLabel : shortTopic(topicLabel(model.index.get(node.id)!))
    const lines = wrapTopic(label, node.depth === 0 ? 190 : 206, node.depth === 0 ? 18 : 16)
    return { ...node, x: 0, y: 0, width: node.depth === 0 ? 218 : 234, height: Math.max(node.depth === 0 ? 84 : 62, lines.length * 23 + 30), side: 'stack', lines }
  })
  const index = new Map(nodes.map(node => [node.id, node]))
  const children = (id: string) => nodes.filter(node => node.parentId === id)
  if (!balanced) {
    let y = 0
    for (const node of nodes) { node.x = 20 + node.depth * 24; node.y = y; y += node.height + (node.depth === 0 ? 16 : 8) }
  } else if (nodes.length) {
    const root = nodes[0]; root.y = -root.height / 2
    const rootChildren = children(root.id)
    const half = Math.ceil(rootChildren.length / 2)
    const span = (id: string): number => Math.max(index.get(id)!.height, children(id).reduce((sum, child, order) => sum + span(child.id) + (order ? 24 : 0), 0))
    const place = (node: PlacedTopic, side: 'left' | 'right', top: number) => {
      const height = span(node.id); node.side = side; node.x = side === 'right' ? node.depth * 312 : -(node.depth * 312) - node.width + root.width; node.y = top + (height - node.height) / 2
      let offset = top
      for (const child of children(node.id)) { place(child, side, offset); offset += span(child.id) + 24 }
    }
    for (const [group, side] of [[rootChildren.slice(0, half), 'right'], [rootChildren.slice(half), 'left']] as const) {
      let top = -group.reduce((sum, child, order) => sum + span(child.id) + (order ? 24 : 0), 0) / 2
      for (const child of group) { place(child, side, top); top += span(child.id) + 24 }
    }
  }
  const edges = nodes.filter(node => node.parentId && index.has(node.parentId)).map(node => {
    const parent = index.get(node.parentId!)!; const sy = parent.y + parent.height / 2; const ty = node.y + node.height / 2
    const color = branchColors[node.branch % branchColors.length]
    if (node.side === 'stack') {
      const sx = parent.x; const tx = node.x; const bend = Math.min(sx, tx) - 20
      return { source: parent.id, target: node.id, color, depth: node.depth, path: `M ${sx} ${sy} C ${bend} ${sy}, ${bend} ${ty}, ${tx} ${ty}` }
    }
    const sx = node.side === 'right' ? parent.x + parent.width : parent.x
    const tx = node.side === 'right' ? node.x : node.x + node.width
    const middle = (sx + tx) / 2
    return { source: parent.id, target: node.id, color, depth: node.depth, path: `M ${sx} ${sy} C ${middle} ${sy}, ${middle} ${ty}, ${tx} ${ty}` }
  })
  const minX = Math.min(0, ...nodes.map(n => n.x)) - 30; const minY = Math.min(0, ...nodes.map(n => n.y)) - 24
  return { nodes, edges, minX, minY, width: Math.max(1, ...nodes.map(n => n.x + n.width)) - minX + 30, height: Math.max(1, ...nodes.map(n => n.y + n.height)) - minY + 24 }
}

const xml = (text: string) => text.replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' })[char]!)
export function mindMapSvg(model: MindMapModel, layout: MindMapLayout, recall = false, revealed = new Set<string>()) {
  const width = Math.ceil(layout.width + 40)
  const titleLines = wrapTopic(`${model.rootLabel} · ${recall ? '回忆导图' : '思维导图'}`, width - 48)
  const footerLines = wrapTopic(`教材标题层级，需结合原文核对；当前展开 ${layout.nodes.length} 个条目。`, width - 48, 12)
  const headerHeight = titleLines.length * 24 + 20
  const height = Math.ceil(layout.height + headerHeight + footerLines.length * 18 + 24)
  const offsetX = -layout.minX + 20; const offsetY = -layout.minY + headerHeight
  const lines = layout.edges.map(edge => `<path d="${edge.path}" stroke="${edge.color}" stroke-width="${edge.depth === 1 ? 3 : 1.8}" fill="none"/>`).join('')
  const topics = layout.nodes.map(node => {
    const hidden = recall && node.depth > 0 && !revealed.has(node.id)
    const labels = hidden ? [`回忆分支 ${node.ordinal}`] : node.lines
    const color = node.depth === 0 ? '#244f40' : branchColors[node.branch % branchColors.length]
    const text = labels.map((line, i) => `<tspan x="${node.x + 14}" dy="${i ? 23 : 0}">${xml(line)}</tspan>`).join('')
    const box = node.depth === 0 ? `<rect x="${node.x}" y="${node.y}" width="${node.width}" height="${node.height}" rx="14" fill="#244f40"/>` : `<path d="M ${node.x} ${node.y + node.height - 6} H ${node.x + node.width}" stroke="${color}" stroke-width="2"/>`
    return `${box}<text x="${node.x + 14}" y="${node.y + 27}" fill="${node.depth === 0 ? '#ffffff' : '#23372e'}" font-size="${node.depth === 0 ? 18 : 16}">${text}</text>`
  }).join('')
  const title = titleLines.map((line, index) => `<text x="24" y="${30 + index * 24}" fill="#23372e" font-size="16">${xml(line)}</text>`).join('')
  const footer = footerLines.map((line, index) => `<text x="24" y="${headerHeight + layout.height + 18 + index * 18}" fill="#5f6f65" font-size="12">${xml(line)}</text>`).join('')
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><rect width="100%" height="100%" fill="#fff"/><g font-family="Microsoft YaHei,Noto Sans SC,sans-serif">${title}<g transform="translate(${offsetX},${offsetY})">${lines}${topics}</g>${footer}</g></svg>`
}
