const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const compiled = ts.transpileModule(fs.readFileSync(path.resolve(__dirname, '../src/lib/knowledge-map.ts'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
const exportsMap = {}
new Function('exports', compiled)(exportsMap)
const { ancestors, searchStructure, localRelations } = exportsMap
const node = (id, parent_id, children = [], label = id) => ({ id, parent_id, children, label, heading: label, kind: parent_id ? 'section' : 'document' })
const nodes = [node('book-a', null, ['chapter']), node('chapter', 'book-a', ['section'], '第2章 内存管理'), node('section', 'chapter', [], '2.1 分页地址转换'), node('book-b', null, ['foreign']), node('foreign', 'book-b', [], '分页地址转换')]
const index = new Map(nodes.map(value => [value.id, value]))
const checks = [
  ['search preserves ancestors and textbook filter', () => {
    const result = searchStructure('分页', index, ['book-a'])
    assert.deepEqual([...result.matching], ['section'])
    assert.deepEqual([...result.visible].sort(), ['book-a', 'chapter', 'section'])
    assert(result.expanded.has('book-a')); assert(result.expanded.has('chapter'))
    assert(!result.visible.has('foreign'))
  }],
  ['blank search restores full tree', () => assert.equal(searchStructure('  ', index, ['book-a']).visible, null)],
  ['unsampled heading still has local structural relations', () => {
    const result = localRelations({ tree: { nodes }, nodes: [], edges: [] }, nodes[2])
    assert.deepEqual(result.nodes.map(value => value.id), ['section', 'chapter'])
    assert.equal(result.edges[0].relation, '章节包含')
  }],
  ['reading sequence is opt-in and duplicate relations combine', () => {
    const edges = [{ source: 'chapter', target: 'section', relation: '学习关联', kind: 'association' }, { source: 'section', target: 'foreign', relation: '教材相邻章节', kind: 'sequence' }]
    const data = { tree: { nodes }, nodes: [], edges }
    const result = localRelations(data, nodes[2])
    assert.equal(result.edges.length, 1); assert.match(result.edges[0].relation, /章节包含 · 学习关联/)
    assert(!result.nodes.some(value => value.id === 'foreign'))
    assert(localRelations(data, nodes[2], true).nodes.some(value => value.id === 'foreign'))
  }],
  ['unsampled siblings retain opt-in reading order without crossing a textbook', () => {
    const children = [node('before', 'chapter'), node('selected', 'chapter'), node('after', 'chapter')]
    const chapter = node('chapter', 'book-a', children.map(item => item.id))
    const data = { tree: { nodes: [nodes[0], chapter, ...children, nodes[3], nodes[4]] }, nodes: [], edges: [] }
    assert.equal(localRelations(data, children[1]).edges.length, 1)
    const result = localRelations(data, children[1], true)
    assert.deepEqual(result.edges.filter(edge => edge.kind === 'sequence').map(edge => [edge.source, edge.target]), [['before', 'selected'], ['selected', 'after']])
    assert(!result.nodes.some(item => item.id === 'foreign'))
  }],
  ['large chapter graph is bounded with an explicit omitted count', () => {
    const children = Array.from({ length: 250 }, (_, i) => node(`part-${i}`, 'chapter'))
    const chapter = node('chapter', 'book-a', children.map(value => value.id))
    const data = { tree: { nodes: [nodes[0], chapter, ...children] }, nodes: [], edges: [] }
    const result = localRelations(data, chapter)
    assert.equal(result.nodes.length, 12); assert.equal(result.omitted, 240)
    assert.equal(data.tree.nodes.length, 252)
  }],
  ['malformed cyclic ancestor data cannot hang navigation', () => {
    const cyclic = new Map([['a', node('a', 'b')], ['b', node('b', 'a')]])
    assert.deepEqual(ancestors('a', cyclic).map(value => value.id), ['b'])
  }],
]
for (const [name, check] of checks) { check(); console.log(`PASS ${name}`) }
console.log(`${checks.length} knowledge navigation checks passed.`)
