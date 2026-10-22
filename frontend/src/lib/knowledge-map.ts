import type { GraphData, GraphEdge, StructureNode } from '../api'

export const kindLabel = (kind: StructureNode['kind']) => ({ document: '教材', chapter: '章', section: '节', concept: '知识点', figure: '图示' })[kind]

export function ancestors(id: string, index: Map<string, StructureNode>): StructureNode[] {
  const result: StructureNode[] = []
  const seen = new Set([id])
  let node = index.get(id)
  while (node?.parent_id && !seen.has(node.parent_id)) {
    seen.add(node.parent_id)
    node = index.get(node.parent_id)
    if (node) result.unshift(node)
  }
  return result
}

// Keep the path to every search hit so a match still has textbook context.
export function searchStructure(query: string, index: Map<string, StructureNode>, rootIds: string[]) {
  const term = query.trim().toLocaleLowerCase()
  const matching = new Set<string>()
  const visible = new Set<string>()
  const expanded = new Set<string>()
  if (!term) return { matching, visible: null, expanded }
  for (const node of index.values()) {
    const path = ancestors(node.id, index)
    if (!rootIds.includes(path[0]?.id ?? node.id)) continue
    if (`${node.label} ${node.heading}`.toLocaleLowerCase().includes(term)) {
      matching.add(node.id)
      visible.add(node.id)
      for (const parent of path) { visible.add(parent.id); expanded.add(parent.id) }
    }
  }
  return { matching, visible, expanded }
}

export type LocalRelations = { nodes: StructureNode[]; edges: GraphEdge[]; omitted: number }

export function localRelations(data: GraphData, selected: StructureNode, includeSequence = false, limit = 12): LocalRelations {
  const index = new Map(data.tree.nodes.map(node => [node.id, node]))
  const candidates: GraphEdge[] = []
  if (selected.parent_id && index.has(selected.parent_id)) candidates.push({ source: selected.parent_id, target: selected.id, relation: '章节包含', kind: 'structural' })
  // The full directory remains authoritative when a heading is outside the
  // sampled overview graph. Sibling links describe order, never prerequisites.
  if (includeSequence && selected.parent_id) {
    const siblings = index.get(selected.parent_id)?.children ?? []
    const position = siblings.indexOf(selected.id)
    const relation = selected.kind === 'concept' ? '导学编排顺序' : '教材相邻章节'
    if (position > 0 && index.has(siblings[position - 1])) candidates.push({ source: siblings[position - 1], target: selected.id, relation, kind: 'sequence' })
    if (position >= 0 && index.has(siblings[position + 1])) candidates.push({ source: selected.id, target: siblings[position + 1], relation, kind: 'sequence' })
  }
  for (const id of selected.children) if (index.has(id)) candidates.push({ source: selected.id, target: id, relation: '章节包含', kind: 'structural' })
  for (const edge of data.edges) {
    if (edge.source !== selected.id && edge.target !== selected.id) continue
    const kind = edge.kind ?? (edge.relation.includes('相邻') ? 'sequence' : edge.relation.includes('包含') ? 'structural' : 'association')
    if (kind === 'sequence' && !includeSequence) continue
    candidates.push({ ...edge, kind })
  }
  const ids = new Set([selected.id])
  const pairs = new Map<string, GraphEdge>()
  const allNeighbors = new Set<string>()
  for (const edge of candidates) {
    if (!index.has(edge.source) || !index.has(edge.target) || edge.source === edge.target) continue
    const neighbor = edge.source === selected.id ? edge.target : edge.source
    allNeighbors.add(neighbor)
    if (!ids.has(neighbor) && ids.size >= limit) continue
    ids.add(neighbor)
    const key = `${edge.source}\0${edge.target}`
    const previous = pairs.get(key)
    if (previous && !previous.relation.split(' · ').includes(edge.relation)) previous.relation += ` · ${edge.relation}`
    else if (!previous) pairs.set(key, { ...edge })
  }
  return { nodes: [...ids].map(id => index.get(id)!).filter(Boolean), edges: [...pairs.values()], omitted: Math.max(0, allNeighbors.size - (ids.size - 1)) }
}
