const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
const code = ts.transpileModule(fs.readFileSync(path.resolve(__dirname, '../src/lib/quiz-draft.ts'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
const helpers = {}; new Function('exports', code)(helpers)
const { QuizDraftQueue } = helpers
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }

const checks = [
  ['rapid choices wait for earlier writes and use acknowledged versions', async () => {
    const gate = deferred(); const calls = []
    const queue = new QuizDraftQueue(0, async (answers, expected) => {
      calls.push({ answers, expected }); if (calls.length === 1) await gate.promise
      return { draft_answers: answers, draft_version: expected + 1 }
    })
    const first = queue.save({ q1: 'A' }); const latest = queue.save({ q1: 'B', q2: 'C' })
    await Promise.resolve(); assert.equal(calls.length, 1)
    gate.resolve(); await Promise.all([first, latest])
    assert.deepEqual(calls, [{ answers: { q1: 'A' }, expected: 0 }, { answers: { q1: 'B', q2: 'C' }, expected: 1 }])
  }],
  ['queued payload is a snapshot even if its caller changes the original object', async () => {
    const input = { q1: 'A' }; let actual
    const queue = new QuizDraftQueue(4, async (answers, expected) => { actual = answers; return { draft_answers: answers, draft_version: expected + 1 } })
    const save = queue.save(input); input.q1 = 'D'; input.q2 = 'B'; await save
    assert.deepEqual(actual, { q1: 'A' })
  }],
  ['a resumed quiz starts with its real server version', async () => {
    let actualVersion
    const queue = new QuizDraftQueue(12, async (answers, expected) => { actualVersion = expected; return { draft_answers: answers, draft_version: 13 } })
    await queue.save({ q1: 'C' }); assert.equal(actualVersion, 12)
  }],
  ['a failed save is visible and does not invent a new acknowledged version', async () => {
    const versions = []; let fail = true
    const queue = new QuizDraftQueue(7, async (answers, expected) => {
      versions.push(expected); if (fail) { fail = false; throw new Error('offline') }
      return { draft_answers: answers, draft_version: 8 }
    })
    await assert.rejects(queue.save({ q1: 'A' }), /offline/)
    await queue.save({ q1: 'B' }); assert.deepEqual(versions, [7, 7])
  }],
  ['waiting before resume or submit includes every previously queued snapshot', async () => {
    const gate = deferred(); const seen = []
    const queue = new QuizDraftQueue(0, async (answers, expected) => { if (expected === 0) await gate.promise; seen.push(answers); return { draft_answers: answers, draft_version: expected + 1 } })
    const first = queue.save({ q1: 'A' }); const last = queue.save({ q1: 'D' }); let complete = false
    const waiting = queue.settled().then(() => { complete = true })
    await Promise.resolve(); assert.equal(complete, false); gate.resolve()
    await Promise.all([first, last, waiting]); assert.deepEqual(seen.at(-1), { q1: 'D' }); assert.equal(complete, true)
  }],
  ['idempotent acknowledgements do not assume the server incremented its version', async () => {
    const versions = []
    const queue = new QuizDraftQueue(5, async (answers, expected) => { versions.push(expected); return { draft_answers: answers, draft_version: expected } })
    await queue.save({ q1: 'A' }); await queue.save({ q1: 'A' }); assert.deepEqual(versions, [5, 5])
  }],
  ['queues from different quizzes do not share versions or block one another', async () => {
    const gate = deferred(); let otherSaved = false
    const firstQueue = new QuizDraftQueue(0, async answers => { await gate.promise; return { draft_answers: answers, draft_version: 1 } })
    const otherQueue = new QuizDraftQueue(20, async (answers, expected) => { assert.equal(expected, 20); otherSaved = true; return { draft_answers: answers, draft_version: 21 } })
    const pending = firstQueue.save({ first: 'A' }); await otherQueue.save({ other: 'B' }); assert.equal(otherSaved, true); gate.resolve(); await pending
  }],
  ['a rejected write does not prevent submission from waiting for settled transport', async () => {
    const queue = new QuizDraftQueue(0, async () => { throw new Error('conflict') })
    await assert.rejects(queue.save({ q1: 'A' }), /conflict/); await queue.settled()
  }],
]

;(async () => { for (const [name, check] of checks) { await check(); console.log(`PASS ${name}`) } console.log(`${checks.length} quiz draft queue checks passed`) })().catch(error => { console.error(error); process.exitCode = 1 })
