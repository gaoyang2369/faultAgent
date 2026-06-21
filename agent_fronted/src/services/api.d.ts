// API 服务类型声明文件

export interface ChatHistoryItem {
  id: string
  title?: string
  source?: 'server' | 'local-cache'
}

export interface ChatHistoryPage {
  items: ChatHistoryItem[]
  hasMore: boolean
  nextCursor?: string | null
  error?: unknown
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp?: string
  thread_id?: string
  threadId?: string
  trace_id?: string | null
  traceId?: string | null
  request_id?: string | null
  requestId?: string | null
  isMarkdown?: boolean
  hasChart?: boolean
  chartData?: any
  imageUrl?: string | null
  isStreaming?: boolean
  streamState?: string
  statusText?: string
  toolEvents?: any[]
  taskSnapshot?: any
  evidenceQuality?: any
  evidences?: any[]
  normalizedEvidences?: any[]
  evidenceCoverage?: any
  reportGate?: string
  reportFilename?: string | null
  reportUrl?: string | null
  reportArtifact?: any
  workorderDecision?: any
  workorder_decision?: any
  qualityGateNotice?: string | null
  rawFinalContent?: string
  toolLifecycleLedger?: any[]
}

export interface StreamCallbacks {
  onToken?: (data: { type: string; content: string; fullContent: string }) => void
  onMessage?: (data: { type: string; content: string; fullContent: string }) => void
  onToolCall?: (data: { type: string; tool: string; input?: any; result?: any; result_preview?: any; truncated?: boolean; action_guard?: any; thread_id?: string | null; run_id?: string; stage?: string; current_stage?: string; stage_duration_ms?: number; evidence?: any[]; evidence_count?: number; evidence_ids?: string[] }) => void
  onTaskUpdate?: (data: { type: string; thread_id?: string | null; trace_id?: string | null; current_stage?: string | null; todos: any[]; summary?: any; status_hint?: string; timestamp?: string | null }) => void
  onComplete?: (data: {
    type: string
    content: string
    raw_final_content?: string
    rawFinalContent?: string
    thread_id: string
    trace_id?: string | null
    traceId?: string | null
    request_id?: string | null
    requestId?: string | null
    stream_id?: string
    event_count: number
    todos: any[]
    timestamp: string
    evidence_quality?: any
    evidenceQuality?: any
    evidence_coverage?: any
    evidenceCoverage?: any
    evidences?: any[]
    normalized_evidences?: any[]
    normalizedEvidences?: any[]
    workflow_result?: any
    workflowResult?: any
    workflow_envelope?: any
    workflowEnvelope?: any
    scenario_result?: any
    scenarioResult?: any
    artifacts?: any[]
    timeline?: any[]
    governance?: any
    report_gate?: string
    reportGate?: string
    report_filename?: string | null
    reportFilename?: string | null
    report_url?: string | null
    reportUrl?: string | null
    report_artifact?: any
    reportArtifact?: any
    workorder_decision?: any
    workorderDecision?: any
    quality_gate_notice?: string | null
    qualityGateNotice?: string | null
    release_ready?: boolean | null
    releaseReady?: boolean | null
    workflow_stages?: any[]
    workflowStageDetails?: any[]
    workflow_stage_details?: any[]
    tool_lifecycle_ledger?: any[]
    toolLifecycleLedger?: any[]
  }) => void
  onError?: (error: { message: string; error_id?: string | null }) => void
  onStart?: (data: { type: string; thread_id: string | null; stream_id?: string; stage?: string; message?: string }) => void
  onPing?: (data: { type: string; timestamp: string; stage?: string; message?: string }) => void
  onInterrupted?: (data: { type: string; thread_id: string | null; stream_id?: string; message: string; reason?: string; user_initiated?: boolean }) => void
}

export interface StreamResponse {
  eventSource: EventSource
  streamId?: string
  close: (reason?: string) => void
  stop?: (reason?: string) => Promise<any>
}

export interface TodosResponse {
  thread_id: string
  todos: any[]
  summary: {
    total: number
    pending: number
    in_progress: number
    completed: number
  }
}

export interface IdentityResponse {
  user_id: string
  user_role: string
  display_name?: string
  role?: 'guest' | 'engineer' | 'admin'
  is_admin: boolean
  permissions?: string[]
  asset_scope?: string[]
  table_scope?: string[]
  system_scope?: string[]
  location_scope?: string[]
  kb_scopes?: string[]
  auth_method?: string | null
  available_auth_methods?: string[]
}

export interface AdminIdentityContext {
  userId?: string
  userRole?: string
}

export interface AdminPdfRecord {
  id: string
  fileName: string
  fileSize: number
  fileType: string
  uploadAt: number
  statusLabel: string
  ocrStatus: string
  ocrError: string
  ocrBackend: string
  kbIngestStatus: string
  kbError: string
  kbDocumentId: string
  kbIndexMode: string
  agentIngestStatus: string
  agentQueryReady: boolean
  agentQueryable: boolean
  knowledgeSourceType: string
  uploadStatus: string
  extractStatus: string
  lastError: string
  processedAt: number
  updatedAt: number
  resultPreview: string
  hasCorrection: boolean
  correctionSource: string
  correctedAt: number
  correctionVersion: number
  correctionPreview: string
  correctionIngestedAt: number
  correctionNeedsReingest: boolean
  correctionText: string
  kbText: string
  kbMarkdown: string
  nextAction: string
  statusTimeline: Array<{
    key: string
    label: string
    description: string
    status: string
    timestamp?: number
    error?: string
  }>
  structuredResult: {
    title?: string
    file_name?: string
    page_count?: number
    text_length?: number
    minimum_text_chars?: number
    preview_chars?: number
    full_text_saved?: boolean
    preview_text?: string
    extraction_mode?: string
    ocr_backend?: string
    medicine_ocr?: {
      configured?: boolean
      available?: boolean
      heavy_model_enabled?: boolean
      recommended_mode?: string
      notes?: string[]
    }
    sections?: Array<{ heading: string; content?: string; excerpt?: string }>
    page_summaries?: Array<{ page_number: number; char_count: number; has_text: boolean; excerpt?: string }>
  } | null
  fileUrl: string
}

export interface ChatAPI {
  closeActiveStream(reason?: string): void
  getChatHistory(type?: string): Promise<ChatHistoryItem[]>
  getChatHistoryPage(type?: string, options?: { limit?: number; cursor?: string | null; keyword?: string }): Promise<ChatHistoryPage>
  getChatMessages(chatId: string, type?: string): Promise<ChatMessage[]>
  deleteChatHistory(chatId: string, type?: string): Promise<{ deleted: boolean; thread_id: string; server_deleted?: boolean }>
  sendServiceMessageStream(message: string, threadId: string | null, userIdentity?: string, callbacks?: StreamCallbacks): Promise<StreamResponse>
  sendEditedServiceMessageStream(message: string, threadId: string, userTurnIndex: number, userIdentity?: string, callbacks?: StreamCallbacks): Promise<StreamResponse>
  stopStream(streamId: string, reason?: string): Promise<any>
  getThreadTodos(threadId: string): Promise<TodosResponse>
  saveGovernanceSnapshot(payload: {
    markdown: string
    json_content: Record<string, any>
    doc_template: string
    report_markdown?: string
    backlog_markdown?: string
    thread_id?: string | null
  }): Promise<any>
  listGovernanceSnapshots(threadId?: string | null, limit?: number): Promise<{
    items: Array<{
      snapshot_id: string
      thread_hint: string
      created_at?: string | null
      markdown_path?: string | null
      json_path?: string | null
      doc_template_path?: string | null
    }>
    thread_id?: string | null
    limit: number
  }>
  createGovernanceLedger(payload: {
    thread_id?: string | null
    summary: Array<Record<string, any>>
    risks: Array<Record<string, any>>
    items: Array<Record<string, any>>
    timeline: Array<Record<string, any>>
    status?: string | null
    owner?: string | null
    next_action?: string | null
    verified_result?: string | null
    due_date?: string | null
    priority?: string | null
    tags?: string[]
    source_snapshot_paths?: Record<string, string>
  }): Promise<any>
  listGovernanceLedger(threadId?: string | null, limit?: number, filters?: {
    status?: string
    priority?: string
    owner?: string
    tag?: string
  }): Promise<{
    items: Array<{
      record_id: string
      thread_id?: string | null
      thread_hint: string
      created_at?: string | null
      risk_count: number
      item_count: number
      priority_summary?: Array<Record<string, any>>
      detail_path: string
      status?: string
      owner?: string
      next_action?: string
      verified_result?: string
      due_date?: string | null
      priority?: string
      tags?: string[]
    }>
    summary?: {
      total: number
      status_counts: Record<string, number>
      priority_counts: Record<string, number>
    }
    filters?: {
      status?: string | null
      priority?: string | null
      owner?: string | null
      tag?: string | null
    }
    thread_id?: string | null
    limit: number
  }>
  updateGovernanceLedger(payload: {
    record_id: string
    status?: string | null
    owner?: string | null
    next_action?: string | null
    verified_result?: string | null
    due_date?: string | null
    priority?: string | null
    tags?: string[]
  }): Promise<any>
  createWorkOrder(payload: Record<string, any>): Promise<any>
  listWorkOrders(threadId?: string | null, limit?: number, filters?: { traceId?: string; status?: string }): Promise<any>
  updateWorkOrder(payload: Record<string, any>): Promise<any>
}

export interface AdminAuthAPI {
  getIdentity(): Promise<IdentityResponse>
  login(username: string, password: string): Promise<IdentityResponse>
  logout(): Promise<IdentityResponse>
}

export interface AdminPdfAPI {
  listRecords(identityContext?: AdminIdentityContext): Promise<AdminPdfRecord[]>
  getRecord(recordId: string, identityContext?: AdminIdentityContext): Promise<AdminPdfRecord>
  uploadFile(file: File, identityContext?: AdminIdentityContext): Promise<{ record: AdminPdfRecord; duplicate?: boolean }>
  ingestRecord(recordId: string, identityContext?: AdminIdentityContext): Promise<{ record: AdminPdfRecord; scheduled: boolean; alreadyIngested?: boolean; message?: string }>
  saveCorrection(recordId: string, correctedText: string, identityContext?: AdminIdentityContext): Promise<{ record: AdminPdfRecord; message?: string; next_action?: string }>
  deleteRecord(recordId: string, identityContext?: AdminIdentityContext): Promise<{ deleted: boolean; record_id: string }>
}

export interface DocumentWorkflowResult {
  markdown?: string
  originalPreviewUrl?: string
  restoredImageUrl?: string
  resultPdfUrl?: string
  pdfUrl?: string
  record?: AdminPdfRecord | null
  fileId?: string
  message?: string
}

export interface DocumentRecognitionAPI {
  uploadOnlyFile(file: File, identityContext?: AdminIdentityContext): Promise<DocumentWorkflowResult & { duplicate?: boolean }>
  uploadAndRecognizeFile(file: File, identityContext?: AdminIdentityContext): Promise<DocumentWorkflowResult>
  restoreImage(file: File, identityContext?: AdminIdentityContext): Promise<DocumentWorkflowResult>
  generatePdfFromMarkdown(
    markdown: string,
    options?: {
      restoredImageUrl?: string
      originalFileId?: string
      fileType?: string
      fallbackPdfUrl?: string
    },
    identityContext?: AdminIdentityContext
  ): Promise<DocumentWorkflowResult>
}

export const chatAPI: ChatAPI
export const adminAuthAPI: AdminAuthAPI
export const adminPdfAPI: AdminPdfAPI
export const documentRecognitionAPI: DocumentRecognitionAPI
export const BASE_URL: string
export function resolveBaseUrl(): string
