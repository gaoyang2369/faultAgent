import { ref, type Ref } from 'vue'

import { chatAPI } from '@/services/api'
import {
  completeTaskSnapshot,
  createTaskSnapshot,
  hasVisibleTaskSnapshot,
  interruptTaskSnapshot
} from '@/utils/taskState'
import { summarizeToolEventPayload } from '@/utils/toolEventSummary.js'
import {
  isRenderableChatMessage,
  mergeMessagesWithLocalCache,
  normalizeChatMessage
} from '@/utils/chatMessageModel.js'
import {
  buildCachedHistoryTitle,
  buildLocalCacheFallbackMessage,
  getCachedServiceConversation,
  isSignedThreadId,
  listCachedServiceHistory,
  removeServiceConversationCache,
  renameServiceConversationCache,
  updateServiceConversationTitle,
  upsertServiceConversationCache
} from '@/utils/chatSessionCache'

type ChatHistoryItem = {
  id: string
  title?: string
  source?: 'server' | 'local-cache'
}

type ToolEvent = {
  key: string
  type: string
  tool: string
  label: string
  summary?: string
  truncated?: boolean
  stage?: string
  currentStage?: string
  actionGuard?: any
  runId?: string
}

type Message = Record<string, any>
type ActiveStreamHandle = {
  close: (reason?: string) => void
  stop?: (reason?: string) => Promise<any>
  streamId?: string
}

interface UseChatStreamOptions {
  currentChatId: Ref<string | number | null>
  chatHistory: Ref<ChatHistoryItem[]>
  userInput: Ref<string>
  assignTodosState: (todos?: any[], summary?: any) => any
  extractTodosFromToolResult: (result: any) => any[] | null
  fetchTodosForThread: (threadId?: string | null) => Promise<any>
  userIdentityStore: Record<string, any>
  adjustTextareaHeight: () => void
  scrollToBottom: (options?: {
    force?: boolean
    behavior?: ScrollBehavior
    markNewContent?: boolean
  }) => Promise<boolean> | boolean
  onSendMessage?: () => void
  onAssistantToken?: (token: string) => void
  onAssistantComplete?: () => void
}

const createToolEventKey = (tool: string, type: string) =>
  `${tool}-${type}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`

const buildHistoryTitle = (threadId: string, source: 'server' | 'local-cache' = 'server') =>
  source === 'local-cache' ? `本地缓存 · 咨询 ${threadId.slice(-6)}` : `咨询 ${threadId.slice(-6)}`

const PUBLIC_THINKING_STATUS = '思考中...'
const THINKING_STREAM_STATES = new Set(['connecting', 'reasoning', 'streaming', 'tool_running'])
const ASSISTANT_SPEAKER_NAME = '故障诊断 Agent'

const normalizeMessageForView = (message: Message) => normalizeChatMessage(message)
const isRenderableMessage = (message: Message) => isRenderableChatMessage(normalizeMessageForView(message))

export const useChatStream = ({
  currentChatId,
  chatHistory,
  userInput,
  assignTodosState,
  extractTodosFromToolResult,
  fetchTodosForThread,
  userIdentityStore,
  adjustTextareaHeight,
  scrollToBottom,
  onSendMessage,
  onAssistantToken,
  onAssistantComplete
}: UseChatStreamOptions) => {
  const currentMessages = ref<Message[]>([])
  const isStreaming = ref(false)
  const isStopping = ref(false)
  const historyLoading = ref(false)
  const historyError = ref('')
  const historyHasMore = ref(false)
  const historyNextCursor = ref<string | null>(null)
  const historySearchQuery = ref('')
  const activeStream = ref<ActiveStreamHandle | null>(null)
  const isDisposed = ref(false)
  const localCacheOnly = ref(false)
  const deletedChatIds = new Set<string>()
  let activeRequestVersion = 0
  let isSubmittingMessage = false

  const getLastAssistantIndex = () => {
    for (let index = currentMessages.value.length - 1; index >= 0; index -= 1) {
      if (currentMessages.value[index]?.role === 'assistant') {
        return index
      }
    }
    return -1
  }

  const patchLastAssistantMessage = (patch: Record<string, any>) => {
    const lastIndex = getLastAssistantIndex()
    if (lastIndex < 0) return
    currentMessages.value.splice(lastIndex, 1, {
      ...currentMessages.value[lastIndex],
      ...patch
    })
  }

  const updateAssistantContent = (content: string) => {
    patchLastAssistantMessage({ content })
  }

  const updateAssistantState = (streamState: string, statusText: string) => {
    const publicStatusText = THINKING_STREAM_STATES.has(streamState)
      ? PUBLIC_THINKING_STATUS
      : statusText
    patchLastAssistantMessage({
      streamState,
      statusText: publicStatusText,
      isStreaming: ['connecting', 'reasoning', 'streaming', 'tool_running'].includes(streamState)
    })
  }

  const updateAssistantTaskSnapshot = (taskSnapshot: any) => {
    patchLastAssistantMessage({
      taskSnapshot
    })
  }

  const buildInterruptedTaskSnapshot = (statusHint: string) => {
    const existingTaskSnapshot = currentMessages.value[getLastAssistantIndex()]?.taskSnapshot
    return interruptTaskSnapshot(existingTaskSnapshot, statusHint) || existingTaskSnapshot
  }

  const ensureLatestAssistantTaskSnapshot = (taskSnapshot: any) => {
    if (!hasVisibleTaskSnapshot(taskSnapshot)) return
    const lastIndex = getLastAssistantIndex()
    if (lastIndex < 0) return
    const existingSnapshot = currentMessages.value[lastIndex]?.taskSnapshot
    if (hasVisibleTaskSnapshot(existingSnapshot)) return
    currentMessages.value.splice(lastIndex, 1, {
      ...currentMessages.value[lastIndex],
      taskSnapshot
    })
  }

  const appendToolEvent = (toolEvent: ToolEvent) => {
    const lastIndex = getLastAssistantIndex()
    if (lastIndex < 0) return
    const prevEvents = Array.isArray(currentMessages.value[lastIndex]?.toolEvents)
      ? currentMessages.value[lastIndex].toolEvents
      : []

    currentMessages.value.splice(lastIndex, 1, {
      ...currentMessages.value[lastIndex],
      toolEvents: [...prevEvents, toolEvent]
    })
  }

  const mergeWorkflowStages = (incomingStages: string[] = []) => {
    const lastIndex = getLastAssistantIndex()
    if (lastIndex < 0) return

    const existingStages = Array.isArray(currentMessages.value[lastIndex]?.workflowStages)
      ? currentMessages.value[lastIndex].workflowStages
      : []
    const nextStages = [...existingStages]

    incomingStages.forEach((stage) => {
      if (stage && !nextStages.includes(stage)) {
        nextStages.push(stage)
      }
    })

    currentMessages.value.splice(lastIndex, 1, {
      ...currentMessages.value[lastIndex],
      workflowStages: nextStages
    })
  }

  const updateWorkflowProgress = (patch: Record<string, any>) => {
    const lastIndex = getLastAssistantIndex()
    if (lastIndex < 0) return
    currentMessages.value.splice(lastIndex, 1, {
      ...currentMessages.value[lastIndex],
      ...patch
    })
  }

  const mergeWorkflowStageDetail = (detailPatch: Record<string, any>) => {
    const stage = detailPatch?.stage
    if (!stage) return

    const lastIndex = getLastAssistantIndex()
    if (lastIndex < 0) return

    const existingDetails = Array.isArray(currentMessages.value[lastIndex]?.workflowStageDetails)
      ? currentMessages.value[lastIndex].workflowStageDetails
      : []
    const nextDetails = [...existingDetails]
    const targetIndex = nextDetails.findIndex((item) => item?.stage === stage)

    if (targetIndex >= 0) {
      const previous = nextDetails[targetIndex]
      nextDetails[targetIndex] = {
        ...previous,
        ...detailPatch,
        tool_count: (previous?.tool_count || 0) + (detailPatch?.tool_count || 0)
      }
    } else {
      nextDetails.push(detailPatch)
    }

    currentMessages.value.splice(lastIndex, 1, {
      ...currentMessages.value[lastIndex],
      workflowStageDetails: nextDetails
    })
  }

  const serializeMessagesForCache = () =>
    currentMessages.value
      .filter(message => !message?.cacheHint)
      .filter(message => isRenderableMessage(message))
      .map(message => normalizeMessageForView(message))

  const persistConversationCache = (
    threadId: string | null | undefined,
    source: 'server' | 'local-cache' = 'server',
    extra: Record<string, any> = {}
  ) => {
    if (!threadId) return
    if (deletedChatIds.has(threadId)) return

    const hasMessages = Object.prototype.hasOwnProperty.call(extra, 'messages')
    const hasTodos = Object.prototype.hasOwnProperty.call(extra, 'todos')
    const hasSummary = Object.prototype.hasOwnProperty.call(extra, 'summary')

    upsertServiceConversationCache(threadId, {
      source,
      title: buildHistoryTitle(threadId, source),
      messages: hasMessages ? extra.messages : serializeMessagesForCache(),
      ...(hasTodos ? { todos: extra.todos } : {}),
      ...(hasSummary ? { summary: extra.summary } : {})
    })
  }

  const ensureChatHistoryItem = (threadId: string | null | undefined, source: 'server' | 'local-cache' = 'server') => {
    if (!threadId) return
    if (deletedChatIds.has(threadId)) return
    currentChatId.value = threadId
    const cachedConversation = getCachedServiceConversation(threadId)

    const nextItem = {
      id: threadId,
      title: cachedConversation?.title || buildHistoryTitle(threadId, source),
      source
    }

    const existingIndex = chatHistory.value.findIndex(item => item.id === threadId)
    if (existingIndex >= 0) {
      const existingItem = chatHistory.value[existingIndex]
      if (!existingItem) return
      const nextHistory = [...chatHistory.value]
      nextHistory.splice(existingIndex, 1, {
        ...existingItem,
        id: threadId,
        title: cachedConversation?.title || existingItem.title || nextItem.title,
        source
      })
      chatHistory.value = nextHistory
      return
    }

    chatHistory.value = [nextItem, ...chatHistory.value]
  }

  const removeChatHistoryItem = (threadId: string | null | undefined) => {
    if (!threadId) return
    chatHistory.value = chatHistory.value.filter(item => item.id !== threadId)
  }

  const closeActiveStream = (reason = 'interrupted') => {
    if (activeStream.value) {
      activeStream.value.close(reason)
      activeStream.value = null
    }
    chatAPI.closeActiveStream(reason)
  }

  const stopStreaming = async () => {
    if (!isStreaming.value || !activeStream.value) return

    isStopping.value = true
    const hasPartialContent = Boolean(currentMessages.value[getLastAssistantIndex()]?.content?.trim())
    patchLastAssistantMessage({
      streamState: 'interrupted',
      statusText: '正在停止生成...'
    })
    userIdentityStore.setStatus?.('connected')

    const handle = activeStream.value
    if (handle?.stop) {
      handle.stop('user_stop').catch((error: any) => {
        console.warn('停止流式生成失败:', error)
      })
    }
    handle?.close('user_stop')

    if (!hasPartialContent) {
      patchLastAssistantMessage({
        content: '回答已停止，尚未生成可显示内容。'
      })
    }
  }

  const startNewChat = async () => {
    activeRequestVersion += 1
    closeActiveStream('interrupted')
    localCacheOnly.value = false
    currentChatId.value = null
    currentMessages.value = []
    assignTodosState([])
    isStreaming.value = false
    isStopping.value = false
    userIdentityStore.setStatus?.('idle')
    await scrollToBottom({ force: true, markNewContent: false })
  }

  const applyLocalCacheConversation = async (chatId: string, fallbackReason = '') => {
    const cachedConversation = getCachedServiceConversation(chatId)
    if (!cachedConversation) {
      currentMessages.value = []
      assignTodosState([])
      localCacheOnly.value = false
      return false
    }

    localCacheOnly.value = true
    currentMessages.value = [
      buildLocalCacheFallbackMessage(),
      ...cachedConversation.messages
        .map(normalizeMessageForView)
        .filter(message => isRenderableMessage(message))
    ]
    assignTodosState(cachedConversation.todos || [], cachedConversation.summary || null)
    ensureChatHistoryItem(chatId, 'local-cache')
    if (fallbackReason) {
      console.warn(fallbackReason)
    }
    await scrollToBottom({ force: true, markNewContent: false })
    return true
  }

  const loadChat = async (chatId: string | null) => {
    if (!chatId) return
    if (deletedChatIds.has(chatId)) return

    activeRequestVersion += 1
    closeActiveStream('interrupted')
    currentChatId.value = chatId

    try {
      const messages = await chatAPI.getChatMessages(chatId, 'service')
      if (messages.length > 0) {
        const cachedConversation = getCachedServiceConversation(chatId)
        localCacheOnly.value = false
        currentMessages.value = mergeMessagesWithLocalCache(
          messages as Message[],
          cachedConversation?.messages || []
        ).filter(message => isRenderableMessage(message))
        ensureChatHistoryItem(chatId, 'server')
        persistConversationCache(chatId, 'server')
        const todosPayload = await fetchTodosForThread(chatId)
        ensureLatestAssistantTaskSnapshot(todosPayload?.taskSnapshot)
        persistConversationCache(chatId, 'server')
        await scrollToBottom({ force: true, markNewContent: false })
        return
      }

      const restored = await applyLocalCacheConversation(chatId, `服务端未返回会话 ${chatId}，已切换为本地缓存只读视图`)
      if (!restored) {
        currentMessages.value = []
        assignTodosState([])
        localCacheOnly.value = false
      }
    } catch (error) {
      console.error('加载对话消息失败:', error)
      const restored = await applyLocalCacheConversation(chatId, `加载服务端会话失败，已尝试恢复本地缓存：${String(error)}`)
      if (!restored) {
        currentMessages.value = []
        assignTodosState([])
        localCacheOnly.value = false
      }
    }
  }

  const mergeHistoryItems = (serverHistory: ChatHistoryItem[], { append = false } = {}) => {
    const merged = new Map<string, ChatHistoryItem>()
    if (append) {
      chatHistory.value.forEach(item => {
        if (!deletedChatIds.has(item.id)) merged.set(item.id, item)
      })
    }

    serverHistory.forEach(item => {
      if (deletedChatIds.has(item.id)) return
      const cachedConversation = getCachedServiceConversation(item.id)
      merged.set(item.id, {
        id: item.id,
        title: cachedConversation?.title || item.title || buildHistoryTitle(item.id, 'server'),
        source: 'server'
      })
    })

    const keyword = historySearchQuery.value.trim().toLowerCase()
    listCachedServiceHistory().forEach(conversation => {
      if (deletedChatIds.has(conversation.id)) return
      const title = buildCachedHistoryTitle(conversation)
      if (keyword && !conversation.id.toLowerCase().includes(keyword) && !title.toLowerCase().includes(keyword)) return
      if (!merged.has(conversation.id)) {
        merged.set(conversation.id, {
          id: conversation.id,
          title,
          source: 'local-cache'
        })
      }
    })

    chatHistory.value = Array.from(merged.values())
  }

  const loadChatHistory = async () => {
    historyLoading.value = true
    historyError.value = ''
    try {
      const page = await chatAPI.getChatHistoryPage('service', {
        limit: 30,
        keyword: historySearchQuery.value.trim()
      })
      if (page.error) {
        throw page.error
      }
      historyHasMore.value = Boolean(page.hasMore)
      historyNextCursor.value = page.nextCursor || null
      mergeHistoryItems(page.items || [])
      if (chatHistory.value.length > 0 && chatHistory.value[0]?.id) {
        await loadChat(chatHistory.value[0].id)
      } else {
        await startNewChat()
      }
    } catch (error) {
      console.error('加载聊天历史失败:', error)
      historyError.value = '历史记录加载失败，已显示本地缓存。'
      historyHasMore.value = false
      historyNextCursor.value = null

      const cachedHistory = listCachedServiceHistory().map(conversation => ({
        id: conversation.id,
        title: buildCachedHistoryTitle(conversation),
        source: 'local-cache' as const
      }))

      chatHistory.value = cachedHistory
      if (cachedHistory.length > 0) {
        const firstCachedHistory = cachedHistory[0]
        if (firstCachedHistory?.id) {
          await loadChat(firstCachedHistory.id)
        } else {
          await startNewChat()
        }
      } else {
        await startNewChat()
      }
    } finally {
      historyLoading.value = false
    }
  }

  const loadMoreChatHistory = async () => {
    if (historyLoading.value || !historyHasMore.value || !historyNextCursor.value) return
    historyLoading.value = true
    historyError.value = ''
    try {
      const page = await chatAPI.getChatHistoryPage('service', {
        limit: 30,
        cursor: historyNextCursor.value,
        keyword: historySearchQuery.value.trim()
      })
      if (page.error) {
        throw page.error
      }
      historyHasMore.value = Boolean(page.hasMore)
      historyNextCursor.value = page.nextCursor || null
      mergeHistoryItems(page.items || [], { append: true })
    } catch (error) {
      console.error('加载更多历史失败:', error)
      historyError.value = '加载更多历史失败，请稍后重试。'
    } finally {
      historyLoading.value = false
    }
  }

  const setHistorySearchQuery = async (keyword: string) => {
    historySearchQuery.value = keyword.slice(0, 80)
    await loadChatHistory()
  }

  const resolveUserIdentity = () => {
    const rawRole = userIdentityStore.userRole
    const rawUserId = userIdentityStore.userId
    const roleText = typeof rawRole === 'string' ? rawRole.trim() : ''
    const normalized = roleText.toLowerCase()

    const visitorKeywords = ['guest', 'visitor', '访客', '游客']
    if (normalized && visitorKeywords.some(keyword => normalized.includes(keyword))) {
      return '游客'
    }

    if (!roleText) {
      if (typeof rawUserId === 'string' && rawUserId.toLowerCase() === 'guest') {
        return '游客'
      }
      return '游客'
    }

    return '管理员'
  }

  const resolveUserSpeakerName = () => {
    const candidates = [
      userIdentityStore.speakerName,
      userIdentityStore.rawDisplayName,
      userIdentityStore.userRole,
      userIdentityStore.userId
    ]
    for (const candidate of candidates) {
      const value = typeof candidate === 'string' ? candidate.trim() : ''
      if (!value || value === '等待身份识别') continue
      return value.replace(/身份识别已完成$/, '')
    }
    return '访客'
  }

  const buildUserMessageMetadata = () => ({
    speakerName: resolveUserSpeakerName(),
    userId: userIdentityStore.userId || null,
    userRole: userIdentityStore.userRole || null
  })

  const buildAssistantMessageMetadata = () => ({
    speakerName: ASSISTANT_SPEAKER_NAME
  })

  const renameChatHistoryItem = async (chatId: string, title: string) => {
    const normalizedTitle = title.trim().slice(0, 60)
    if (!chatId || !normalizedTitle) return
    if (deletedChatIds.has(chatId)) return

    updateServiceConversationTitle(chatId, normalizedTitle)
    chatHistory.value = chatHistory.value.map(item =>
      item.id === chatId ? { ...item, title: normalizedTitle } : item
    )
  }

  const deleteChatHistoryItem = async (chatId: string) => {
    if (!chatId) return
    const cachedConversation = getCachedServiceConversation(chatId)
    deletedChatIds.add(chatId)

    const deletingCurrent = currentChatId.value === chatId
    if (deletingCurrent) {
      activeRequestVersion += 1
      closeActiveStream('user_stop')
    }

    let serverDeleted = false
    if (isSignedThreadId(chatId)) {
      try {
        const response = await chatAPI.deleteChatHistory(chatId, 'service')
        serverDeleted = Boolean(response?.server_deleted ?? response?.deleted)
      } catch (error: any) {
        if (Number(error?.status) !== 404 || !cachedConversation) {
          deletedChatIds.delete(chatId)
          throw error
        }
        console.warn(`服务端拒绝删除会话 ${chatId}，已按本地缓存残留清理`)
      }
    }

    removeServiceConversationCache(chatId)

    const nextHistory = chatHistory.value.filter(item => item.id !== chatId)
    chatHistory.value = nextHistory

    if (!deletingCurrent) return

    currentChatId.value = null
    currentMessages.value = []
    assignTodosState([])
    localCacheOnly.value = false
    isStreaming.value = false
    isStopping.value = false
    activeStream.value = null

    const nextChat = nextHistory[0]
    if (nextChat?.id) {
      await loadChat(nextChat.id)
    } else {
      await startNewChat()
    }

    if (!serverDeleted && isSignedThreadId(chatId)) {
      console.warn(`服务端会话 ${chatId} 未确认删除，但本地状态已清理`)
    }
  }

  const buildToolEvent = (toolData: any): ToolEvent => {
    const previewSource = toolData.result_preview ?? toolData.result
    const summary = summarizeToolEventPayload(previewSource)
    const inputSummary = summarizeToolEventPayload(toolData.input)
    const evidenceSummary = toolData.evidence_count ? `证据 ${toolData.evidence_count} 条` : ''
    const stageLabelMap: Record<string, string> = {
      collect: '收集',
      retrieve: '检索',
      analyze: '分析',
      report: '报告'
    }
    const stageLabel = toolData.stage ? (stageLabelMap[toolData.stage] || toolData.stage) : ''

    if (toolData.type === 'tool_start') {
      return {
        key: createToolEventKey(toolData.tool, toolData.type),
        type: toolData.type,
        tool: toolData.tool,
        label: `开始执行 ${toolData.tool}`,
        summary: [stageLabel ? `阶段: ${stageLabel}` : '', inputSummary].filter(Boolean).join(' | '),
        actionGuard: toolData.action_guard || null,
        runId: toolData.run_id,
        stage: toolData.stage,
        currentStage: toolData.current_stage
      }
    }

    return {
      key: createToolEventKey(toolData.tool, toolData.type),
      type: toolData.type,
      tool: toolData.tool,
      label: toolData.truncated
        ? `完成 ${toolData.tool}（结果已截断）`
        : `完成 ${toolData.tool}`,
      summary: [stageLabel ? `阶段: ${stageLabel}` : '', summary, evidenceSummary].filter(Boolean).join(' | '),
      actionGuard: toolData.action_guard || null,
      truncated: Boolean(toolData.truncated),
      runId: toolData.run_id,
      stage: toolData.stage,
      currentStage: toolData.current_stage
    }
  }

  const formatErrorMessage = (error: { message?: string; error_id?: string | null } | null | undefined) => {
    const message = error?.message || '请求处理失败，请稍后重试'
    return error?.error_id ? `${message}（错误编号：${error.error_id}）` : message
  }

  let voiceAccumulatedText = ''

  const syncExternalThread = (threadId?: string | null) => {
    if (!threadId) return
    ensureChatHistoryItem(threadId, 'server')
    localCacheOnly.value = false
  }

  const getVoiceAssistantIndex = () => {
    const lastIndex = getLastAssistantIndex()
    if (lastIndex < 0) return -1
    return currentMessages.value[lastIndex]?.voiceSessionActive ? lastIndex : -1
  }

  const patchVoiceAssistantMessage = (patch: Record<string, any>) => {
    const lastIndex = getVoiceAssistantIndex()
    if (lastIndex < 0) return
    currentMessages.value.splice(lastIndex, 1, {
      ...currentMessages.value[lastIndex],
      ...patch
    })
  }

  const beginVoiceAssistantMessage = async (
    threadId?: string | null,
    statusText = '语音回复处理中...'
  ) => {
    syncExternalThread(threadId)
    voiceAccumulatedText = ''
    currentMessages.value.push({
      role: 'assistant',
      ...buildAssistantMessageMetadata(),
      content: '',
      timestamp: new Date().toISOString(),
      isMarkdown: true,
      hasChart: false,
      chartData: null,
      imageUrl: null,
      isStreaming: true,
      streamState: 'reasoning',
      statusText,
      toolEvents: [],
      taskSnapshot: null,
      voiceSessionActive: true
    })
    await scrollToBottom({ force: true, markNewContent: false })
  }

  const ensureVoiceAssistantMessage = async (
    threadId?: string | null,
    statusText = '语音回复处理中...'
  ) => {
    if (getVoiceAssistantIndex() >= 0) {
      syncExternalThread(threadId)
      return
    }
    await beginVoiceAssistantMessage(threadId, statusText)
  }

  const appendVoiceUserMessage = async (content: string, threadId?: string | null) => {
    const messageContent = content.trim()
    if (!messageContent) return
    syncExternalThread(threadId)
    currentMessages.value.push({
      role: 'user',
      ...buildUserMessageMetadata(),
      content: messageContent,
      timestamp: new Date().toISOString(),
      voiceSessionActive: true
    })
    await scrollToBottom({ force: true, markNewContent: false })
  }

  const discardActiveVoiceTurn = async () => {
    const assistantIndex = getVoiceAssistantIndex()
    if (assistantIndex >= 0) {
      currentMessages.value.splice(assistantIndex, 1)
    }

    const lastIndex = currentMessages.value.length - 1
    const lastMessage = currentMessages.value[lastIndex]
    if (lastMessage?.role === 'user' && lastMessage?.voiceSessionActive) {
      currentMessages.value.splice(lastIndex, 1)
    }

    voiceAccumulatedText = ''
    await scrollToBottom({ force: true, markNewContent: false })
  }

  const updateVoiceAssistantStatus = async (
    streamState: string,
    statusText: string,
    threadId?: string | null
  ) => {
    await ensureVoiceAssistantMessage(threadId, statusText)
    patchVoiceAssistantMessage({
      streamState,
      statusText,
      isStreaming: ['connecting', 'reasoning', 'streaming', 'tool_running'].includes(streamState)
    })
    await scrollToBottom()
  }

  const appendVoiceAssistantToken = async (content: string, threadId?: string | null) => {
    if (!content) return
    await ensureVoiceAssistantMessage(threadId, '正在回复...')
    voiceAccumulatedText += content
    patchVoiceAssistantMessage({
      content: voiceAccumulatedText,
      isStreaming: true,
      streamState: 'streaming',
      statusText: '正在回复...'
    })
    persistConversationCache(threadId || (typeof currentChatId.value === 'string' ? currentChatId.value : null), 'server')
    await scrollToBottom()
  }

  const completeVoiceAssistantMessage = async (payload: Record<string, any> = {}) => {
    const threadId = typeof payload.thread_id === 'string' ? payload.thread_id : null
    syncExternalThread(threadId)
    await ensureVoiceAssistantMessage(threadId, '回复完成')

    const finalContent = String(
      payload.content || payload.final_content || payload.text || voiceAccumulatedText || '语音回复已播放。'
    )
    const patch: Record<string, any> = {
      content: finalContent,
      thread_id: threadId,
      threadId,
      isStreaming: false,
      streamState: 'completed',
      statusText: '回复完成',
      voiceSessionActive: false
    }

    patch.evidences = payload.evidences || []
    patch.normalizedEvidences = payload.normalizedEvidences || payload.normalized_evidences || []
    patch.traceId = payload.traceId || payload.trace_id || null
    patch.trace_id = payload.trace_id || payload.traceId || null
    patch.requestId = payload.requestId || payload.request_id || null
    patch.request_id = payload.request_id || payload.requestId || null
    patch.findings = payload.findings || []
    patch.findingLinks = payload.findingLinks || payload.finding_links || []
    patch.workflowResult = payload.workflowResult || payload.workflow_result || null
    patch.scenarioResult = payload.scenarioResult || payload.scenario_result || null
    patch.artifacts = payload.artifacts || []
    patch.timeline = payload.timeline || []
    patch.governance = payload.governance || null
    patch.reportGate = payload.reportGate || payload.report_gate || 'pass'
    patch.reportFilename = payload.reportFilename || payload.report_filename || null
    patch.reportUrl = payload.reportUrl || payload.report_url || null
    patch.reportArtifact = payload.reportArtifact || payload.report_artifact || null
    patch.sqlArtifact = payload.sqlArtifact || payload.sql_artifact || null
    patch.knowledgeArtifact = payload.knowledgeArtifact || payload.knowledge_artifact || null
    patch.analysisArtifact = payload.analysisArtifact || payload.analysis_artifact || null
    patch.workorderDecision = payload.workorderDecision || payload.workorder_decision || null
    patch.workorder_decision = payload.workorder_decision || payload.workorderDecision || null
    patch.artifact = payload.artifact || null
    patch.workflowStages = payload.workflowStages || payload.workflow_stages || []
    patch.currentWorkflowStage = payload.currentWorkflowStage || payload.current_stage || null
    patch.workflowStageDetails = payload.workflowStageDetails || payload.workflow_stage_details || []
    patch.toolLifecycleLedger = payload.toolLifecycleLedger || payload.tool_lifecycle_ledger || []
    patch.evidenceQuality = payload.evidenceQuality || payload.evidence_quality || null
    patch.evidenceCoverage = payload.evidenceCoverage || payload.evidence_coverage || null
    patch.qualityGateNotice = payload.qualityGateNotice || payload.quality_gate_notice || null
    patch.releaseReady = payload.releaseReady ?? payload.release_ready ?? null

    if (Array.isArray(payload.todos) && payload.todos.length) {
      patch.taskSnapshot = assignTodosState(payload.todos)
    }

    patchVoiceAssistantMessage(patch)
    const finalTaskSnapshot = currentMessages.value[getLastAssistantIndex()]?.taskSnapshot

    if (payload.chartData) {
      currentMessages.value.push({
        role: 'assistant',
        ...buildAssistantMessageMetadata(),
        content: '',
        chartData: payload.chartData,
        timestamp: new Date().toISOString(),
        isMarkdown: true
      })
    }

    if (payload.imageUrl) {
      currentMessages.value.push({
        role: 'assistant',
        ...buildAssistantMessageMetadata(),
        content: '',
        imageUrl: payload.imageUrl,
        timestamp: new Date().toISOString(),
        isMarkdown: true
      })
    }

    const cacheExtra: Record<string, any> = {}
    if (hasVisibleTaskSnapshot(finalTaskSnapshot)) {
      cacheExtra.todos = finalTaskSnapshot.todos
      cacheExtra.summary = finalTaskSnapshot.summary
    }
    persistConversationCache(threadId || (typeof currentChatId.value === 'string' ? currentChatId.value : null), 'server', cacheExtra)
    await scrollToBottom()
  }

  const finishVoicePlayback = async (threadId?: string | null) => {
    const lastIndex = getVoiceAssistantIndex()
    if (lastIndex < 0) return

    const existingContent = String(currentMessages.value[lastIndex]?.content || '').trim()
    if (!existingContent) {
      await completeVoiceAssistantMessage({ thread_id: threadId, content: '语音回复已播放。' })
      return
    }

    patchVoiceAssistantMessage({
      isStreaming: false,
      streamState: 'completed',
      statusText: '语音播放完成'
    })
    persistConversationCache(threadId || (typeof currentChatId.value === 'string' ? currentChatId.value : null), 'server')
    await scrollToBottom()
  }

  const applyVoiceVisualActions = async (actions: any[] = [], threadId?: string | null) => {
    if (!Array.isArray(actions) || !actions.length) return
    await ensureVoiceAssistantMessage(threadId, '正在处理可视化结果...')

    actions.forEach(action => {
      if (action?.type === 'todos_update') {
        const taskSnapshot = assignTodosState(action.todos || [], action.summary)
        updateAssistantTaskSnapshot(taskSnapshot)
        return
      }

      const normalizedToolEvent = action?.type === 'tool_start' || action?.type === 'tool_end'
        ? action
        : {
            type: 'tool_end',
            tool: action?.tool || action?.type || 'visual_action',
            result_preview: action?.result_preview ?? action?.result ?? action,
            truncated: Boolean(action?.truncated)
          }
      appendToolEvent(buildToolEvent(normalizedToolEvent))
    })

    persistConversationCache(threadId || (typeof currentChatId.value === 'string' ? currentChatId.value : null), 'server')
    await scrollToBottom()
  }

  const sendMessage = async (content?: string, options: Record<string, any> = {}) => {
    if (isSubmittingMessage || isStreaming.value || (!content && !userInput.value.trim())) return

    isSubmittingMessage = true
    let requestedThreadId: string | null = null

    try {
      onSendMessage?.()

      const messageContent = content || userInput.value.trim()
      const optionThreadId = options.threadId
      requestedThreadId = typeof optionThreadId === 'string'
        ? optionThreadId
        : typeof currentChatId.value === 'string'
          ? currentChatId.value
          : null
      const appendUserMessage = options.appendUserMessage !== false
      const editUserTurnIndex = Number.isInteger(options?.edit?.userTurnIndex)
        ? Number(options.edit.userTurnIndex)
        : null
      const reboundFromLocalCache = Boolean(requestedThreadId && localCacheOnly.value && !isSignedThreadId(requestedThreadId))

    if (appendUserMessage) {
      const userMessage = {
        role: 'user',
        ...buildUserMessageMetadata(),
        content: messageContent,
        timestamp: new Date().toISOString()
      }
      currentMessages.value.push(userMessage)
    }

    if (appendUserMessage && reboundFromLocalCache) {
      currentMessages.value.push({
        role: 'assistant',
        ...buildAssistantMessageMetadata(),
        content: '⚠️ 以下回复将从新的受控会话开始。旧本地缓存内容不会自动注入服务端上下文。',
        timestamp: new Date().toISOString(),
        isMarkdown: true,
        cacheHint: true
      })
    }

    if (!content) {
      userInput.value = ''
      adjustTextareaHeight()
    }
    await scrollToBottom({ force: true, markNewContent: false })

    currentMessages.value.push({
      role: 'assistant',
      ...buildAssistantMessageMetadata(),
      content: '',
      timestamp: new Date().toISOString(),
      isMarkdown: true,
      hasChart: false,
      chartData: null,
      imageUrl: null,
      isStreaming: true,
      streamState: 'connecting',
      statusText: '正在建立流式连接...',
      toolEvents: [],
      taskSnapshot: null,
      workflowStages: [],
      currentWorkflowStage: null,
      workflowStageDetails: [],
      toolLifecycleLedger: []
    })
    isStreaming.value = true
    isStopping.value = false
    userIdentityStore.setStatus?.('connecting')

    let accumulatedText = ''
    let hasVisibleToken = false
    let activeToolCount = 0
    activeRequestVersion += 1
    const requestVersion = activeRequestVersion
    const isCurrentRequest = () => !isDisposed.value && requestVersion === activeRequestVersion

    const userIdentity = resolveUserIdentity()

      const streamCallbacks = {
          onStart: async (startData: any) => {
            if (!isCurrentRequest()) return

            const nextThreadId = startData.thread_id
            if (requestedThreadId && nextThreadId && requestedThreadId !== nextThreadId) {
              renameServiceConversationCache(requestedThreadId, nextThreadId)
              removeChatHistoryItem(requestedThreadId)
            }

            ensureChatHistoryItem(nextThreadId, 'server')
            localCacheOnly.value = false
            updateAssistantState(
              startData.stage === 'reasoning' ? 'reasoning' : 'streaming',
              startData.message || '模型已开始推理，等待首个可显示 token...'
            )
            isStopping.value = false
            userIdentityStore.setStatus?.('connected')
            persistConversationCache(nextThreadId, 'server')
            await scrollToBottom()
          },
          onPing: async (pingData: any) => {
            if (!isCurrentRequest()) return
            if (hasVisibleToken) return
            if (activeToolCount > 0) {
              updateAssistantState('tool_running', `工具执行中，模型尚未输出可显示内容...`)
            } else {
              updateAssistantState(
                pingData?.stage === 'reasoning' ? 'reasoning' : 'connecting',
                pingData?.message || '模型仍在推理，尚未产出可显示内容...'
              )
            }
            await scrollToBottom()
          },
          onToken: async (contentData: any) => {
            if (!isCurrentRequest()) return
            hasVisibleToken = true
            accumulatedText += contentData.content
            updateAssistantContent(accumulatedText)
            updateAssistantState('streaming', '正在回复...')
            onAssistantToken?.(contentData.content)
            await scrollToBottom()
          },
          onToolCall: async (toolData: any) => {
            if (!isCurrentRequest()) return
            appendToolEvent(buildToolEvent(toolData))
            if (toolData.stage) {
              mergeWorkflowStages([toolData.stage])
            }
            updateWorkflowProgress({
              currentWorkflowStage: toolData.current_stage || toolData.stage || null
            })
            if (toolData.stage) {
              mergeWorkflowStageDetail({
                stage: toolData.stage,
                status: toolData.type === 'tool_end' ? 'active' : 'active',
                tool_count: toolData.type === 'tool_end' ? 1 : 0,
                duration_ms: toolData.stage_duration_ms ?? null
              })
            }
            if (toolData.type === 'tool_start') {
              activeToolCount += 1
              updateAssistantState('tool_running', PUBLIC_THINKING_STATUS)
              if (toolData.tool === 'write_todos') {
                updateAssistantTaskSnapshot(
                  createTaskSnapshot([], null, {
                    threadId: typeof currentChatId.value === 'string' ? currentChatId.value : requestedThreadId,
                    isLoading: true,
                    statusHint: '规划中'
                  })
                )
              }
            } else if (toolData.type === 'tool_end') {
              activeToolCount = Math.max(0, activeToolCount - 1)
              updateAssistantState(
                hasVisibleToken ? 'streaming' : 'reasoning',
                PUBLIC_THINKING_STATUS
              )

              if (toolData.tool === 'write_todos') {
                const parsedTodos = extractTodosFromToolResult(toolData.result ?? toolData.result_preview)
                if (parsedTodos && parsedTodos.length) {
                  const taskSnapshot = assignTodosState(parsedTodos)
                  updateAssistantTaskSnapshot(taskSnapshot)
                } else {
                  const payload = await fetchTodosForThread(typeof currentChatId.value === 'string' ? currentChatId.value : null)
                  updateAssistantTaskSnapshot(payload?.taskSnapshot)
                }
              }
            }
            persistConversationCache(
              typeof currentChatId.value === 'string' ? currentChatId.value : requestedThreadId,
              localCacheOnly.value ? 'local-cache' : 'server'
            )
            await scrollToBottom()
          },
          onTaskUpdate: async (taskData: any) => {
            if (!isCurrentRequest()) return
            const taskSnapshot = {
              ...assignTodosState(taskData.todos || [], taskData.summary || null),
              statusHint: taskData.status_hint || '',
              lifecycleState: '',
              isLoading: false
            }
            updateAssistantTaskSnapshot(taskSnapshot)
            updateWorkflowProgress({
              currentWorkflowStage: taskData.current_stage || null
            })
            persistConversationCache(
              typeof currentChatId.value === 'string' ? currentChatId.value : requestedThreadId,
              localCacheOnly.value ? 'local-cache' : 'server',
              {
                todos: taskSnapshot.todos,
                summary: taskSnapshot.summary
              }
            )
            await scrollToBottom()
          },
          onComplete: async (completeData: any) => {
            if (!isCurrentRequest()) return
            const reportGate = completeData.reportGate || completeData.report_gate || 'pass'
            const gateStatusTextMap: Record<string, string> = {
              pass: '回复完成',
              review_required: '回复完成，需人工复核',
              blocked: '回复完成，当前结论待确认'
            }
            ensureChatHistoryItem(completeData.thread_id, 'server')
            const patch: Record<string, any> = {
              content: completeData.grounded_final_content || completeData.content,
              thread_id: completeData.thread_id,
              threadId: completeData.thread_id,
              rawFinalContent: completeData.rawFinalContent || completeData.raw_final_content || completeData.content,
              isStreaming: false,
              streamState: 'completed',
              statusText: '回复完成'
            }

            patch.statusText = gateStatusTextMap[reportGate] || '回复完成'
            patch.evidences = completeData.evidences || []
            patch.normalizedEvidences = completeData.normalizedEvidences || completeData.normalized_evidences || []
            patch.traceId = completeData.traceId || completeData.trace_id || null
            patch.trace_id = completeData.trace_id || completeData.traceId || null
            patch.requestId = completeData.requestId || completeData.request_id || null
            patch.request_id = completeData.request_id || completeData.requestId || null
            patch.findings = completeData.findings || []
            patch.findingLinks = completeData.finding_links || []
            patch.workflowResult = completeData.workflowResult || completeData.workflow_result || null
            patch.workflowEnvelope = completeData.workflowEnvelope || completeData.workflow_envelope || null
            patch.scenarioResult = completeData.scenarioResult || completeData.scenario_result || null
            patch.artifacts = completeData.artifacts || []
            patch.timeline = completeData.timeline || []
            patch.governance = completeData.governance || null
            patch.evidenceQuality = completeData.evidenceQuality || completeData.evidence_quality || null
            patch.evidenceCoverage = completeData.evidenceCoverage || completeData.evidence_coverage || null
            patch.reportGate = reportGate
            patch.reportFilename = completeData.reportFilename || completeData.report_filename || null
            patch.reportUrl = completeData.reportUrl || completeData.report_url || null
            patch.reportArtifact = completeData.reportArtifact || completeData.report_artifact || null
            patch.sqlArtifact = completeData.sqlArtifact || completeData.sql_artifact || null
            patch.knowledgeArtifact = completeData.knowledgeArtifact || completeData.knowledge_artifact || null
            patch.analysisArtifact = completeData.analysisArtifact || completeData.analysis_artifact || null
            patch.workorderDecision = completeData.workorderDecision || completeData.workorder_decision || null
            patch.workorder_decision = completeData.workorder_decision || completeData.workorderDecision || null
            patch.artifact = completeData.artifact || null
            patch.qualityGateNotice = completeData.qualityGateNotice || completeData.quality_gate_notice || null
            patch.releaseReady = completeData.releaseReady ?? completeData.release_ready ?? null
            patch.workflowStages = completeData.workflow_stages || []
            patch.currentWorkflowStage = completeData.current_stage || null
            patch.workflowStageDetails = completeData.workflow_stage_details || []
            patch.toolLifecycleLedger = completeData.toolLifecycleLedger || completeData.tool_lifecycle_ledger || []

            if (completeData.todos?.length) {
              const taskSnapshot = assignTodosState(completeData.todos)
              patch.taskSnapshot = taskSnapshot
            } else {
              const existingTaskSnapshot = currentMessages.value[getLastAssistantIndex()]?.taskSnapshot
              const completedTaskSnapshot = completeTaskSnapshot(
                existingTaskSnapshot,
                reportGate === 'blocked' ? '本轮回答已结束' : '本轮回答已完成'
              )
              if (completedTaskSnapshot) {
                patch.taskSnapshot = completedTaskSnapshot
              }
            }

            patchLastAssistantMessage(patch)
            onAssistantComplete?.()

            isStreaming.value = false
            isStopping.value = false
            activeStream.value = null
            userIdentityStore.setStatus?.('connected')
            await scrollToBottom()

            if (completeData.chartData) {
              currentMessages.value.push({
                role: 'assistant',
                ...buildAssistantMessageMetadata(),
                content: '',
                chartData: completeData.chartData,
                timestamp: new Date().toISOString(),
                isMarkdown: true
              })
            }

            if (completeData.imageUrl) {
              currentMessages.value.push({
                role: 'assistant',
                ...buildAssistantMessageMetadata(),
                content: '',
                imageUrl: completeData.imageUrl,
                timestamp: new Date().toISOString(),
                isMarkdown: true
              })
            }

            const finalTaskSnapshot = patch.taskSnapshot || currentMessages.value[getLastAssistantIndex()]?.taskSnapshot
            const cacheExtra: Record<string, any> = {}
            if (hasVisibleTaskSnapshot(finalTaskSnapshot)) {
              cacheExtra.todos = finalTaskSnapshot.todos
              cacheExtra.summary = finalTaskSnapshot.summary
            }
            persistConversationCache(completeData.thread_id, 'server', cacheExtra)
          },
          onError: async (error: any) => {
            if (!isCurrentRequest()) return
            const message = formatErrorMessage(error)
            const contentWithError = accumulatedText
              ? `${accumulatedText}\n\n⚠️ ${message}`
              : `请求处理失败：${message}`

            patchLastAssistantMessage({
              content: contentWithError,
              isStreaming: false,
              streamState: 'failed',
              statusText: '回复失败',
              taskSnapshot: buildInterruptedTaskSnapshot('回复失败，执行已停止')
            })

            isStreaming.value = false
            isStopping.value = false
            activeStream.value = null
            userIdentityStore.setStatus?.('error')
            persistConversationCache(
              typeof currentChatId.value === 'string' ? currentChatId.value : requestedThreadId,
              localCacheOnly.value ? 'local-cache' : 'server'
            )
            await scrollToBottom()
          },
          onInterrupted: async (data: any) => {
            if (!isCurrentRequest()) return
            const interruptedByUser = Boolean(data?.user_initiated || data?.reason === 'user_stop')
            const existingTaskSnapshot = currentMessages.value[getLastAssistantIndex()]?.taskSnapshot
            patchLastAssistantMessage({
              content: accumulatedText || (interruptedByUser ? '回答已停止，尚未生成可显示内容。' : '会话已中断，请手动重试。'),
              isStreaming: false,
              streamState: 'interrupted',
              statusText: data?.message || (interruptedByUser ? '已停止生成' : '会话已中断'),
              taskSnapshot: interruptTaskSnapshot(
                existingTaskSnapshot,
                interruptedByUser ? '已停止生成' : '执行已中断'
              ) || existingTaskSnapshot
            })

            isStreaming.value = false
            isStopping.value = false
            activeStream.value = null
            userIdentityStore.setStatus?.(interruptedByUser ? 'idle' : 'disconnected')
            persistConversationCache(
              typeof currentChatId.value === 'string' ? currentChatId.value : requestedThreadId,
              localCacheOnly.value ? 'local-cache' : 'server'
            )
          }
        }

      const stream = editUserTurnIndex !== null && requestedThreadId && isSignedThreadId(requestedThreadId)
        ? await chatAPI.sendEditedServiceMessageStream(
            messageContent,
            requestedThreadId,
            editUserTurnIndex,
            userIdentity,
            streamCallbacks
          )
        : await chatAPI.sendServiceMessageStream(
            messageContent,
            requestedThreadId,
            userIdentity,
            streamCallbacks
          )

      if (isCurrentRequest()) {
        activeStream.value = stream
      } else {
        stream.close('interrupted')
      }
    } catch (error: any) {
      console.error('发送消息失败:', error)
      const userFriendlyMessage = `发送失败：${error.message || '请求未发送成功'}`

      patchLastAssistantMessage({
        content: userFriendlyMessage,
        isStreaming: false,
        streamState: 'failed',
        statusText: '发送失败',
        taskSnapshot: buildInterruptedTaskSnapshot('发送失败，执行已停止')
      })

      isStreaming.value = false
      isStopping.value = false
      activeStream.value = null
      userIdentityStore.setStatus?.('error')
      persistConversationCache(
        typeof currentChatId.value === 'string' ? currentChatId.value : requestedThreadId,
        localCacheOnly.value ? 'local-cache' : 'server'
      )
      await scrollToBottom()
      if (options.throwOnError) {
        throw error
      }
    } finally {
      isSubmittingMessage = false
    }
  }

  const getUserTurnIndexAtMessageIndex = (messageIndex: number) => {
    let userTurnIndex = -1
    for (let index = 0; index <= messageIndex; index += 1) {
      if (currentMessages.value[index]?.role === 'user') {
        userTurnIndex += 1
      }
    }
    return userTurnIndex
  }

  const resolveLatestVisibleTaskSnapshot = (messages: Message[]) => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const snapshot = messages[index]?.taskSnapshot
      if (hasVisibleTaskSnapshot(snapshot)) {
        return snapshot
      }
    }
    return null
  }

  const editUserMessage = async (messageIndex: number, content: string) => {
    const targetMessage = currentMessages.value[messageIndex]
    const nextContent = String(content || '').trim()
    if (!targetMessage || targetMessage.role !== 'user') {
      throw new Error('只能编辑用户消息')
    }
    if (!nextContent) {
      throw new Error('消息内容不能为空')
    }

    const previousContent = String(targetMessage.content || '').trim()
    if (previousContent === nextContent) {
      return
    }

    const requestedThreadId = typeof currentChatId.value === 'string' ? currentChatId.value : null
    if (!requestedThreadId || !isSignedThreadId(requestedThreadId)) {
      throw new Error('当前会话尚未同步到服务端，不能编辑后重新生成。请先发送一轮新消息后再编辑。')
    }
    const userTurnIndex = getUserTurnIndexAtMessageIndex(messageIndex)
    if (userTurnIndex < 0) {
      throw new Error('未找到可编辑的用户轮次')
    }
    const editedAt = new Date().toISOString()
    const previousMessages = [...currentMessages.value]
    const previousHistory = Array.isArray(targetMessage.editHistory)
      ? targetMessage.editHistory
      : []
    const editHistory = [
      ...previousHistory,
      {
        content: previousContent,
        editedAt,
        timestamp: targetMessage.timestamp || null
      }
    ]

    activeRequestVersion += 1
    closeActiveStream('interrupted')
    isStreaming.value = false
    isStopping.value = false
    activeStream.value = null

    const editedMessage = {
      ...targetMessage,
      content: nextContent,
      timestamp: targetMessage.timestamp || new Date().toISOString(),
      editedAt,
      isEdited: true,
      editHistory,
      editRevision: editHistory.length + 1
    }
    const keptMessages = [
      ...currentMessages.value.slice(0, messageIndex),
      editedMessage
    ]
    currentMessages.value = keptMessages

    const latestTaskSnapshot = resolveLatestVisibleTaskSnapshot(keptMessages)
    if (latestTaskSnapshot) {
      assignTodosState(latestTaskSnapshot.todos || [], latestTaskSnapshot.summary || null)
    } else {
      assignTodosState([])
    }

    persistConversationCache(
      requestedThreadId,
      localCacheOnly.value ? 'local-cache' : 'server'
    )
    await scrollToBottom({ force: true, markNewContent: false })

    try {
      await sendMessage(nextContent, {
        appendUserMessage: false,
        threadId: requestedThreadId,
        throwOnError: true,
        edit: {
          userTurnIndex
        }
      })
    } catch (error: any) {
      currentMessages.value = previousMessages
      const previousTaskSnapshot = resolveLatestVisibleTaskSnapshot(previousMessages)
      if (previousTaskSnapshot) {
        assignTodosState(previousTaskSnapshot.todos || [], previousTaskSnapshot.summary || null)
      } else {
        assignTodosState([])
      }
      persistConversationCache(requestedThreadId, localCacheOnly.value ? 'local-cache' : 'server')
      await scrollToBottom({ force: true, markNewContent: false })
      throw new Error(error?.message || '保存并重新生成失败，请稍后重试')
    }
  }

  const disposeStream = () => {
    isDisposed.value = true
    activeRequestVersion += 1
    closeActiveStream('interrupted')
    isStreaming.value = false
    isStopping.value = false
  }

  const reviveStreamLifecycle = () => {
    isDisposed.value = false
  }

  return {
    currentMessages,
    isStreaming,
    isStopping,
    sendMessage,
    editUserMessage,
    stopStreaming,
    loadChat,
    loadChatHistory,
    loadMoreChatHistory,
    setHistorySearchQuery,
    historyLoading,
    historyError,
    historyHasMore,
    historySearchQuery,
    renameChatHistoryItem,
    deleteChatHistoryItem,
    startNewChat,
    disposeStream,
    reviveStreamLifecycle,
    appendVoiceUserMessage,
    beginVoiceAssistantMessage,
    updateVoiceAssistantStatus,
    appendVoiceAssistantToken,
    completeVoiceAssistantMessage,
    finishVoicePlayback,
    discardActiveVoiceTurn,
    applyVoiceVisualActions
  }
}
