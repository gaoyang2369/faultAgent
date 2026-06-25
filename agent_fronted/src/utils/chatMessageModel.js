import { summarizeToolEventPayload } from './toolEventSummary.js'

const ROLE_ALIASES = {
  human: 'user',
  user: 'user',
  HumanMessage: 'user',
  UserMessage: 'user',
  ai: 'assistant',
  assistant: 'assistant',
  AIMessage: 'assistant',
  AssistantMessage: 'assistant',
  tool: 'tool',
  ToolMessage: 'tool',
  system: 'system',
  SystemMessage: 'system'
}

const MOJIBAKE_HINT_RE = /(Ã.|Â.|Ð.|Ñ.|ï¼|â€¦|â€”|ð|ä½|å¥|å|æ|ç³»|è¿|å·¥|è¯|æ¨)/u
const ONLY_QUESTION_MARKS_RE = /^[?？\s]+$/

const safeStringify = (value) => {
  try {
    return JSON.stringify(value)
  } catch (error) {
    return String(value ?? '')
  }
}

const extractContentFragments = (value) => {
  if (value == null) {
    return []
  }

  if (typeof value === 'string') {
    return [value]
  }

  if (typeof value === 'number' || typeof value === 'boolean') {
    return [String(value)]
  }

  if (Array.isArray(value)) {
    return value.flatMap(item => extractContentFragments(item))
  }

  if (typeof value === 'object') {
    if (typeof value.text === 'string') {
      return [value.text]
    }

    if (value.text && typeof value.text === 'object') {
      return extractContentFragments(value.text.value ?? value.text.text ?? value.text.content)
    }

    if (typeof value.value === 'string') {
      return [value.value]
    }

    if (typeof value.content === 'string' || Array.isArray(value.content)) {
      return extractContentFragments(value.content)
    }

    if (typeof value.output_text === 'string') {
      return [value.output_text]
    }

    if (typeof value.input_text === 'string') {
      return [value.input_text]
    }

    if (value.type === 'image_url' || value.image_url) {
      return ['[图片]']
    }

    return [safeStringify(value)]
  }

  return [String(value)]
}

const normalizeTimestamp = (value) => {
  if (typeof value === 'string' && value.trim()) {
    return value
  }
  return new Date(value || Date.now()).toISOString()
}

const isTerminalFailedState = (streamState = '', statusText = '') => {
  const normalizedState = String(streamState || '').trim().toLowerCase()
  if (normalizedState === 'failed' || normalizedState === 'interrupted') {
    return true
  }

  const normalizedStatus = String(statusText || '')
  return normalizedStatus.includes('失败') || normalizedStatus.includes('中断') || normalizedStatus.includes('停止')
}

const normalizeTerminalTaskSnapshot = (taskSnapshot, streamState = '', statusText = '') => {
  if (!taskSnapshot || !isTerminalFailedState(streamState, statusText)) {
    return taskSnapshot || null
  }

  const todos = Array.isArray(taskSnapshot.todos)
    ? taskSnapshot.todos.map(todo => {
        const status = String(todo?.status || '').trim().toLowerCase()
        return status === 'in_progress' || status === 'in-progress'
          ? { ...todo, status: 'interrupted' }
          : todo
      })
    : []

  const summary = {
    total: todos.length,
    pending: todos.filter(todo => todo?.status === 'pending').length,
    in_progress: todos.filter(todo => todo?.status === 'in_progress' || todo?.status === 'in-progress').length,
    completed: todos.filter(todo => todo?.status === 'completed' || todo?.status === 'done').length,
    interrupted: todos.filter(todo => todo?.status === 'interrupted' || todo?.status === 'stopped' || todo?.status === 'aborted').length
  }

  return {
    ...taskSnapshot,
    todos,
    summary,
    isLoading: false,
    statusHint: taskSnapshot.statusHint || (String(streamState).toLowerCase() === 'failed' ? '回复失败，执行已停止' : '执行已中断'),
    lifecycleState: 'interrupted',
    updatedAt: taskSnapshot.updatedAt || new Date().toISOString()
  }
}

const HISTORY_TOOL_FALLBACK_NAME = '工具'

const normalizeToolEvent = (toolEvent = {}) => {
  const details = normalizeMessageContent(toolEvent.details ?? toolEvent.rawContent ?? '')
  return {
    ...toolEvent,
    key: typeof toolEvent.key === 'string' && toolEvent.key.trim()
      ? toolEvent.key
      : `tool-event-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
    type: typeof toolEvent.type === 'string' && toolEvent.type.trim()
      ? toolEvent.type
      : 'tool_end',
    tool: typeof toolEvent.tool === 'string' && toolEvent.tool.trim()
      ? toolEvent.tool.trim()
      : HISTORY_TOOL_FALLBACK_NAME,
    label: typeof toolEvent.label === 'string' && toolEvent.label.trim()
      ? toolEvent.label.trim()
      : '工具事件',
    summary: normalizeMessageContent(toolEvent.summary ?? ''),
    details
  }
}

const isGenericHistoryToolName = (value) => {
  const normalized = String(value ?? '').trim()
  return !normalized || normalized === HISTORY_TOOL_FALLBACK_NAME || normalized === '工具返回结果'
}

const buildHistoryToolEvent = (message = {}, index = 0) => {
  const toolName = typeof message.name === 'string' && message.name.trim()
    ? message.name.trim()
    : HISTORY_TOOL_FALLBACK_NAME
  const content = normalizeMessageContent(message.content ?? message.message ?? '')
  return normalizeToolEvent({
    key: `history-tool-${index}-${toolName}-${Math.random().toString(16).slice(2, 8)}`,
    type: 'tool_end',
    tool: toolName,
    label: toolName === HISTORY_TOOL_FALLBACK_NAME ? '工具返回结果' : `工具执行完成：${toolName}`,
    summary: summarizeToolEventPayload(content),
    details: content,
    source: 'history'
  })
}

const preferCachedToolEvents = (serverToolEvents = [], cachedToolEvents = []) => {
  if (!Array.isArray(cachedToolEvents) || cachedToolEvents.length === 0) {
    return false
  }

  if (!Array.isArray(serverToolEvents) || serverToolEvents.length === 0) {
    return true
  }

  if (cachedToolEvents.length > serverToolEvents.length) {
    return true
  }

  const cachedHasStart = cachedToolEvents.some(event => event?.type === 'tool_start')
  const serverHasStart = serverToolEvents.some(event => event?.type === 'tool_start')
  if (cachedHasStart && !serverHasStart) {
    return true
  }

  const cachedHasNamedTool = cachedToolEvents.some(event => !isGenericHistoryToolName(event?.tool))
  const serverHasNamedTool = serverToolEvents.some(event => !isGenericHistoryToolName(event?.tool))
  if (cachedHasNamedTool && !serverHasNamedTool) {
    return true
  }

  return false
}

const pickBestMessageContent = (serverContent = '', cachedContent = '') => {
  if (serverContent && !isMessageContentDegraded(serverContent)) {
    return serverContent
  }
  if (cachedContent && !isMessageContentDegraded(cachedContent)) {
    return cachedContent
  }
  return serverContent || cachedContent || ''
}

const collapseServerHistoryMessages = (serverMessages = [], cachedMessages = []) => {
  const normalizedServer = serverMessages.map(normalizeChatMessage)
  const normalizedCache = cachedMessages.map(normalizeChatMessage)
  const cachedAssistantQueue = normalizedCache.filter(message => message.role === 'assistant' && !message.cacheHint)
  let cachedAssistantCursor = 0
  const collapsedMessages = []
  let pendingTurn = null

  const ensurePendingTurn = () => {
    if (!pendingTurn) {
      pendingTurn = {
        assistantMessages: [],
        toolMessages: []
      }
    }
    return pendingTurn
  }

  const flushPendingTurn = () => {
    if (!pendingTurn) return

    const assistantMessages = pendingTurn.assistantMessages
    const toolMessages = pendingTurn.toolMessages
    const cachedAssistant = cachedAssistantQueue[cachedAssistantCursor] || null
    if (assistantMessages.length > 0 || toolMessages.length > 0) {
      cachedAssistantCursor += 1
    }

    const lastAssistantMessage = assistantMessages[assistantMessages.length - 1] || null
    const serverAssistantContent = lastAssistantMessage?.content || ''
    const cachedAssistantContent = cachedAssistant?.content || ''
    const collapsedToolEvents = toolMessages.map((message, index) => buildHistoryToolEvent(message, index))
    const cachedToolEvents = Array.isArray(cachedAssistant?.toolEvents)
      ? cachedAssistant.toolEvents.map(normalizeToolEvent)
      : []
    const mergedToolEvents = preferCachedToolEvents(collapsedToolEvents, cachedToolEvents)
      ? cachedToolEvents
      : collapsedToolEvents

    const hydratedAssistantMessage = normalizeChatMessage({
      ...(cachedAssistant || {}),
      ...(lastAssistantMessage || {}),
      role: 'assistant',
      content: pickBestMessageContent(serverAssistantContent, cachedAssistantContent),
      timestamp: lastAssistantMessage?.timestamp || toolMessages[toolMessages.length - 1]?.timestamp || cachedAssistant?.timestamp,
      isMarkdown: true,
      toolEvents: mergedToolEvents,
      taskSnapshot: cachedAssistant?.taskSnapshot || lastAssistantMessage?.taskSnapshot || null,
      streamState: 'completed',
      statusText: ''
    })

    if (isRenderableChatMessage(hydratedAssistantMessage)) {
      collapsedMessages.push(hydratedAssistantMessage)
    }

    pendingTurn = null
  }

  normalizedServer.forEach((message) => {
    if (message.role === 'user') {
      flushPendingTurn()
      collapsedMessages.push(message)
      return
    }

    if (message.role === 'assistant') {
      ensurePendingTurn().assistantMessages.push(message)
      return
    }

    if (message.role === 'tool') {
      ensurePendingTurn().toolMessages.push(message)
      return
    }

    flushPendingTurn()
    collapsedMessages.push(message)
  })

  flushPendingTurn()
  return collapsedMessages
}

export const normalizeMessageRole = (message = {}) => {
  const candidates = [
    message.role,
    message.speaker,
    message.sender,
    message.messageRole,
    message.type
  ]

  for (const candidate of candidates) {
    if (typeof candidate !== 'string') continue
    const normalized = candidate.trim()
    if (!normalized) continue
    if (ROLE_ALIASES[normalized]) {
      return ROLE_ALIASES[normalized]
    }
    const lower = normalized.toLowerCase()
    if (ROLE_ALIASES[lower]) {
      return ROLE_ALIASES[lower]
    }
  }

  return 'assistant'
}

export const normalizeMessageContent = (content) => {
  if (typeof content === 'string') {
    return content
  }

  const fragments = extractContentFragments(content)
    .map(item => String(item ?? '').trim())
    .filter(Boolean)

  if (fragments.length === 0) {
    return ''
  }

  return fragments.join('\n')
}

export const isMessageContentDegraded = (content) => {
  if (typeof content !== 'string') {
    return false
  }

  const normalized = content.trim()
  if (!normalized) {
    return false
  }

  if (normalized.includes('\uFFFD')) {
    return true
  }

  if (normalized.length >= 2 && ONLY_QUESTION_MARKS_RE.test(normalized)) {
    return true
  }

  return MOJIBAKE_HINT_RE.test(normalized)
}

export const normalizeChatMessage = (message = {}) => {
  const role = normalizeMessageRole(message)
  const streamState = typeof message.streamState === 'string' && message.streamState
    ? message.streamState
    : 'completed'
  const statusText = typeof message.statusText === 'string' ? message.statusText : ''
  const normalizedToolEvents = Array.isArray(message.toolEvents)
    ? message.toolEvents.map(normalizeToolEvent)
    : []
  const normalizedToolLifecycleLedger = Array.isArray(message.toolLifecycleLedger)
    ? message.toolLifecycleLedger
    : Array.isArray(message.tool_lifecycle_ledger)
      ? message.tool_lifecycle_ledger
      : []
  const sqlArtifact = message.sqlArtifact || message.sql_artifact || null
  const knowledgeArtifact = message.knowledgeArtifact || message.knowledge_artifact || null
  const analysisArtifact = message.analysisArtifact || message.analysis_artifact || null
  const reportArtifact = message.reportArtifact || message.report_artifact || null
  const workorderDecision = message.workorderDecision || message.workorder_decision || null
  const uiPayload = message.uiPayload || message.ui_payload || null
  const traceId = message.traceId || message.trace_id || message.trace?.trace_id || null
  const requestId = message.requestId || message.request_id || message.trace?.request_id || null

  return {
    ...message,
    role,
    content: normalizeMessageContent(message.content ?? message.message ?? ''),
    timestamp: normalizeTimestamp(message.timestamp),
    isMarkdown: role === 'assistant' ? true : Boolean(message.isMarkdown),
    streamState,
    statusText,
    toolEvents: normalizedToolEvents,
    toolLifecycleLedger: normalizedToolLifecycleLedger,
    sqlArtifact,
    knowledgeArtifact,
    analysisArtifact,
    reportArtifact,
    workorderDecision,
    workorder_decision: workorderDecision,
    uiPayload,
    ui_payload: uiPayload,
    traceId,
    trace_id: traceId,
    requestId,
    request_id: requestId,
    taskSnapshot: normalizeTerminalTaskSnapshot(message.taskSnapshot, streamState, statusText)
  }
}

export const isRenderableChatMessage = (message = {}) => {
  const normalized = normalizeChatMessage(message)
  if (normalized.role === 'tool') {
    return Boolean(normalized.showInPrimaryView)
  }

  if (normalized.role === 'user') {
    return Boolean(normalized.content.trim())
  }

  if (normalized.role !== 'assistant') {
    return Boolean(normalized.content.trim()) || Boolean(normalized.cacheHint)
  }

  if (normalized.content.trim()) {
    return true
  }

  if (normalized.chartData || normalized.imageUrl) {
    return true
  }

  if (normalized.analysisArtifact || normalized.sqlArtifact || normalized.knowledgeArtifact) {
    return true
  }

  if (normalized.workorderDecision) {
    return true
  }

  if (Array.isArray(normalized.toolEvents) && normalized.toolEvents.length > 0) {
    return true
  }

  if (Array.isArray(normalized.toolLifecycleLedger) && normalized.toolLifecycleLedger.length > 0) {
    return true
  }

  if (normalized.taskSnapshot && Array.isArray(normalized.taskSnapshot.todos) && normalized.taskSnapshot.todos.length > 0) {
    return true
  }

  if (normalized.taskSnapshot?.isLoading) {
    return true
  }

  if (normalized.isStreaming || normalized.cacheHint) {
    return true
  }

  return false
}

export const mergeMessagesWithLocalCache = (serverMessages = [], cachedMessages = []) => {
  const normalizedServer = collapseServerHistoryMessages(serverMessages, cachedMessages)
  const normalizedCache = cachedMessages.map(normalizeChatMessage)

  if (normalizedServer.length === 0 || normalizedCache.length === 0) {
    return normalizedServer
  }

  const mergedMessages = normalizedServer.map((message, index) => {
    const cached = normalizedCache[index]
    if (!cached || cached.role !== message.role) {
      return message
    }

    const shouldRestoreContent = (
      (!message.content && Boolean(cached.content)) ||
      (isMessageContentDegraded(message.content) && !isMessageContentDegraded(cached.content))
    )

    if (!shouldRestoreContent) {
      const shouldRestoreToolEvents = (
        (!Array.isArray(message.toolEvents) || message.toolEvents.length === 0) &&
        Array.isArray(cached.toolEvents) &&
        cached.toolEvents.length > 0
      )
      const shouldRestoreTaskSnapshot = (
        (!message.taskSnapshot || !Array.isArray(message.taskSnapshot.todos) || message.taskSnapshot.todos.length === 0) &&
        Boolean(cached.taskSnapshot)
      )
      const shouldRestoreToolLifecycleLedger = (
        (!Array.isArray(message.toolLifecycleLedger) || message.toolLifecycleLedger.length === 0) &&
        Array.isArray(cached.toolLifecycleLedger) &&
        cached.toolLifecycleLedger.length > 0
      )
      const shouldRestoreArtifacts = (
        (!message.analysisArtifact && cached.analysisArtifact) ||
        (!message.sqlArtifact && cached.sqlArtifact) ||
        (!message.knowledgeArtifact && cached.knowledgeArtifact) ||
        (!message.workorderDecision && cached.workorderDecision)
      )

      if (!shouldRestoreToolEvents && !shouldRestoreTaskSnapshot && !shouldRestoreToolLifecycleLedger && !shouldRestoreArtifacts) {
        return message
      }

      return {
        ...message,
        ...(shouldRestoreToolEvents ? { toolEvents: cached.toolEvents } : {}),
        ...(shouldRestoreTaskSnapshot ? { taskSnapshot: cached.taskSnapshot } : {}),
        ...(shouldRestoreToolLifecycleLedger ? { toolLifecycleLedger: cached.toolLifecycleLedger } : {}),
        ...(!message.analysisArtifact && cached.analysisArtifact ? { analysisArtifact: cached.analysisArtifact } : {}),
        ...(!message.sqlArtifact && cached.sqlArtifact ? { sqlArtifact: cached.sqlArtifact } : {}),
        ...(!message.knowledgeArtifact && cached.knowledgeArtifact ? { knowledgeArtifact: cached.knowledgeArtifact } : {}),
        ...(!message.workorderDecision && cached.workorderDecision ? { workorderDecision: cached.workorderDecision } : {}),
        ...(!message.traceId && cached.traceId ? { traceId: cached.traceId, trace_id: cached.traceId } : {}),
        ...(!message.requestId && cached.requestId ? { requestId: cached.requestId, request_id: cached.requestId } : {})
      }
    }

    return {
      ...message,
      content: cached.content,
      toolEvents: Array.isArray(message.toolEvents) && message.toolEvents.length > 0
        ? message.toolEvents
        : cached.toolEvents,
      taskSnapshot: message.taskSnapshot || cached.taskSnapshot,
      toolLifecycleLedger: Array.isArray(message.toolLifecycleLedger) && message.toolLifecycleLedger.length > 0
        ? message.toolLifecycleLedger
        : cached.toolLifecycleLedger,
      analysisArtifact: message.analysisArtifact || cached.analysisArtifact || null,
      sqlArtifact: message.sqlArtifact || cached.sqlArtifact || null,
      knowledgeArtifact: message.knowledgeArtifact || cached.knowledgeArtifact || null,
      workorderDecision: message.workorderDecision || cached.workorderDecision || null,
      traceId: message.traceId || cached.traceId || null,
      trace_id: message.traceId || cached.traceId || null,
      requestId: message.requestId || cached.requestId || null,
      request_id: message.requestId || cached.requestId || null
    }
  })

  const trailingCachedMessages = normalizedCache.slice(normalizedServer.length)
  if (
    trailingCachedMessages.length === 1 &&
    trailingCachedMessages[0]?.role === 'assistant' &&
    trailingCachedMessages[0]?.streamState === 'interrupted' &&
    normalizedServer[normalizedServer.length - 1]?.role === 'user'
  ) {
    mergedMessages.push(trailingCachedMessages[0])
  }

  return mergedMessages
}
