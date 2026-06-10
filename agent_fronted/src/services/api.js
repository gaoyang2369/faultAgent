export const resolveBaseUrl = () => {
  const explicitBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim()
  if (explicitBaseUrl) {
    return explicitBaseUrl.replace(/\/$/, '')
  }

  if (typeof window !== 'undefined') {
    return '/api'
  }

  return 'http://localhost:8000'
}

export const BASE_URL = resolveBaseUrl()
const STREAM_ABORT_MESSAGE = '连接已中断，请手动重试'
const USER_STOP_MESSAGE = '已停止生成'

let activeStreamHandle = null

export const fetchWithSession = (url, options = {}) => {
  return fetch(url, {
    credentials: 'include',
    ...options
  })
}

const parseErrorResponse = async (response) => {
  try {
    const data = await response.clone().json()
    if (typeof data?.detail === 'string' && data.detail.trim()) {
      return data.detail.trim()
    }
    if (typeof data?.message === 'string' && data.message.trim()) {
      return data.message.trim()
    }
  } catch {
    // ignore
  }

  try {
    const text = await response.text()
    if (text?.trim()) {
      return text.trim()
    }
  } catch {
    // ignore
  }

  return `HTTP error! status: ${response.status}`
}

const fetchJsonWithSession = async (url, options = {}) => {
  const response = await fetchWithSession(url, options)
  if (!response.ok) {
    const error = new Error(await parseErrorResponse(response))
    error.status = response.status
    throw error
  }
  return response.json()
}

const normalizeAdminPdfRecord = (record = {}) => ({
  id: record.id,
  fileName: record.file_name || record.fileName || '',
  fileSize: Number(record.file_size ?? record.fileSize ?? 0),
  fileType: record.file_type || record.fileType || 'application/pdf',
  uploadAt: Number(record.uploaded_at ?? record.uploadAt ?? 0),
  statusLabel: record.status_label || record.statusLabel || '服务端已登记',
  ocrStatus: record.ocr_status || record.ocrStatus || 'uploaded',
  ocrError: record.ocr_error || record.ocrError || '',
  ocrBackend: record.ocr_backend || record.ocrBackend || '',
  kbIngestStatus: record.kb_ingest_status || record.kbIngestStatus || 'pending',
  kbError: record.kb_error || record.kbError || '',
  kbDocumentId: record.kb_document_id || record.kbDocumentId || '',
  kbIndexMode: record.kb_index_mode || record.kbIndexMode || '',
  agentIngestStatus: record.agent_ingest_status || record.agentIngestStatus || 'pending',
  agentQueryReady: Boolean(record.agent_query_ready ?? record.agentQueryReady ?? false),
  agentQueryable: Boolean(record.agent_queryable ?? record.agentQueryable ?? record.agent_query_ready ?? record.agentQueryReady ?? false),
  knowledgeSourceType: record.knowledge_source_type || record.knowledgeSourceType || 'uploaded_pdf',
  uploadStatus: record.upload_status || record.uploadStatus || 'uploaded',
  extractStatus: record.extract_status || record.extractStatus || record.ocr_status || record.ocrStatus || 'uploaded',
  lastError: record.last_error || record.lastError || record.kb_error || record.kbError || record.ocr_error || record.ocrError || '',
  processedAt: Number(record.processed_at ?? record.processedAt ?? 0),
  updatedAt: Number(record.updated_at ?? record.updatedAt ?? record.processed_at ?? record.processedAt ?? record.uploaded_at ?? record.uploadAt ?? 0),
  resultPreview: record.result_preview || record.resultPreview || '',
  hasCorrection: Boolean(record.has_correction ?? record.hasCorrection ?? false),
  correctionSource: record.correction_source || record.correctionSource || '',
  correctedAt: Number(record.corrected_at ?? record.correctedAt ?? 0),
  correctionVersion: Number(record.correction_version ?? record.correctionVersion ?? 0),
  correctionPreview: record.correction_preview || record.correctionPreview || '',
  correctionIngestedAt: Number(record.correction_ingested_at ?? record.correctionIngestedAt ?? 0),
  correctionNeedsReingest: Boolean(record.correction_needs_reingest ?? record.correctionNeedsReingest ?? false),
  correctionText: record.correction_text || record.correctionText || '',
  kbText: record.kb_text || record.kbText || record.kb_markdown || record.kbMarkdown || '',
  kbMarkdown: record.kb_markdown || record.kbMarkdown || record.kb_text || record.kbText || '',
  nextAction: record.next_action || record.nextAction || '',
  statusTimeline: Array.isArray(record.status_timeline || record.statusTimeline)
    ? (record.status_timeline || record.statusTimeline).map((item) => ({
        key: item.key || '',
        label: item.label || '',
        description: item.description || '',
        status: item.status || 'pending',
        timestamp: Number(item.timestamp || 0),
        error: item.error || ''
      }))
    : [],
  structuredResult: record.structured_result || record.structuredResult || null,
  fileUrl: record.file_url
    ? (record.file_url.startsWith('http') ? record.file_url : `${BASE_URL}${record.file_url}`)
    : `${BASE_URL}/admin/pdfs/${encodeURIComponent(record.id || '')}/file`
})

const parseEventPayload = (event, label) => {
  try {
    return JSON.parse(event.data)
  } catch (error) {
    console.error(`解析 ${label} 事件失败:`, error)
    return null
  }
}

const clearActiveHandle = (handle) => {
  if (activeStreamHandle === handle) {
    activeStreamHandle = null
  }
}

const createStreamId = () => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `stream-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`
}

const shouldNotifyInterrupted = (reason) => ['interrupted', 'user_stop'].includes(reason)

const shouldRequestBackendStop = (reason) => ['interrupted', 'user_stop'].includes(reason)

export const chatAPI = {

  closeActiveStream(reason = 'interrupted') {
    if (activeStreamHandle) {
      activeStreamHandle.close(reason)
    }
  },

  async stopStream(streamId, reason = 'user_stop') {
    if (!streamId) {
      return { ok: false, status: 'missing_stream_id' }
    }

    const response = await fetchWithSession(`${BASE_URL}/chat/stop`, {
      method: 'POST',
      keepalive: true,
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        stream_id: streamId,
        reason
      })
    })

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }

    return response.json()
  },

  // 获取聊天历史列表
  async getChatHistory(type = 'chat') {
    try {
      const response = await fetchWithSession(`${BASE_URL}/ai/history/${type}`)
      if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
      const chatIds = await response.json()
      return chatIds.map(id => ({
        id,
        title: type === 'pdf' ? `PDF对话 ${id.slice(-6)}` :
               type === 'service' ? `咨询 ${id.slice(-6)}` :
               `对话 ${id.slice(-6)}`
      }))
    } catch (error) {
      console.error('API Error:', error)
      return []
    }
  },

  async getChatHistoryPage(type = 'chat', options = {}) {
    try {
      const params = new URLSearchParams()
      params.set('limit', String(options.limit || 30))
      if (options.cursor) params.set('cursor', String(options.cursor))
      if (options.keyword) params.set('q', String(options.keyword))
      const response = await fetchWithSession(`${BASE_URL}/ai/history/${type}?${params.toString()}`)
      if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
      const data = await response.json()
      const rawItems = Array.isArray(data) ? data.map(id => ({ id })) : Array.isArray(data?.items) ? data.items : []
      return {
        items: rawItems.map(item => ({
          id: item.id,
          title: item.title || (type === 'pdf' ? `PDF对话 ${String(item.id || '').slice(-6)}` :
                 type === 'service' ? `咨询 ${String(item.id || '').slice(-6)}` :
                 `对话 ${String(item.id || '').slice(-6)}`)
        })),
        hasMore: Boolean(data?.has_more ?? data?.hasMore ?? false),
        nextCursor: data?.next_cursor || data?.nextCursor || null
      }
    } catch (error) {
      console.error('API Error:', error)
      return {
        items: [],
        hasMore: false,
        nextCursor: null,
        error
      }
    }
  },

  // 获取特定对话的消息历史
  async getChatMessages(chatId, type = 'chat') {
    try {
      const response = await fetchWithSession(`${BASE_URL}/ai/history/${type}/${encodeURIComponent(chatId)}`)
      if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
      const messages = await response.json()
      return messages.map(msg => ({
        ...msg,
        timestamp: msg.timestamp || new Date().toISOString()
      }))
    } catch (error) {
      console.error('API Error:', error)
      return []
    }
  },

  async deleteChatHistory(chatId, type = 'service') {
    const response = await fetchWithSession(`${BASE_URL}/ai/history/${type}/${encodeURIComponent(chatId)}`, {
      method: 'DELETE'
    })
    if (!response.ok) {
      const message = await response.text()
      const error = new Error(message || `HTTP error! status: ${response.status}`)
      error.status = response.status
      error.responseText = message
      throw error
    }
    return response.json()
  },

  // 流式聊天：单活连接 + 显式关闭 + 禁止危险自动重连
  async sendServiceMessageStream(message, threadId, userIdentity = '游客', callbacks = {}, options = {}) {
    try {
      this.closeActiveStream('interrupted')

      const streamId = createStreamId()
      const params = new URLSearchParams()
      params.set('message', message)
      if (threadId) {
        params.set('thread_id', threadId)
      }
      if (Number.isInteger(options?.edit?.userTurnIndex)) {
        params.set('user_turn_index', String(options.edit.userTurnIndex))
      }
      if (userIdentity) {
        params.set('user_identity', userIdentity)
      }
      params.set('stream_id', streamId)

      const endpoint = Number.isInteger(options?.edit?.userTurnIndex)
        ? '/chat/stream/edit'
        : '/chat/stream'
      const url = `${BASE_URL}${endpoint}?${params.toString()}`
      const eventSource = new EventSource(url, { withCredentials: true })
      let fullContent = ''
      let isComplete = false
      let isClosed = false
      let activeThreadId = threadId || null
      let stopRequestPromise = null

      const streamHandle = {
        eventSource,
        streamId,
        stop: async (reason = 'user_stop') => {
          if (stopRequestPromise) {
            return stopRequestPromise
          }
          stopRequestPromise = Promise.resolve().then(() => this.stopStream(streamId, reason))
          stopRequestPromise.catch((error) => {
            console.warn('停止流式请求失败:', error)
          })
          return stopRequestPromise
        },
        close: (reason = 'manual') => {
          if (isClosed) return
          isClosed = true
          clearActiveHandle(streamHandle)
          eventSource.close()
          if (!isComplete && shouldRequestBackendStop(reason)) {
            streamHandle.stop(reason)
          }
          if (!isComplete && shouldNotifyInterrupted(reason) && callbacks.onInterrupted) {
            callbacks.onInterrupted({
              type: 'interrupted',
              thread_id: activeThreadId,
              stream_id: streamId,
              reason,
              user_initiated: reason === 'user_stop',
              message: reason === 'user_stop' ? USER_STOP_MESSAGE : '流式连接已关闭'
            })
          }
        }
      }

      activeStreamHandle = streamHandle

      const isActive = () => activeStreamHandle === streamHandle && !isClosed

      eventSource.addEventListener('start', (event) => {
        if (!isActive()) return
        const data = parseEventPayload(event, 'start')
        if (!data) {
          callbacks.onError?.({ message: '解析开始事件失败', error_id: null })
          streamHandle.close('error')
          return
        }
        activeThreadId = data.thread_id || activeThreadId
        callbacks.onStart?.({
          type: data.type || 'chat_start',
          thread_id: activeThreadId,
          stream_id: data.stream_id || streamId,
          stage: data.stage || 'reasoning',
          message: data.message || ''
        })
      })

      eventSource.addEventListener('ping', (event) => {
        if (!isActive()) return
        const data = parseEventPayload(event, 'ping')
        if (!data) return
        callbacks.onPing?.(data)
      })

      eventSource.addEventListener('token', (event) => {
        if (!isActive()) return
        const data = parseEventPayload(event, 'token')
        if (!data) return
        fullContent += data.content || ''
        const payload = {
          type: 'token',
          content: data.content || '',
          fullContent
        }
        callbacks.onToken?.(payload)
        callbacks.onMessage?.(payload)
      })

      eventSource.addEventListener('tool_start', (event) => {
        if (!isActive()) return
        const data = parseEventPayload(event, 'tool_start')
        if (!data) return
        callbacks.onToolCall?.({
          type: 'tool_start',
          tool: data.tool,
          input: data.input,
          run_id: data.run_id,
          stage: data.stage,
          current_stage: data.current_stage,
          thread_id: activeThreadId
        })
      })

      eventSource.addEventListener('tool_end', (event) => {
        if (!isActive()) return
        const data = parseEventPayload(event, 'tool_end')
        if (!data) return
        callbacks.onToolCall?.({
          type: 'tool_end',
          tool: data.tool,
          result: data.result,
          result_preview: data.result_preview,
          stage: data.stage,
          current_stage: data.current_stage,
          stage_duration_ms: data.stage_duration_ms,
          evidence: data.evidence || [],
          evidence_count: data.evidence_count || 0,
          evidence_ids: data.evidence_ids || [],
          action_guard: data.action_guard || null,
          truncated: Boolean(data.truncated),
          thread_id: activeThreadId
        })
      })

      eventSource.addEventListener('complete', (event) => {
        if (!isActive()) return
        const data = parseEventPayload(event, 'complete')
        if (!data) {
          callbacks.onError?.({ message: '解析完成事件失败', error_id: null })
          streamHandle.close('error')
          return
        }
        isComplete = true
        activeThreadId = data.thread_id || activeThreadId
        fullContent = data.final_content || fullContent
        callbacks.onComplete?.({
          type: 'complete',
          content: fullContent,
          raw_final_content: data.raw_final_content || fullContent,
          rawFinalContent: data.raw_final_content || fullContent,
          grounded_final_content: data.grounded_final_content || fullContent,
          groundedFinalContent: data.grounded_final_content || fullContent,
          thread_id: activeThreadId,
          threadId: activeThreadId,
          stream_id: data.stream_id || streamId,
          streamId: data.stream_id || streamId,
          event_count: data.event_count,
          eventCount: data.event_count,
          evidence_count: data.evidence_count || 0,
          evidenceCount: data.evidence_count || 0,
          evidences: data.evidences || [],
          normalized_evidences: data.normalized_evidences || [],
          normalizedEvidences: data.normalized_evidences || [],
          findings: data.findings || [],
          finding_links: data.finding_links || [],
          findingLinks: data.finding_links || [],
          workflow_result: data.workflow_result || null,
          workflowResult: data.workflow_result || null,
          workflow_envelope: data.workflow_envelope || null,
          workflowEnvelope: data.workflow_envelope || null,
          scenario_result: data.scenario_result || null,
          scenarioResult: data.scenario_result || null,
          artifacts: data.artifacts || [],
          timeline: data.timeline || [],
          governance: data.governance || null,
          evidence_quality: data.evidence_quality || null,
          evidenceQuality: data.evidence_quality || null,
          evidence_coverage: data.evidence_coverage || null,
          evidenceCoverage: data.evidence_coverage || null,
          report_gate: data.report_gate || 'pass',
          reportGate: data.report_gate || 'pass',
          report_filename: data.report_filename || null,
          reportFilename: data.report_filename || null,
          report_url: data.report_url || null,
          reportUrl: data.report_url || null,
          report_artifact: data.report_artifact || null,
          reportArtifact: data.report_artifact || null,
          quality_gate_notice: data.quality_gate_notice || null,
          qualityGateNotice: data.quality_gate_notice || null,
          release_ready: data.release_ready ?? null,
          releaseReady: data.release_ready ?? null,
          workflow_stages: data.workflow_stages || [],
          workflowStages: data.workflow_stages || [],
          current_stage: data.current_stage || null,
          currentStage: data.current_stage || null,
          workflow_stage_details: data.workflow_stage_details || [],
          workflowStageDetails: data.workflow_stage_details || [],
          tool_lifecycle_ledger: data.tool_lifecycle_ledger || [],
          toolLifecycleLedger: data.tool_lifecycle_ledger || [],
          todos: data.todos || [],
          timestamp: data.timestamp
        })
        streamHandle.close('complete')
      })

      eventSource.addEventListener('server_error', (event) => {
        if (!isActive()) return
        const data = parseEventPayload(event, 'server_error')
        callbacks.onError?.({
          message: data?.message || '服务端错误',
          error_id: data?.error_id || null
        })
        streamHandle.close('error')
      })

      eventSource.onerror = () => {
        if (!isActive() || isComplete) return
        callbacks.onError?.({
          message: STREAM_ABORT_MESSAGE,
          error_id: null
        })
        streamHandle.close('error')
      }

      return streamHandle
    } catch (error) {
      console.error('SSE API Error:', error)
      callbacks.onError?.({ message: error.message, error_id: null })
      throw error
    }
  },

  // 获取指定对话的任务清单
  async sendEditedServiceMessageStream(message, threadId, userTurnIndex, userIdentity = '游客', callbacks = {}) {
    return this.sendServiceMessageStream(
      message,
      threadId,
      userIdentity,
      callbacks,
      {
        edit: {
          userTurnIndex
        }
      }
    )
  },

  async getThreadTodos(threadId) {
    if (!threadId) {
      return {
        thread_id: '',
        todos: [],
        summary: {
          total: 0,
          pending: 0,
          in_progress: 0,
          completed: 0
        }
      }
    }

    try {
      const response = await fetchWithSession(`${BASE_URL}/api/todos/${encodeURIComponent(threadId)}`)
      if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
      return await response.json()
    } catch (error) {
      console.error('获取任务清单失败:', error)
      return {
        thread_id: threadId,
        todos: [],
        summary: {
          total: 0,
          pending: 0,
          in_progress: 0,
          completed: 0
        }
      }
    }
  },

  async saveGovernanceSnapshot(payload) {
    const response = await fetchWithSession(`${BASE_URL}/api/governance/save`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(payload)
    })

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }

    return response.json()
  },

  async listGovernanceSnapshots(threadId, limit = 6) {
    const params = new URLSearchParams()
    if (threadId) {
      params.set('thread_id', threadId)
    }
    params.set('limit', String(limit))

    const response = await fetchWithSession(`${BASE_URL}/api/governance/list?${params.toString()}`)
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }

    return response.json()
  },

  async createGovernanceLedger(payload) {
    const response = await fetchWithSession(`${BASE_URL}/api/governance/ledger`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(payload)
    })
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }
    return response.json()
  },

  async listGovernanceLedger(threadId, limit = 6, filters = {}) {
    const params = new URLSearchParams()
    if (threadId) {
      params.set('thread_id', threadId)
    }
    params.set('limit', String(limit))
    if (filters.status) {
      params.set('status', filters.status)
    }
    if (filters.priority) {
      params.set('priority', filters.priority)
    }
    if (filters.owner) {
      params.set('owner', filters.owner)
    }
    if (filters.tag) {
      params.set('tag', filters.tag)
    }

    const response = await fetchWithSession(`${BASE_URL}/api/governance/ledger?${params.toString()}`)
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }
    return response.json()
  },

  async updateGovernanceLedger(payload) {
    const response = await fetchWithSession(`${BASE_URL}/api/governance/ledger/update`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(payload)
    })
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }
    return response.json()
  }
}

export const adminAuthAPI = {
  async getIdentity() {
    return fetchJsonWithSession(`${BASE_URL}/auth/identity`)
  },

  async login(username, password) {
    return fetchJsonWithSession(`${BASE_URL}/auth/admin/login`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        username,
        password
      })
    })
  },

  async logout() {
    return fetchJsonWithSession(`${BASE_URL}/auth/logout`, {
      method: 'POST'
    })
  }
}

export const adminPdfAPI = {
  async listRecords() {
    const data = await fetchJsonWithSession(`${BASE_URL}/admin/pdfs`)
    const records = Array.isArray(data?.records) ? data.records : []
    return records.map(normalizeAdminPdfRecord)
  },

  async getRecord(recordId) {
    const data = await fetchJsonWithSession(`${BASE_URL}/admin/pdfs/${encodeURIComponent(recordId)}`)
    return normalizeAdminPdfRecord(data || {})
  },

  async uploadFile(file) {
    const formData = new FormData()
    formData.append('file', file)
    const data = await fetchJsonWithSession(`${BASE_URL}/admin/pdfs`, {
      method: 'POST',
      body: formData
    })
    return {
      ...data,
      record: normalizeAdminPdfRecord(data?.record || {})
    }
  },

  async ingestRecord(recordId) {
    const data = await fetchJsonWithSession(`${BASE_URL}/admin/pdfs/${encodeURIComponent(recordId)}/ingest`, {
      method: 'POST'
    })
    return {
      ...data,
      record: normalizeAdminPdfRecord(data?.record || {})
    }
  },

  async saveCorrection(recordId, correctedText) {
    const data = await fetchJsonWithSession(`${BASE_URL}/admin/pdfs/${encodeURIComponent(recordId)}/correction`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        corrected_text: correctedText
      })
    })
    return {
      ...data,
      record: normalizeAdminPdfRecord(data?.record || {})
    }
  },

  async deleteRecord(recordId) {
    return fetchJsonWithSession(`${BASE_URL}/admin/pdfs/${encodeURIComponent(recordId)}`, {
      method: 'DELETE'
    })
  }
}

const isPdfFileLike = (file) => {
  const name = String(file?.name || '').toLowerCase()
  const type = String(file?.type || '').toLowerCase()
  return name.endsWith('.pdf') || type === 'application/pdf'
}

const buildMarkdownFromRecord = (record = {}) => {
  if (record.correctionText?.trim()) return record.correctionText
  if (record.kbMarkdown?.trim()) return record.kbMarkdown
  if (record.kbText?.trim()) return record.kbText
  if (record.resultPreview?.trim()) return record.resultPreview

  const structured = record.structuredResult || {}
  const parts = []
  const title = structured.title || structured.file_name || record.fileName
  const metaRows = [
    record.fileName ? `- 文件名：${record.fileName}` : '',
    typeof structured.page_count === 'number' ? `- 页数：${structured.page_count}` : '',
    (structured.ocr_backend || record.ocrBackend) ? `- 解析后端：${structured.ocr_backend || record.ocrBackend}` : '',
    structured.extraction_mode ? `- 处理模式：${structured.extraction_mode}` : ''
  ].filter(Boolean)

  if (title) parts.push(`# ${title}`)
  if (metaRows.length) parts.push(`## 文档元信息\n${metaRows.join('\n')}`)
  if (structured.preview_text?.trim()) parts.push(`## 文档正文\n${structured.preview_text.trim()}`)
  if (Array.isArray(structured.sections)) {
    structured.sections.forEach((section) => {
      if (!section?.heading) return
      const content = section.content || section.excerpt || ''
      parts.push(`## ${section.heading}\n${content}`.trim())
    })
  }
  return parts.join('\n\n')
}

const normalizeDocumentWorkflowPayload = (data = {}) => ({
  markdown: data.markdown || data.markdownContent || data.text || '',
  originalPreviewUrl: data.original_preview_url || data.originalPreviewUrl || '',
  restoredImageUrl: data.restored_image_url || data.restoredImageUrl || '',
  resultPdfUrl: data.pdf_url || data.result_pdf_url || data.resultPdfUrl || '',
  record: data.record ? normalizeAdminPdfRecord(data.record) : null,
  fileId: data.file_id || data.fileId || data.record_id || data.recordId || ''
})

export const documentRecognitionAPI = {
  async uploadOnlyFile(file, identityContext = {}) {
    if (isPdfFileLike(file)) {
      const { record, duplicate } = await adminPdfAPI.uploadFile(file, identityContext)
      return {
        record,
        fileId: record.id,
        resultPdfUrl: record.fileUrl,
        duplicate,
        message: duplicate ? '检测到同名同大小文件，已定位到现有服务端记录。' : 'PDF 已上传。'
      }
    }

    const formData = new FormData()
    formData.append('file', file)

    try {
      const data = await fetchJsonWithSession(`${BASE_URL}/admin/recognition/upload`, {
        ...withIdentityHeaders({}, identityContext),
        method: 'POST',
        body: formData
      })
      return normalizeDocumentWorkflowPayload(data)
    } catch (error) {
      if (error?.status !== 404) throw error

      // TODO: 后端接入图片仅上传接口后删除该降级分支，并返回 fileId/originalPreviewUrl。
      return {
        markdown: '',
        originalPreviewUrl: '',
        resultPdfUrl: '',
        record: null,
        fileId: '',
        message: '图片上传接口尚未接入，当前仅完成前端预览与流程选择。'
      }
    }
  },

  async uploadAndRecognizeFile(file, identityContext = {}) {
    if (isPdfFileLike(file)) {
      const { record } = await adminPdfAPI.uploadFile(file, identityContext)
      const detail = await adminPdfAPI.getRecord(record.id, identityContext)
      return {
        markdown: buildMarkdownFromRecord(detail),
        originalPreviewUrl: detail.fileUrl,
        resultPdfUrl: detail.fileUrl,
        record: detail,
        fileId: detail.id
      }
    }

    const formData = new FormData()
    formData.append('file', file)

    try {
      const data = await fetchJsonWithSession(`${BASE_URL}/admin/recognition/ocr`, {
        ...withIdentityHeaders({}, identityContext),
        method: 'POST',
        body: formData
      })
      return normalizeDocumentWorkflowPayload(data)
    } catch (error) {
      if (error?.status !== 404) throw error

      // TODO: 后端接入图片 OCR 后删除该降级分支，并由 /admin/recognition/ocr 返回真实 Markdown。
      return {
        markdown: [
          `# ${file.name}`,
          '',
          '## 待识别内容',
          '后端图片 OCR 接口尚未接入，当前仅保留原图预览与可编辑 Markdown 区域。'
        ].join('\n'),
        originalPreviewUrl: '',
        resultPdfUrl: '',
        record: null,
        fileId: ''
      }
    }
  },

  async restoreImage(file, identityContext = {}) {
    const formData = new FormData()
    formData.append('file', file)

    try {
      const data = await fetchJsonWithSession(`${BASE_URL}/admin/recognition/restore-image`, {
        ...withIdentityHeaders({}, identityContext),
        method: 'POST',
        body: formData
      })
      return normalizeDocumentWorkflowPayload(data)
    } catch (error) {
      if (error?.status !== 404) throw error

      // TODO: 后端接入图片复原接口后删除该降级分支，并返回 restoredImageUrl。
      return {
        restoredImageUrl: '',
        message: '图片复原接口尚未接入，可继续使用原图生成 PDF。'
      }
    }
  },

  async generatePdfFromMarkdown(markdown, options = {}, identityContext = {}) {
    try {
      const data = await fetchJsonWithSession(`${BASE_URL}/admin/recognition/markdown-to-pdf`, {
        ...withIdentityHeaders({}, identityContext),
        method: 'POST',
        headers: mergeHeaders(withIdentityHeaders({}, identityContext).headers, {
          'Content-Type': 'application/json'
        }),
        body: JSON.stringify({
          markdown,
          restored_image_url: options.restoredImageUrl || '',
          original_file_id: options.originalFileId || '',
          file_type: options.fileType || ''
        })
      })
      return normalizeDocumentWorkflowPayload(data)
    } catch (error) {
      if (error?.status !== 404) throw error

      // TODO: 后端接入 Markdown 转 PDF 接口后删除该降级分支，并返回 pdfUrl。
      return {
        pdfUrl: options.fallbackPdfUrl || '',
        resultPdfUrl: options.fallbackPdfUrl || '',
        message: 'Markdown 生成 PDF 接口尚未接入。'
      }
    }
  }
}
