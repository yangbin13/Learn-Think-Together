const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')

const source = fs.readFileSync(path.resolve(__dirname, '../src/lib/starter-prompts.ts'), 'utf8')
const code = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText
const helpers = {}
new Function('exports', code)(helpers)
const { starterPrompts } = helpers

const course = '数据结构与算法图解'
const explain = starterPrompts('uploaded-course', course, 'explain')
const socratic = starterPrompts('uploaded-course', course, 'socratic')
assert.equal(explain.length, 3)
assert.match(explain[0], /教材目录.*三个主要学习模块/)
assert.match(explain[1], /重点概念.*生活例子解释/)
assert.equal(socratic.length, 3)
assert(socratic.every(prompt => prompt.includes('只问我一个问题') || prompt.includes('只问我一个可回答的入门问题')))
assert(socratic.every(prompt => !prompt.includes('概括') && !prompt.includes('推荐前三')))
assert.notDeepEqual(explain, socratic)
assert.equal(starterPrompts('data-structures', '数据结构', 'explain')[0], '用一个生活例子解释栈和队列的区别')
console.log('PASS starter prompts match the selected teaching mode')
