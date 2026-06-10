import { ref, computed, type Ref } from 'vue'
import { chatAPI } from '@/services/api'
import { upsertServiceConversationCache } from '@/utils/chatSessionCache'
import {
  buildTaskSummary,
  createEmptyTaskSummary,
  createTaskSnapshot,
  normalizeTaskSummary,
  normalizeTodos
} from '@/utils/taskState'

type TaskSummary = ReturnType<typeof createEmptyTaskSummary>

export const useTodosPanel = (currentChatId?: Ref<string | number | null>) => {
  const currentThreadTodos = ref<ReturnType<typeof normalizeTodos>>([])
  const todoSummary = ref<TaskSummary>(createEmptyTaskSummary())
  const isTodosLoading = ref(false)

  const hasTodos = computed(() => currentThreadTodos.value.length > 0)
  const pendingCount = computed(() => todoSummary.value.pending || 0)
  const inProgressCount = computed(() => todoSummary.value.in_progress || 0)
  const completedCount = computed(() => todoSummary.value.completed || 0)

  const assignTodosState = (todos: any[] = [], summary: Partial<TaskSummary> | null = null) => {
    const threadId = typeof currentChatId?.value === 'string' && currentChatId.value
      ? currentChatId.value
      : 'todo'
    const normalizedTodos = normalizeTodos(todos, threadId)
    const normalizedSummary = normalizeTaskSummary(summary, normalizedTodos)

    currentThreadTodos.value = normalizedTodos
    todoSummary.value = normalizedSummary

    if (typeof currentChatId?.value === 'string' && currentChatId.value) {
      upsertServiceConversationCache(currentChatId.value, {
        todos: normalizedTodos,
        summary: normalizedSummary
      })
    }

    return createTaskSnapshot(normalizedTodos, normalizedSummary, {
      threadId,
      isLoading: false
    })
  }

  const extractTodosSnippet = (text?: string | null) => {
    if (!text || typeof text !== 'string') return null
    const markers = ["'todos'", '"todos"']
    for (const marker of markers) {
      const markerIndex = text.indexOf(marker)
      if (markerIndex === -1) continue
      const start = text.indexOf('[', markerIndex)
      if (start === -1) continue
      let depth = 0
      for (let i = start; i < text.length; i++) {
        const char = text[i]
        if (char === '[') depth += 1
        else if (char === ']') {
          depth -= 1
          if (depth === 0) {
            return text.slice(start, i + 1)
          }
        }
      }
    }
    return null
  }

  const parseTodosSnippet = (snippet: string | null) => {
    if (!snippet) return null
    try {
      return JSON.parse(snippet)
    } catch (error) {
      // ignore
    }
    try {
      return JSON.parse(snippet.replace(/'/g, '"'))
    } catch (error) {
      console.warn('解析 todos 片段失败:', error)
    }
    return null
  }

  const extractTodosFromToolResult = (result: any) => {
    if (!result) return null
    if (Array.isArray(result)) return result
    if (typeof result === 'object') {
      if (Array.isArray((result as any).todos)) return (result as any).todos
      if ((result as any).result) {
        return extractTodosFromToolResult((result as any).result)
      }
    }
    if (typeof result === 'string') {
      const snippet = extractTodosSnippet(result)
      if (snippet) {
        const parsed = parseTodosSnippet(snippet)
        if (Array.isArray(parsed)) return parsed
      }
    }
    return null
  }

  const fetchTodosForThread = async (threadId?: string | null) => {
    if (!threadId) {
      const taskSnapshot = assignTodosState([])
      return {
        thread_id: '',
        todos: [],
        summary: buildTaskSummary([]),
        taskSnapshot
      }
    }

    isTodosLoading.value = true
    try {
      const data = await chatAPI.getThreadTodos(threadId)
      const taskSnapshot = assignTodosState(data.todos || [], data.summary)
      return {
        ...data,
        taskSnapshot
      }
    } catch (error) {
      console.error('加载任务清单失败:', error)
      const taskSnapshot = assignTodosState([])
      return {
        thread_id: threadId,
        todos: [],
        summary: createEmptyTaskSummary(),
        taskSnapshot
      }
    } finally {
      isTodosLoading.value = false
    }
  }

  return {
    currentThreadTodos,
    todoSummary,
    isTodosLoading,
    hasTodos,
    pendingCount,
    inProgressCount,
    completedCount,
    assignTodosState,
    fetchTodosForThread,
    extractTodosFromToolResult
  }
}
