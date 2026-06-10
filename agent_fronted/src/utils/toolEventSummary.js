const SQL_RESULT_KEYS = ['rows', 'total_rows', 'truncated', 'note']

const compactText = (value) => String(value ?? '').replace(/\s+/g, ' ').trim()

const truncateText = (value, limit = 160) => {
  const compacted = compactText(value)
  if (!compacted) return ''
  if (compacted.length <= limit) return compacted
  return `${compacted.slice(0, limit)}...`
}

const safeStringify = (value) => {
  try {
    return JSON.stringify(value)
  } catch (error) {
    return String(value)
  }
}

export const summarizeToolEventPayload = (payload, limit = 160) => {
  if (payload == null) return ''

  if (typeof payload === 'string' || typeof payload === 'number' || typeof payload === 'boolean') {
    return truncateText(payload, limit)
  }

  if (Array.isArray(payload)) {
    return truncateText(safeStringify(payload), limit)
  }

  if (typeof payload === 'object') {
    if (Array.isArray(payload.todos)) {
      const total = payload.todos.length
      const done = payload.todos.filter(todo => todo?.status === 'completed').length
      return truncateText(`todos: ${total} 项，已完成 ${done} 项`, limit)
    }

    if (SQL_RESULT_KEYS.some(key => Object.prototype.hasOwnProperty.call(payload, key))) {
      const rowCount = Array.isArray(payload.rows) ? payload.rows.length : 0
      const totalRows = typeof payload.total_rows === 'number' ? payload.total_rows : rowCount
      const truncated = payload.truncated ? '，结果已截断' : ''
      const note = payload.note ? `，${compactText(payload.note)}` : ''
      return truncateText(`返回 ${rowCount} 行，total_rows=${totalRows}${truncated}${note}`, limit)
    }

    if (typeof payload.content === 'string') {
      return truncateText(payload.content, limit)
    }

    return truncateText(safeStringify(payload), limit)
  }

  return truncateText(String(payload), limit)
}
