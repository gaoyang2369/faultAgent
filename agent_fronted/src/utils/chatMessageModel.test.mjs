import assert from 'node:assert/strict'

import {
  isRenderableChatMessage,
  isMessageContentDegraded,
  mergeMessagesWithLocalCache,
  normalizeChatMessage
} from './chatMessageModel.js'

const humanMessage = normalizeChatMessage({
  role: 'human',
  content: '测试用户消息'
})

assert.equal(humanMessage.role, 'user')
assert.equal(humanMessage.content, '测试用户消息')

const structuredAssistantMessage = normalizeChatMessage({
  type: 'AIMessage',
  content: [
    { type: 'text', text: '第一段' },
    { type: 'text', text: { value: '第二段' } }
  ]
})

assert.equal(structuredAssistantMessage.role, 'assistant')
assert.equal(structuredAssistantMessage.content, '第一段\n第二段')

assert.equal(isMessageContentDegraded('???????'), true)
assert.equal(isMessageContentDegraded('ä½ å¥½'), true)
assert.equal(isMessageContentDegraded('正常中文'), false)

const mergedMessages = mergeMessagesWithLocalCache(
  [
    { role: 'human', content: '???????' },
    { role: 'ai', content: '服务端回复正常' }
  ],
  [
    { role: 'user', content: '根据故障手册，提取关键排查建议' },
    { role: 'assistant', content: '服务端回复正常' }
  ]
)

assert.equal(mergedMessages[0].role, 'user')
assert.equal(mergedMessages[0].content, '根据故障手册，提取关键排查建议')
assert.equal(mergedMessages[1].role, 'assistant')
assert.equal(mergedMessages[1].content, '服务端回复正常')

const hydratedHistoryMessages = mergeMessagesWithLocalCache(
  [
    { type: 'HumanMessage', content: '查询 F01002' },
    { type: 'AIMessage', content: '我来帮您查询故障代码。' },
    { type: 'ToolMessage', name: 'query_knowledge_base', content: '文档片段：F01002 内部软件错误' },
    { type: 'AIMessage', content: '【结论】F01002 表示内部软件错误。' }
  ],
  [
    { role: 'user', content: '查询 F01002' },
    {
      role: 'assistant',
      content: '【结论】F01002 表示内部软件错误。',
      toolEvents: [
        { key: 'cached-start', type: 'tool_start', tool: 'query_knowledge_base', label: '正在调用工具：query_knowledge_base' },
        { key: 'cached-end', type: 'tool_end', tool: 'query_knowledge_base', label: '工具执行完成：query_knowledge_base', summary: '文档片段：F01002 内部软件错误' }
      ]
    }
  ]
)

assert.equal(hydratedHistoryMessages.length, 2)
assert.equal(hydratedHistoryMessages[0].role, 'user')
assert.equal(hydratedHistoryMessages[1].role, 'assistant')
assert.equal(hydratedHistoryMessages[1].content, '【结论】F01002 表示内部软件错误。')
assert.equal(hydratedHistoryMessages[1].toolEvents.length, 2)
assert.equal(hydratedHistoryMessages.some(message => message.role === 'tool'), false)
assert.equal(isRenderableChatMessage({ role: 'tool', content: '原始工具结果' }), false)

const interruptedMessages = mergeMessagesWithLocalCache(
  [
    { type: 'HumanMessage', content: '请给我一个长回复' }
  ],
  [
    { role: 'user', content: '请给我一个长回复' },
    {
      role: 'assistant',
      content: '这是已经生成到一半的回答',
      streamState: 'interrupted',
      statusText: '已停止生成'
    }
  ]
)

assert.equal(interruptedMessages.length, 2)
assert.equal(interruptedMessages[1].role, 'assistant')
assert.equal(interruptedMessages[1].streamState, 'interrupted')
assert.equal(interruptedMessages[1].content, '这是已经生成到一半的回答')

const failedMessage = normalizeChatMessage({
  role: 'assistant',
  content: 'request failed',
  streamState: 'failed',
  statusText: 'reply failed',
  taskSnapshot: {
    todos: [
      { id: 'step-1', title: 'done', status: 'completed' },
      { id: 'step-2', title: 'running', status: 'in_progress' }
    ],
    summary: {
      total: 2,
      pending: 0,
      in_progress: 1,
      completed: 1,
      interrupted: 0
    }
  }
})

assert.equal(failedMessage.taskSnapshot.lifecycleState, 'interrupted')
assert.equal(failedMessage.taskSnapshot.todos[1].status, 'interrupted')
assert.equal(failedMessage.taskSnapshot.summary.in_progress, 0)
assert.equal(failedMessage.taskSnapshot.summary.interrupted, 1)

const artifactOnlyMessage = normalizeChatMessage({
  role: 'assistant',
  content: '',
  analysis_artifact: {
    conclusion: 'G120电机1存在故障码A07089',
    recommendations: ['立即处置：确认现场安全条件']
  },
  sql_artifact: {
    summary: 'SQL 返回 50 条'
  },
  knowledge_artifact: {
    query: 'A07089'
  },
  ui_payload: {
    type: 'diagnosis_card',
    device_label: 'G120电机1'
  }
})

assert.equal(artifactOnlyMessage.analysisArtifact.conclusion, 'G120电机1存在故障码A07089')
assert.equal(artifactOnlyMessage.sqlArtifact.summary, 'SQL 返回 50 条')
assert.equal(artifactOnlyMessage.knowledgeArtifact.query, 'A07089')
assert.equal(artifactOnlyMessage.uiPayload.type, 'diagnosis_card')
assert.equal(artifactOnlyMessage.ui_payload.device_label, 'G120电机1')
assert.equal(isRenderableChatMessage(artifactOnlyMessage), true)

console.log('chatMessageModel checks passed')
