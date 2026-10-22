const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const compiled = ts.transpileModule(fs.readFileSync(path.resolve(__dirname, '../src/lib/mind-map.ts'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
const helpers = {}; new Function('exports', compiled)(helpers)
const { createMindMap, visibleTopics, visibleSelection, anchoredViewport, descendantCount, layoutMindMap, mindMapSvg, wrapTopic } = helpers
const topic = (id, parent, children = [], label = id) => ({ id, parent_id: parent, children, label, heading: label, kind: parent ? 'section' : 'document', mastery: null })
const children = Array.from({ length: 250 }, (_, i) => topic(`s-${i}`, 'chapter', [], `1.${i + 1} 知识点`))
const data = { nodes: [], edges: [], tree: { root_ids: ['book'], nodes: [topic('book', null, ['chapter'], '教材'), { ...topic('chapter', 'book', children.map(n => n.id), '第一章 概念框架'), kind: 'chapter' }, ...children] } }
const checks = [
  ['full course opens with every first-level chapter and no sampled graph dependency', () => {
    const model = createMindMap(data, '课程'); assert.equal(visibleTopics(model, model.rootId, new Set([model.rootId])).length, 2); assert.equal(descendantCount(model.rootId, model.index), 251); assert.equal(model.index.get('s-249').mastery, null)
  }],
  ['expansion and chapter focus preserve headings outside the old 180-node sample', () => {
    const model = createMindMap(data, '课程'); const visible = visibleTopics(model, 'chapter', new Set(['chapter'])); assert.equal(visible.length, 251); assert(visible.some(n => n.id === 's-249')); assert.equal(data.tree.nodes.length, 252)
  }],
  ['multiple textbooks retain distinct origins without changing the supplied model', () => {
    const graph = { ...data, tree: { root_ids: ['book', 'other'], nodes: [...data.tree.nodes, topic('other', null, ['foreign']), topic('foreign', 'other', [], '知识点')] } }
    const all = createMindMap(graph, '课程'); assert.equal(all.index.get('book').parent_id, all.rootId); assert.equal(graph.tree.nodes[0].parent_id, null)
    const one = createMindMap(graph, '课程', 'book'); assert.equal(descendantCount(one.rootId, one.index), 251); assert(!visibleTopics(one, one.rootId, new Set(one.index.keys())).some(n => n.id === 'foreign'))
  }],
  ['cyclic or repeated child IDs cannot loop or duplicate displayed topics', () => {
    const graph = { ...data, tree: { root_ids: ['a'], nodes: [topic('a', null, ['b', 'b']), topic('b', 'a', ['a', 'missing'])] } }; const model = createMindMap(graph, '课程')
    assert.equal(visibleTopics(model, 'a', new Set(['a', 'b'])).length, 2); assert.equal(descendantCount('a', model.index), 1)
  }],
  ['long headings wrap without dropping or abbreviating textbook text', () => { const label = '客户/服务器模式与具有复杂条件的磁盘读/写程序'.repeat(3); assert.equal(wrapTopic(label).join(''), label) }],
  ['personal title corrections change display while preserving source identity and recall secrecy', () => {
    const graph = { ...data, tree: { ...data.tree, nodes: data.tree.nodes.map(n => n.id === 'chapter' ? { ...n, display_label: '第一章 已核对标题', study_note: '我的理解' } : n) } }
    const model = createMindMap(graph, '课程'); const layout = layoutMindMap(model, 'book', new Set(['book']), true)
    assert(layout.nodes.find(n => n.id === 'chapter').lines.join('').includes('已核对标题'))
    assert.equal(model.index.get('chapter').heading, '第一章 概念框架'); assert.equal(model.index.get('chapter').id, 'chapter')
    assert(!mindMapSvg(model, layout, true).includes('已核对标题')); assert(!mindMapSvg(model, layout).includes('我的理解'))
  }],
  ['balanced branches do not overlap at the same depth', () => {
    const model = createMindMap(data, '课程'); const layout = layoutMindMap(model, 'chapter', new Set(['chapter']), true)
    for (const side of ['left', 'right']) { const list = layout.nodes.filter(n => n.side === side).sort((a, b) => a.y - b.y); for (let i = 1; i < list.length; i++) assert(list[i].y >= list[i - 1].y + list[i - 1].height) }
  }],
  ['small-screen layout keeps root and first-level names legible at native width', () => {
    const nodes = [topic('book', null, Array.from({ length: 10 }, (_, i) => `c${i}`), '操作系统'), ...Array.from({ length: 10 }, (_, i) => topic(`c${i}`, 'book', [], `第${i + 1}章 章节标题`))]
    const model = createMindMap({ ...data, tree: { root_ids: ['book'], nodes } }, '操作系统'); const layout = layoutMindMap(model, 'book', new Set(['book']), false)
    assert(layout.width < 340); assert(layout.height < 860); assert.equal(layout.nodes.length, 11)
  }],
  ['recall export hides the same branch names as the live learning mode', () => {
    const model = createMindMap(data, '课程'); const layout = layoutMindMap(model, 'book', new Set(['book']), true)
    const svg = mindMapSvg(model, layout, true); assert(!svg.includes('概念框架')); assert(svg.includes('回忆分支'))
    assert(mindMapSvg(model, layout, true, new Set(['chapter'])).includes('概念框架'))
  }],
  ['SVG export escapes labels and never executes student-provided markup', () => {
    const graph = { ...data, tree: { root_ids: ['unsafe'], nodes: [topic('unsafe', null, [], '<script>&"')] } }; const model = createMindMap(graph, '<script>&"'); const svg = mindMapSvg(model, layoutMindMap(model, model.rootId, new Set(), true))
    assert(!svg.includes('<script>')); assert(svg.includes('&lt;script&gt;')); assert(svg.includes('&amp;'))
  }],
  ['root branches can be fully closed and reopened without losing nested state', () => {
    const model = createMindMap(data, '课程'); const open = new Set(['book', 'chapter']); const folded = new Set(open); folded.delete('book')
    assert.deepEqual(visibleTopics(model, 'book', folded).map(n => n.id), ['book']); folded.add('book')
    assert.equal(visibleTopics(model, 'book', folded).length, 252); assert.deepEqual([...open], ['book', 'chapter'])
  }],
  ['closing an ancestor returns selection to its visible parent instead of an invisible leaf', () => {
    const model = createMindMap(data, '课程')
    assert.equal(visibleSelection(model, 'book', new Set(['book']), 's-249'), 'chapter')
    assert.equal(visibleSelection(model, 'book', new Set(), 's-249'), 'book')
    assert.equal(visibleSelection(model, 'chapter', new Set(['chapter']), 'unknown'), 'chapter')
  }],
  ['expansion keeps both scale and the clicked branch screen position', () => {
    const model = createMindMap(data, '课程'); const before = layoutMindMap(model, 'book', new Set(['book']), true)
    const after = layoutMindMap(model, 'book', new Set(['book', 'chapter']), true); const viewport = { x: 24, y: 300, zoom: .9 }
    const next = anchoredViewport(before, after, 'chapter', viewport)
    const a = before.nodes.find(n => n.id === 'chapter'); const b = after.nodes.find(n => n.id === 'chapter')
    assert.equal(next.zoom, viewport.zoom); assert.equal(a.x * viewport.zoom + viewport.x, b.x * next.zoom + next.x)
    assert.equal(a.y * viewport.zoom + viewport.y, b.y * next.zoom + next.y)
    assert.deepEqual(anchoredViewport(before, after, 'unknown', viewport), viewport)
  }],
  ['figure visibility changes preserve chapter hierarchy and do not mutate source data', () => {
    const graph = { ...data, tree: { ...data.tree, nodes: data.tree.nodes.map(n => n.id === 'chapter' ? { ...n, children: [...n.children, 'figure:a', 'figure:b'] } : n).concat([{ ...topic('figure:a', 'chapter', [], '图 3-39 TCP三次握手'), kind: 'figure', page_start: 185 }, { ...topic('figure:b', 'chapter', [], '图 3-40 TCP状态'), kind: 'figure', page_start: 185 }]) } }
    const shown = createMindMap(graph, '课程'); const hidden = createMindMap(graph, '课程', 'all', false)
    assert(shown.index.has('figure:a')); assert(shown.index.has('figure:b')); assert(!hidden.index.has('figure:a'))
    assert.equal(hidden.index.get('chapter').children.length, 250); assert.equal(graph.tree.nodes.find(n => n.id === 'chapter').children.length, 252)
    assert.equal(shown.index.get('figure:a').mastery, null)
  }],
  ['figure captions remain hidden in recall exports until the same node is revealed', () => {
    const graph = { ...data, tree: { root_ids: ['book'], nodes: [topic('book', null, ['figure']), { ...topic('figure', 'book', [], '图 2-8 HTTP请求报文'), kind: 'figure' }] } }
    const model = createMindMap(graph, '课程'); const layout = layoutMindMap(model, 'book', new Set(['book']), true)
    assert(!mindMapSvg(model, layout, true).includes('HTTP请求报文')); assert(mindMapSvg(model, layout, true, new Set(['figure'])).includes('HTTP请求报文'))
  }],
]
for (const [name, check] of checks) { check(); console.log(`PASS ${name}`) }
console.log(`${checks.length} mind map contract checks passed.`)
