export type NormalizedTodoStatus = 'pending' | 'in_progress' | 'completed' | 'interrupted'

export type NormalizedTodo = {
  id: string
  title: string
  description: string
  status: NormalizedTodoStatus
}

export type TaskSummary = {
  total: number
  pending: number
  in_progress: number
  completed: number
  interrupted: number
}

export type DecoratedTodo = NormalizedTodo & {
  displayStatus: NormalizedTodoStatus
  statusText: string
  statusIcon: string
}

export type TaskSnapshot = {
  todos: NormalizedTodo[]
  summary: TaskSummary
  isLoading?: boolean
  statusHint?: string
  lifecycleState?: 'active' | 'interrupted' | ''
  updatedAt?: string
}

const STATUS_META: Record<NormalizedTodoStatus, { label: string; icon: string }> = {
  pending: { label: '待处理', icon: '•' },
  in_progress: { label: '进行中', icon: '◐' },
  completed: { label: '已完成', icon: '✔' },
  interrupted: { label: '已停止', icon: '■' }
}

export const createEmptyTaskSummary = (): TaskSummary => ({
  total: 0,
  pending: 0,
  in_progress: 0,
  completed: 0,
  interrupted: 0
})

export const normalizeTodoStatus = (status?: string): NormalizedTodoStatus => {
  if (!status) return 'pending'
  const value = String(status).trim().toLowerCase()
  if (value === 'in-progress') return 'in_progress'
  if (value === 'done') return 'completed'
  if (value === 'completed') return 'completed'
  if (value === 'in_progress') return 'in_progress'
  if (value === 'interrupted') return 'interrupted'
  if (value === 'stopped') return 'interrupted'
  if (value === 'aborted') return 'interrupted'
  return value === 'pending' ? 'pending' : 'pending'
}

export const normalizeTodos = (todos: any[] = [], threadId = 'todo'): NormalizedTodo[] => {
  if (!Array.isArray(todos)) return []
  return todos.map((todo, index) => {
    const normalizedStatus = normalizeTodoStatus(todo?.status)
    const title = todo?.title || todo?.name || todo?.task || todo?.content || `任务 ${index + 1}`
    const description = todo?.description || todo?.detail || todo?.notes || ''
    return {
      id: todo?.id || `${threadId}-${index}`,
      title,
      description,
      status: normalizedStatus
    }
  })
}

export const buildTaskSummary = (todos: NormalizedTodo[] = []): TaskSummary => ({
  total: todos.length,
  pending: todos.filter(todo => todo.status === 'pending').length,
  in_progress: todos.filter(todo => todo.status === 'in_progress').length,
  completed: todos.filter(todo => todo.status === 'completed').length,
  interrupted: todos.filter(todo => todo.status === 'interrupted').length
})

export const normalizeTaskSummary = (summary?: Partial<TaskSummary> | null, todos: NormalizedTodo[] = []): TaskSummary => {
  if (!summary) {
    return buildTaskSummary(todos)
  }

  const normalized = {
    total: Number(summary.total || 0),
    pending: Number(summary.pending || 0),
    in_progress: Number(summary.in_progress || 0),
    completed: Number(summary.completed || 0),
    interrupted: Number(summary.interrupted || 0)
  }

  if (normalized.total > 0) {
    return normalized
  }

  return buildTaskSummary(todos)
}

export const decorateTodos = (todos: NormalizedTodo[] = []): DecoratedTodo[] =>
  todos.map(todo => ({
    ...todo,
    displayStatus: todo.status,
    statusText: STATUS_META[todo.status]?.label || STATUS_META.pending.label,
    statusIcon: STATUS_META[todo.status]?.icon || STATUS_META.pending.icon
  }))

export const getTaskProgressState = (
  summary: TaskSummary,
  lifecycleState: TaskSnapshot['lifecycleState'] = ''
): 'idle' | 'active' | 'done' | 'interrupted' => {
  if (lifecycleState === 'interrupted') return 'interrupted'
  if (!summary.total) return 'idle'
  return summary.pending + summary.in_progress + summary.interrupted > 0 ? 'active' : 'done'
}

export const getTaskProgressPercent = (summary: TaskSummary): number => {
  if (!summary.total) return 0
  return Math.min(100, Math.round((summary.completed / summary.total) * 100))
}

export const getTaskStatusText = (
  summary: TaskSummary,
  isLoading = false,
  statusHint = '',
  lifecycleState: TaskSnapshot['lifecycleState'] = ''
): string => {
  if (statusHint) return statusHint
  if (isLoading && !summary.total) return '规划中'
  if (lifecycleState === 'interrupted') return '已停止生成'
  if (!summary.total) return '待触发'
  return getTaskProgressState(summary) === 'done' ? '全部完成' : '执行中'
}

export const getTaskHeadline = (
  todos: NormalizedTodo[] = [],
  isLoading = false,
  lifecycleState: TaskSnapshot['lifecycleState'] = ''
): string => {
  if (isLoading && !todos.length) {
    return '正在规划任务'
  }

  if (lifecycleState === 'interrupted') {
    const interruptedTodo = todos.find(todo => todo.status === 'interrupted')
    if (interruptedTodo) {
      const stepIndex = todos.findIndex(todo => todo.id === interruptedTodo.id) + 1
      return `已停止：STEP ${stepIndex}`
    }
    return todos.length ? '任务已停止' : '已停止生成'
  }

  if (!todos.length) {
    return '暂无任务'
  }

  const inProgressTodo = todos.find(todo => todo.status === 'in_progress')
  if (inProgressTodo) {
    const stepIndex = todos.findIndex(todo => todo.id === inProgressTodo.id) + 1
    return `正在执行：STEP ${stepIndex}`
  }

  const pendingTodo = todos.find(todo => todo.status === 'pending')
  if (pendingTodo) {
    const stepIndex = todos.findIndex(todo => todo.id === pendingTodo.id) + 1
    return `待执行：STEP ${stepIndex}`
  }

  return '全部完成'
}

export const createTaskSnapshot = (
  todos: any[] = [],
  summary: Partial<TaskSummary> | null = null,
  options: { threadId?: string | null; isLoading?: boolean; statusHint?: string; lifecycleState?: TaskSnapshot['lifecycleState'] } = {}
): TaskSnapshot => {
  const normalizedTodos = normalizeTodos(todos, options.threadId || 'todo')
  const normalizedSummary = normalizeTaskSummary(summary, normalizedTodos)
  return {
    todos: normalizedTodos,
    summary: normalizedSummary,
    isLoading: Boolean(options.isLoading),
    statusHint: options.statusHint || '',
    lifecycleState: options.lifecycleState || '',
    updatedAt: new Date().toISOString()
  }
}

export const interruptTaskSnapshot = (
  taskSnapshot?: Partial<TaskSnapshot> | null,
  statusHint = '已停止生成'
): TaskSnapshot | null => {
  if (!taskSnapshot) return null
  const normalizedTodos = normalizeTodos(taskSnapshot.todos || [])
  const interruptedTodos = normalizedTodos.map(todo => (
    todo.status === 'in_progress'
      ? { ...todo, status: 'interrupted' as const }
      : todo
  ))

  return {
    todos: interruptedTodos,
    summary: buildTaskSummary(interruptedTodos),
    isLoading: false,
    statusHint,
    lifecycleState: 'interrupted',
    updatedAt: new Date().toISOString()
  }
}

export const hasVisibleTaskSnapshot = (taskSnapshot?: Partial<TaskSnapshot> | null): boolean => {
  if (!taskSnapshot) return false
  if (taskSnapshot.isLoading) return true
  return Array.isArray(taskSnapshot.todos) && taskSnapshot.todos.length > 0
}

export const completeTaskSnapshot = (
  taskSnapshot?: Partial<TaskSnapshot> | null,
  statusHint = '已完成'
): TaskSnapshot | null => {
  if (!taskSnapshot) return null
  const normalizedTodos = normalizeTodos(taskSnapshot.todos || [])
  const completedTodos = normalizedTodos.map(todo => ({
    ...todo,
    status: 'completed' as const
  }))

  return {
    todos: completedTodos,
    summary: buildTaskSummary(completedTodos),
    isLoading: false,
    statusHint,
    lifecycleState: '',
    updatedAt: new Date().toISOString()
  }
}
