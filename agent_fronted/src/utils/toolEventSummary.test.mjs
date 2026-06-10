import assert from 'node:assert/strict'

import { summarizeToolEventPayload } from './toolEventSummary.js'

const sqlSummary = summarizeToolEventPayload({
  rows: [{ fault_code: 'F1030-0/0/0' }],
  total_rows: 1,
  truncated: false
})

assert.notEqual(sqlSummary, '[object Object]')
assert.match(sqlSummary, /返回 1 行/)

const todosSummary = summarizeToolEventPayload({
  todos: [
    { content: '查询最新运行数据', status: 'completed' },
    { content: '分析原因', status: 'pending' }
  ]
})

assert.equal(todosSummary, 'todos: 2 项，已完成 1 项')

console.log('toolEventSummary checks passed')
