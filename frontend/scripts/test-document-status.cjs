const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')
// Render the real TSX status components without a browser or a live model.
const cache = new Map()
function load(filename) {
  if (cache.has(filename)) return cache.get(filename)
  const source = fs.readFileSync(filename, 'utf8')
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText
  const exports = {}; cache.set(filename, exports)
  const resolve = name => name.startsWith('.') ? load(path.resolve(path.dirname(filename), `${name}.tsx`)) : require(name)
  new Function('require', 'exports', compiled)(resolve, exports)
  return exports
}
const { MaterialProgress, MaterialQualityView } = load(path.resolve(__dirname, '../src/components/DocumentStatus.tsx'))
const base = { id: 'test-only', title: '测试教材', filename: 'test.pdf', status: 'processing', source_type: 'uploaded_pdf', page_count: 0, chunk_count: 0 }
const render = (patch, component = MaterialProgress) => renderToStaticMarkup(React.createElement(component, { material: { ...base, ...patch } }))
const checks = [
  ['long PDF page checkpoints', () => { const html = render({ progress: { stage: 'extracting', current_page: 120, total_pages: 1349 } }); assert.match(html, /120 \/ 1,349 物理页/); assert.match(html, /8\.9%/); assert.match(html, /aria-valuenow="8\.9"/); assert.doesNotMatch(html, /剩余.*分钟/) }],
  ['actual vector checkpoint', () => { const html = render({ chunk_count: 1000, progress: { stage: 'embedding', total_pages: 1349, chunk_count: 1000, embedding_completed: 423, embedding_percent: 42.3 } }); assert.match(html, /42\.3%/); assert.match(html, /423 \/ 1,000 已建立向量/) }],
  ['failed extraction is explicit', () => { const html = render({ status: 'failed', error: '未提取到可检索文字', progress: { stage: 'failed' } }); assert.match(html, /未提取到可检索文字/); assert.match(html, /role="alert"/); assert.doesNotMatch(html, /role="progressbar"/) }],
  ['semantic fallback keeps cause visible', () => { const html = render({ status: 'ready', chunk_count: 1000, embedding_status: 'lexical_fallback', quality: { semantic_error: '语义服务暂时不可用' }, progress: { stage: 'ready', embedding_completed: 423, embedding_percent: 42.3 } }); assert.match(html, /语义暂不可用/); assert.match(html, /语义服务暂时不可用/); assert.match(html, /42\.3%/); assert.doesNotMatch(html, /关键词 \+ 语义检索/) }],
  ['requeued resume shows queue', () => { const html = render({ status: 'queued', embedding_status: 'lexical_fallback', progress: { stage: 'embedding', embedding_percent: 42.3 } }); assert.match(html, /排队等待解析/); assert.doesNotMatch(html, /role="progressbar"/) }],
  ['quality facts and warnings', () => { const html = render({ status: 'ready', quality: { page_kind: 'physical', total_pages: 1349, text_pages: 1342, blank_pages: 7, outline_entries: 0, chapter_source: '正文标题推断，待核验', warnings: ['图片与公式尚未自动理解'] } }, MaterialQualityView); assert.match(html, /总物理页数/); assert.match(html, /1,349/); assert.match(html, /0 条目录项/); assert.match(html, /图片与公式尚未自动理解/); assert.doesNotMatch(html, /OCR 已完成/) }],
  ['legacy document remains usable', () => { const html = render({ status: 'ready', source_type: 'original', page_count: 5, chunk_count: 12 }); assert.match(html, /5 逻辑段/); assert.match(html, /12 个片段/); assert.doesNotMatch(html, /role="progressbar"/) }],
]
for (const [name, run] of checks) { run(); console.log(`PASS ${name}`) }
console.log(`${checks.length} document status regression checks passed.`)
