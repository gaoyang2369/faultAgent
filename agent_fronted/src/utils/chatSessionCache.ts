import { normalizeChatMessage } from './chatMessageModel.js'

const STORAGE_KEY = 'fd_service_chat_cache_v1'
const MAX_CONVERSATIONS = 20
const MAX_MESSAGES_PER_CONVERSATION = 80

export type CachedConversationSummary = {
  total: number
  pending: number
  in_progress: number
  completed: number
}

export type CachedConversation = {
  id: string
  title?: string
  updatedAt: string
  source: 'server' | 'local-cache'
  messages: Array<Record<string, any>>
  todos?: any[]
  summary?: CachedConversationSummary
}

const isBrowser = () => typeof window !== 'undefined' && typeof window.localStorage !== 'undefined'

const createEmptyCache = (): Record<string, CachedConversation> => ({})

const cloneJson = <T>(value: T): T => JSON.parse(JSON.stringify(value))

const buildDefaultTitle = (chatId: string) => `咨询 ${chatId.slice(-6)}`

const normalizeMessages = (messages: Array<Record<string, any>> = []) =>
  messages.slice(-MAX_MESSAGES_PER_CONVERSATION).map(message => normalizeChatMessage(cloneJson(message)))

const normalizeTodos = (todos?: any[]) => {
  if (!Array.isArray(todos)) return []
  return cloneJson(todos)
}

const normalizeSummary = (summary?: CachedConversationSummary) => {
  if (!summary) return undefined
  return {
    total: Number(summary.total || 0),
    pending: Number(summary.pending || 0),
    in_progress: Number(summary.in_progress || 0),
    completed: Number(summary.completed || 0)
  }
}

const readCache = (): Record<string, CachedConversation> => {
  if (!isBrowser()) return createEmptyCache()

  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return createEmptyCache()
    const parsed = JSON.parse(raw) as Record<string, any>
    if (!parsed || typeof parsed !== 'object') {
      return createEmptyCache()
    }

    return Object.entries(parsed).reduce<Record<string, CachedConversation>>((acc, [chatId, value]) => {
      if (!value || typeof value !== 'object') return acc
      const rawValue = value as Record<string, any>
      acc[chatId] = {
        id: chatId,
        title: typeof rawValue.title === 'string' && rawValue.title.trim() ? rawValue.title : buildDefaultTitle(chatId),
        updatedAt: typeof rawValue.updatedAt === 'string' ? rawValue.updatedAt : new Date().toISOString(),
        source: rawValue.source === 'server' ? 'server' : 'local-cache',
        messages: normalizeMessages(Array.isArray(rawValue.messages) ? rawValue.messages : []),
        todos: normalizeTodos(rawValue.todos),
        summary: normalizeSummary(rawValue.summary)
      }
      return acc
    }, createEmptyCache())
  } catch (error) {
    console.warn('读取本地聊天缓存失败:', error)
    return createEmptyCache()
  }
}

const writeCache = (cache: Record<string, CachedConversation>) => {
  if (!isBrowser()) return

  const orderedEntries = Object.values(cache)
    .sort((left, right) => {
      const leftTime = new Date(left.updatedAt).getTime()
      const rightTime = new Date(right.updatedAt).getTime()
      return rightTime - leftTime
    })
    .slice(0, MAX_CONVERSATIONS)
    .map(item => [item.id, item])

  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(Object.fromEntries(orderedEntries)))
  } catch (error) {
    console.warn('写入本地聊天缓存失败:', error)
  }
}

export const isSignedThreadId = (chatId?: string | null) => Boolean(chatId && chatId.startsWith('thread.'))

export const listCachedServiceHistory = () =>
  Object.values(readCache())
    .sort((left, right) => new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime())

export const getCachedServiceConversation = (chatId?: string | null) => {
  if (!chatId) return null
  return readCache()[chatId] || null
}

export const updateServiceConversationTitle = (chatId?: string | null, title?: string | null) => {
  if (!chatId) return
  const normalizedTitle = typeof title === 'string' ? title.trim() : ''
  if (!normalizedTitle) return

  const cache = readCache()
  const previous = cache[chatId]
  cache[chatId] = {
    id: chatId,
    title: normalizedTitle,
    updatedAt: previous?.updatedAt || new Date().toISOString(),
    source: previous?.source || (isSignedThreadId(chatId) ? 'server' : 'local-cache'),
    messages: previous?.messages || [],
    todos: previous?.todos,
    summary: previous?.summary
  }
  writeCache(cache)
}

export const removeServiceConversationCache = (chatId?: string | null) => {
  if (!chatId) return
  const cache = readCache()
  if (!cache[chatId]) return
  delete cache[chatId]
  writeCache(cache)
}

export const upsertServiceConversationCache = (
  chatId: string,
  patch: Partial<Omit<CachedConversation, 'id'>> = {}
) => {
  if (!chatId) return

  const cache = readCache()
  const previous = cache[chatId]
  const hasMessages = Object.prototype.hasOwnProperty.call(patch, 'messages')
  const hasTodos = Object.prototype.hasOwnProperty.call(patch, 'todos')
  const hasSummary = Object.prototype.hasOwnProperty.call(patch, 'summary')
  const nextValue: CachedConversation = {
    id: chatId,
    title: typeof patch.title === 'string' && patch.title.trim()
      ? patch.title
      : previous?.title || buildDefaultTitle(chatId),
    updatedAt: patch.updatedAt || new Date().toISOString(),
    source: patch.source === 'server' ? 'server' : previous?.source || 'local-cache',
    messages: hasMessages ? normalizeMessages(patch.messages || []) : previous?.messages || [],
    todos: hasTodos ? normalizeTodos(patch.todos) : previous?.todos,
    summary: hasSummary ? normalizeSummary(patch.summary) : previous?.summary
  }

  cache[chatId] = nextValue
  writeCache(cache)
}

export const renameServiceConversationCache = (fromChatId?: string | null, toChatId?: string | null) => {
  if (!fromChatId || !toChatId || fromChatId === toChatId) return

  const cache = readCache()
  const previous = cache[fromChatId]
  if (!previous) return

  const nextTarget = cache[toChatId]
  cache[toChatId] = {
    ...previous,
    ...nextTarget,
    id: toChatId,
    title: nextTarget?.title || previous.title || buildDefaultTitle(toChatId),
    source: 'server',
    updatedAt: new Date().toISOString(),
    messages: nextTarget?.messages?.length ? nextTarget.messages : previous.messages,
    todos: nextTarget?.todos?.length ? nextTarget.todos : previous.todos,
    summary: nextTarget?.summary || previous.summary
  }
  delete cache[fromChatId]
  writeCache(cache)
}

export const buildCachedHistoryTitle = (conversation: CachedConversation) => {
  const baseTitle = conversation.title || buildDefaultTitle(conversation.id)
  if (conversation.source === 'local-cache') {
    return baseTitle.startsWith('本地缓存 · ') ? baseTitle : `本地缓存 · ${baseTitle}`
  }
  return baseTitle
}
