const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const code = ts.transpileModule(fs.readFileSync(path.resolve(__dirname, '../src/lib/question-draft.ts'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
const helpers = {}; new Function('exports', code)(helpers)
const { readQuestionDraft, writeQuestionDraft } = helpers
const memory = () => { const data = new Map(); return { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key), data } }
const checks = [
  ['same shared course never exposes another student draft', () => {
    const s = memory(); writeQuestionDraft(s, 'alice', 'operating-systems', 'Alice question')
    assert.equal(readQuestionDraft(s, 'bob', 'operating-systems'), '')
    assert.equal(readQuestionDraft(s, 'alice', 'operating-systems'), 'Alice question')
  }],
  ['one student can switch courses without borrowing the other question', () => {
    const s = memory(); writeQuestionDraft(s, 'alice', 'network', 'Network question'); writeQuestionDraft(s, 'alice', 'ds', 'Tree question')
    assert.equal(readQuestionDraft(s, 'alice', 'network'), 'Network question'); assert.equal(readQuestionDraft(s, 'alice', 'ds'), 'Tree question')
  }],
  ['multiline Unicode draft survives repeated reads and edits', () => {
    const s = memory(); const question = '请解释递归\n  factorial(3)🙂'
    writeQuestionDraft(s, 'student', 'course', question)
    assert.equal(readQuestionDraft(s, 'student', 'course'), question); assert.equal(readQuestionDraft(s, 'student', 'course'), question)
    writeQuestionDraft(s, 'student', 'course', question + '\n再问边界'); assert.equal(readQuestionDraft(s, 'student', 'course'), question + '\n再问边界')
  }],
  ['sending or explicitly clearing removes the persisted question', () => {
    const s = memory(); writeQuestionDraft(s, 'student', 'course', 'question'); writeQuestionDraft(s, 'student', 'course', '')
    assert.equal(readQuestionDraft(s, 'student', 'course'), ''); assert.equal(s.data.size, 0)
  }],
  ['only whitespace has no stored draft', () => {
    const s = memory(); writeQuestionDraft(s, 'student', 'course', 'question'); writeQuestionDraft(s, 'student', 'course', ' \n\t ')
    assert.equal(s.data.size, 0)
  }],
  ['legacy unowned key is never migrated into a signed in account', () => {
    const s = memory(); s.setItem('thinkmate-question-operating-systems', 'unknown owner question')
    assert.equal(readQuestionDraft(s, 'alice', 'operating-systems'), '')
  }],
  ['encoded key components prevent separator collisions', () => {
    const s = memory(); writeQuestionDraft(s, 'a:b', 'c', 'first'); writeQuestionDraft(s, 'a', 'b:c', 'second')
    assert.equal(readQuestionDraft(s, 'a:b', 'c'), 'first'); assert.equal(readQuestionDraft(s, 'a', 'b:c'), 'second')
  }],
  ['oversized stored or incoming draft is bounded by composer capacity', () => {
    const s = memory(); writeQuestionDraft(s, 'student', 'course', '问'.repeat(8200)); assert.equal(readQuestionDraft(s, 'student', 'course').length, 8000)
    s.data.set([...s.data.keys()][0], 'q'.repeat(9000)); assert.equal(readQuestionDraft(s, 'student', 'course').length, 8000)
  }],
  ['blocked storage APIs report failure and do not crash the composer', () => {
    const s = { getItem() { throw Error('blocked') }, setItem() { throw Error('quota') }, removeItem() { throw Error('blocked') } }
    assert.equal(readQuestionDraft(s, 'student', 'course'), '')
    assert.equal(writeQuestionDraft(s, 'student', 'course', 'question'), false)
    assert.equal(writeQuestionDraft(s, 'student', 'course', ''), false)
  }],
]
for (const [label, check] of checks) { check(); console.log('PASS ' + label) }
console.log(`${checks.length} question draft checks passed`)
