const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const code = ts.transpileModule(fs.readFileSync(path.resolve(__dirname, '../src/lib/trace-status.ts'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
const helpers = {}; new Function('exports', code)(helpers)
const { traceDisplayStates, traceStateLabels } = helpers
const event = (node, status) => ({ node, status })

const checks = [
  ['finished history keeps a start as history even without a terminal event', () => {
    assert.deepEqual(traceDisplayStates([event('teaching', 'running')], false), ['started'])
    assert.notEqual(traceStateLabels.started, traceStateLabels.completed)
  }],
  ['a current latest start remains visibly active while the response is pending', () => {
    for (const status of ['running', 'started', 'pending']) assert.deepEqual(traceDisplayStates([event('review', status)], true), ['running'])
  }],
  ['a later terminal event settles the earlier start before the whole answer finishes', () => {
    assert.deepEqual(traceDisplayStates([event('review', 'running'), event('review', 'completed'), event('teaching', 'running')], true), ['started', 'completed', 'running'])
  }],
  ['failure is retained when the same node begins another attempt', () => {
    assert.deepEqual(traceDisplayStates([event('review', 'running'), event('review', 'failed'), event('review', 'running')], true), ['started', 'failed', 'running'])
  }],
  ['a later successful retry does not retroactively turn the failed event into success', () => {
    assert.deepEqual(traceDisplayStates([event('review', 'failed'), event('review', 'completed')], false), ['failed', 'completed'])
  }],
  ['different nodes do not incorrectly settle each other', () => {
    assert.deepEqual(traceDisplayStates([event('teaching', 'running'), event('search_materials', 'completed')], true), ['running', 'completed'])
  }],
  ['ending the message stops every spinner while preserving existing outcomes', () => {
    assert.deepEqual(traceDisplayStates([event('teaching', 'running'), event('review', 'running'), event('calculate', 'failed'), event('search_materials', 'completed')], false), ['started', 'started', 'failed', 'completed'])
  }],
  ['a new failure has no successful or running display state', () => {
    assert.deepEqual(traceDisplayStates([event('calculate', 'running'), event('calculate', 'failed')], true), ['started', 'failed'])
  }],
  ['unknown statuses are honest about the missing outcome', () => {
    assert.deepEqual(traceDisplayStates([event('future_node', 'unknown-status')], false), ['unknown'])
    assert.equal(traceStateLabels.unknown, '状态未确认')
  }],
  ['recovery continues learning without marking failed verification successful', () => {
    assert.deepEqual(traceDisplayStates([event('review', 'failed'), event('chat_recovery', 'degraded')], false), ['failed', 'degraded'])
    assert.equal(traceStateLabels.degraded, '继续学习')
  }],
  ['an unrecognized later event cannot leave an earlier start falsely animated', () => {
    assert.deepEqual(traceDisplayStates([event('review', 'running'), event('review', 'unrecognized')], true), ['started', 'unknown'])
  }],
  ['repeated starts for the same node animate only the latest attempt', () => {
    assert.deepEqual(traceDisplayStates([event('review', 'running'), event('review', 'started')], true), ['started', 'running'])
  }],
  ['historical readback does not mutate persisted trace events', () => {
    const history = Object.freeze([Object.freeze(event('teaching', 'running')), Object.freeze(event('review', 'running')), Object.freeze(event('review', 'completed')), Object.freeze(event('citation_validation', 'completed'))])
    assert.deepEqual(traceDisplayStates(history, false), ['started', 'started', 'completed', 'completed'])
    assert.equal(history[0].status, 'running'); assert.equal(history[1].status, 'running')
  }],
]
for (const [name, check] of checks) { check(); console.log(`PASS ${name}`) }
console.log(`${checks.length} trace state checks passed`)
