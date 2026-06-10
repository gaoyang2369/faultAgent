<template>
  <el-dialog
    :model-value="modelValue"
    @update:model-value="emit('update:modelValue', $event)"
    width="96%"
    top="1vh"
    class="upload-dialog"
    modal-class="upload-dialog-overlay"
    header-class="upload-dialog-header"
    body-class="upload-dialog-body"
    :style="dialogStyle"
    :close-on-click-modal="false"
    append-to-body
  >
    <template #header>
      <div class="dialog-title">
        <span>文档 / 图片识别校对</span>
        <small>原始预览、OCR 识别、条件复原与 PDF 返回</small>
      </div>
    </template>

    <div class="upload-workspace">
      <section class="workspace-topbar">
        <div class="file-summary">
          <strong>{{ fileSummary.name }}</strong>
          <span>{{ fileSummary.meta }}</span>
        </div>

        <div class="topbar-actions">
          <button type="button" class="secondary-btn" @click="historyDrawerVisible = true">
            历史记录 {{ records.length }}
          </button>
          <button type="button" class="secondary-btn" @click="openFilePicker">选择文件</button>
          <button
            type="button"
            class="primary-btn"
            :disabled="!selectedFile || isProcessing || !workflowMode"
            @click="handlePrimaryAction"
          >
            {{ primaryActionLabel }}
          </button>
          <button
            v-if="isCorrectionWorkspaceVisible"
            type="button"
            class="secondary-btn"
            :disabled="!activeRecord || !isMarkdownDirty || isSavingCorrection || !markdownDraft.trim()"
            @click="saveCorrection"
          >
            {{ isSavingCorrection ? '保存中...' : '保存校对' }}
          </button>
          <button
            v-if="isCorrectionWorkspaceVisible && selectedFile"
            type="button"
            class="secondary-btn"
            :disabled="isProcessing"
            @click="workflowMode = ''"
          >
            返回选择
          </button>
          <button
            type="button"
            class="dark-btn"
            :disabled="ingestAction.disabled"
            :title="ingestAction.hint"
            @click="triggerKnowledgeBaseIngest"
          >
            {{ ingestAction.label }}
          </button>
          <button
            v-if="activeRecord"
            type="button"
            class="danger-btn"
            :disabled="isDeleting"
            @click="deleteActiveRecord"
          >
            {{ isDeleting ? '删除中...' : '删除记录' }}
          </button>
          <button type="button" class="ghost-btn" :disabled="!previewUrl && !markdownDraft" @click="clearSelectedFile">
            清除
          </button>
        </div>

        <span class="status-pill" :class="statusBadge.tone">{{ statusBadge.label }}</span>
      </section>

      <section class="workflow-strip" aria-label="处理流程">
        <div
          v-for="step in workflowSteps"
          :key="step.key"
          class="workflow-step"
          :class="step.status"
        >
          <span class="workflow-step__index">{{ step.index }}</span>
          <span class="workflow-step__content">
            <strong>{{ step.label }}</strong>
            <small>{{ step.description }}</small>
          </span>
        </div>
      </section>

      <div v-if="statusBanner.text" class="workspace-banner" :class="statusBanner.tone">
        {{ statusBanner.text }}
      </div>

      <Transition name="history-drawer">
        <div v-if="historyDrawerVisible" class="history-drawer-backdrop" @click.self="historyDrawerVisible = false">
          <aside class="history-drawer" aria-label="历史上传记录">
            <header class="history-drawer__head">
              <div>
                <strong>历史上传</strong>
                <span>{{ records.length }} 条记录</span>
              </div>
              <button type="button" class="text-btn" @click="historyDrawerVisible = false">关闭</button>
            </header>

            <input
              v-model="historySearch"
              class="history-search"
              type="search"
              placeholder="搜索文件名"
            />

            <div v-if="!records.length" class="history-empty">
              <strong>暂无历史记录</strong>
              <small>上传 PDF 后会显示在这里</small>
            </div>

            <div v-else-if="!filteredHistoryRecords.length" class="history-empty">
              <strong>没有匹配结果</strong>
              <small>换个关键词试试</small>
            </div>

            <div v-else class="history-list">
              <button
                v-for="record in filteredHistoryRecords"
                :key="record.id"
                type="button"
                class="history-item"
                :class="{ active: record.id === activeRecordId }"
                @click="selectHistoryRecord(record)"
              >
                <strong>{{ record.fileName }}</strong>
                <span>{{ formatFileSize(record.fileSize) }} · {{ describeOcrStatus(record.ocrStatus, record.ocrError) }}</span>
                <small>{{ formatHistoryTime(record.updatedAt || record.processedAt || record.uploadAt) }}</small>
              </button>
            </div>
          </aside>
        </div>
      </Transition>

      <section v-if="!isCorrectionWorkspaceVisible" class="decision-layout">
        <article class="workspace-panel decision-preview-panel">
          <header class="workspace-panel__head">
            <h2>原始文件</h2>
            <div v-if="previewUrl" class="preview-head-actions">
              <span v-if="currentFileType === 'pdf'" class="preview-source-badge">{{ previewSourceLabel }}</span>
              <button type="button" class="text-btn" title="清除当前文件" @click="clearSelectedFile">
                清除
              </button>
            </div>
          </header>
          <div class="workspace-panel__body pdf-body">
            <div v-if="previewUrl && currentFileType === 'pdf'" class="pdf-preview-shell">
              <iframe
                v-if="!pdfPreviewFailed"
                :key="previewUrl"
                class="pdf-inline-frame"
                :src="previewUrl"
                title="原始 PDF 预览"
                @load="handlePdfPreviewLoaded"
                @error="handlePdfPreviewError"
              ></iframe>
              <div v-if="pdfPreviewLoading && !pdfPreviewFailed" class="pdf-preview-state">
                <span class="pdf-preview-spinner"></span>
                <strong>正在加载 PDF 预览...</strong>
                <small>{{ previewSourceLabel }}</small>
              </div>
              <div v-else-if="pdfPreviewFailed" class="pdf-preview-state failed">
                <strong>嵌入式 PDF 预览加载失败</strong>
                <small>请重新选择文件，或稍后从历史记录中再次打开。</small>
              </div>
            </div>
            <img
              v-else-if="previewUrl && currentFileType === 'image'"
              class="image-inline-preview"
              :src="previewUrl"
              alt="原始图片预览"
            />
            <div
              v-else
              class="upload-empty"
              :class="{ over: isDragOver }"
              @click="openFilePicker"
              @dragover.prevent="isDragOver = true"
              @dragleave.prevent="isDragOver = false"
              @drop.prevent="handleDrop"
            >
              <div class="file-mark">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <polyline points="14 2 14 8 20 8" />
                  <line x1="12" y1="18" x2="12" y2="12" />
                  <polyline points="9 15 12 12 15 15" />
                </svg>
              </div>
              <strong>选择 PDF 或图片</strong>
              <small>支持 pdf、png、jpg、jpeg、webp</small>
            </div>
            <div v-if="selectedFile || activeRecord" class="file-meta-card">
              <strong>{{ fileSummary.name }}</strong>
              <span>{{ fileSummary.meta }}</span>
              <span v-if="blurDetectionResult.message">{{ blurDetectionResult.message }}</span>
            </div>
          </div>
        </article>

        <article v-if="!isSavedPdfPreviewVisible" class="workspace-panel decision-panel">
          <header class="workspace-panel__head">
            <h2>处理方式</h2>
            <span>{{ decisionHint }}</span>
          </header>
          <div class="workspace-panel__body decision-body">
            <button
              type="button"
              class="decision-card"
              :class="{ active: workflowMode === 'upload_only' }"
              :disabled="!selectedFile || isProcessing"
              @click="selectWorkflowMode('upload_only')"
            >
              <span class="decision-card__badge">推荐</span>
              <strong>仅上传文件</strong>
              <small>不进入 Markdown 校对，不调用图片复原；适合 PDF 或清晰图片。</small>
            </button>
            <button
              type="button"
              class="decision-card"
              :class="{ active: workflowMode === 'restore_and_correct' }"
              :disabled="currentFileType !== 'image' || isProcessing"
              @click="selectWorkflowMode('restore_and_correct')"
            >
              <span class="decision-card__badge warn">图片专用</span>
              <strong>需要复原并校对</strong>
              <small>仅图片可选。进入复原、OCR、Markdown 校对和 PDF 返回流程。</small>
            </button>

            <div class="decision-footer">
              <button
                type="button"
                class="primary-btn"
                :disabled="!selectedFile || !workflowMode || isProcessing"
                @click="handlePrimaryAction"
              >
                {{ primaryActionLabel }}
              </button>
              <span>{{ selectedDecisionText }}</span>
            </div>
          </div>
        </article>

        <article v-else class="workspace-panel saved-preview-panel">
          <header class="workspace-panel__head">
            <h2>存入效果预览</h2>
            <span>{{ savedPdfPreviewStatus }}</span>
          </header>
          <div class="workspace-panel__body saved-preview-body">
            <div class="saved-preview-note">{{ savedPdfPreviewHint }}</div>
            <div
              v-if="renderedPdfHtml"
              class="pdf-html-preview saved-preview-content"
              v-html="renderedPdfHtml"
            ></div>
            <div v-else class="preview-empty saved-preview-empty">
              <strong>{{ savedPdfPreviewEmptyTitle }}</strong>
              <small>{{ savedPdfPreviewEmptyHint }}</small>
            </div>
          </div>
        </article>
      </section>

      <section v-else class="workspace-grid">
        <article class="workspace-panel pdf-panel">
          <header class="workspace-panel__head">
            <h2>原文件</h2>
            <div v-if="previewUrl" class="preview-head-actions">
              <span v-if="currentFileType === 'pdf'" class="preview-source-badge">{{ previewSourceLabel }}</span>
              <button type="button" class="text-btn" title="清除当前文件" @click="clearSelectedFile">
                清除
              </button>
            </div>
          </header>
          <div class="workspace-panel__body pdf-body">
            <div v-if="previewUrl && currentFileType === 'pdf'" class="pdf-preview-shell">
              <iframe
                v-if="!pdfPreviewFailed"
                :key="previewUrl"
                class="pdf-inline-frame"
                :src="previewUrl"
                title="原始 PDF 预览"
                @load="handlePdfPreviewLoaded"
                @error="handlePdfPreviewError"
              ></iframe>
              <div v-if="pdfPreviewLoading && !pdfPreviewFailed" class="pdf-preview-state">
                <span class="pdf-preview-spinner"></span>
                <strong>正在加载 PDF 预览...</strong>
                <small>{{ previewSourceLabel }}</small>
              </div>
              <div v-else-if="pdfPreviewFailed" class="pdf-preview-state failed">
                <strong>嵌入式 PDF 预览加载失败</strong>
                <small>请重新选择文件，或稍后从历史记录中再次打开。</small>
              </div>
            </div>
            <img
              v-else-if="previewUrl && currentFileType === 'image'"
              class="image-inline-preview"
              :src="previewUrl"
              alt="原始图片预览"
            />
            <div
              v-else
              class="upload-empty"
              :class="{ over: isDragOver }"
              @click="openFilePicker"
              @dragover.prevent="isDragOver = true"
              @dragleave.prevent="isDragOver = false"
              @drop.prevent="handleDrop"
            >
              <div class="file-mark">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <polyline points="14 2 14 8 20 8" />
                  <line x1="12" y1="18" x2="12" y2="12" />
                  <polyline points="9 15 12 12 15 15" />
                </svg>
              </div>
              <strong>选择 PDF 或图片</strong>
              <small>支持 pdf、png、jpg、jpeg、webp</small>
            </div>
            <div v-if="selectedFile || activeRecord" class="file-meta-card">
              <strong>{{ fileSummary.name }}</strong>
              <span>{{ fileSummary.meta }}</span>
              <span v-if="blurDetectionResult.message">{{ blurDetectionResult.message }}</span>
            </div>
          </div>
        </article>

        <article class="workspace-panel markdown-panel">
          <header class="workspace-panel__head">
            <h2>Markdown 校对</h2>
            <span>{{ markdownStats }}</span>
          </header>
          <div class="workspace-panel__body markdown-body">
            <textarea
              v-model="markdownDraft"
              class="markdown-editor"
              spellcheck="false"
              placeholder="识别完成后，Markdown 内容会显示在这里。你可以直接修正文案、表格和图片说明。"
            ></textarea>
          </div>
        </article>

        <article class="workspace-panel preview-panel">
          <header class="workspace-panel__head">
            <h2>PDF 返回</h2>
            <span>{{ resultPanelStatus }}</span>
          </header>
          <div class="workspace-panel__body result-body">
            <a
              v-if="resultPdfUrl"
              class="download-link result-download-link"
              :href="resultPdfUrl"
              target="_blank"
              rel="noopener noreferrer"
            >
              预览 / 下载
            </a>
            <div v-if="renderedPdfHtml" class="pdf-html-preview" v-html="renderedPdfHtml"></div>
            <iframe
              v-else-if="resultPdfUrl"
              class="result-pdf-frame"
              :src="resultPdfUrl"
              title="生成后的 PDF 预览"
            ></iframe>
            <div v-else class="preview-empty pdf-result-empty">
              <strong>等待 PDF 返回</strong>
              <small>{{ pdfResultHint }}</small>
            </div>
          </div>
        </article>
      </section>

      <input
        ref="fileInputRef"
        type="file"
        accept=".pdf,application/pdf,image/png,image/jpeg,image/jpg,image/webp"
        hidden
        @change="handleFileSelected"
      />
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

import { adminPdfAPI, documentRecognitionAPI } from '@/services/api'
import { useUserIdentityStore } from '@/stores/userIdentity'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean): void
}>()
const userIdentityStore = useUserIdentityStore()

type StatusTimelineItem = {
  key: string
  label: string
  description: string
  status: 'done' | 'current' | 'pending' | 'failed' | 'skipped' | string
  timestamp?: number
  error?: string
}

type UploadRecord = {
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
  statusTimeline: StatusTimelineItem[]
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

type StatusTone = 'idle' | 'processing' | 'success' | 'warning' | 'danger'
type FileKind = 'pdf' | 'image' | ''
type WorkflowMode = 'upload_only' | 'restore_and_correct' | ''
type WorkflowStepStatus = 'pending' | 'current' | 'done' | 'skipped' | 'failed'

type BlurDetectionResult = {
  score: number
  threshold: number
  isBlurred: boolean
  checked: boolean
  message: string
}

const MAX_FILE_SIZE = 50 * 1024 * 1024
const REQUEST_TIMEOUT_MS = 120000
const DETAIL_TIMEOUT_MS = 45000
const dialogStyle = {
  width: 'min(96vw, 1920px)',
  height: '96vh',
  maxHeight: '96vh',
  marginTop: '1vh'
}

marked.setOptions({
  gfm: true,
  breaks: true
})

const fileInputRef = ref<HTMLInputElement | null>(null)
const selectedFile = ref<File | null>(null)
const fileType = ref<FileKind>('')
const workflowMode = ref<WorkflowMode>('')
const isDragOver = ref(false)
const isUploading = ref(false)
const isRecognizing = ref(false)
const isRestoring = ref(false)
const isGeneratingPdf = ref(false)
const isDeleting = ref(false)
const isIngesting = ref(false)
const isSavingCorrection = ref(false)
const localPreviewUrl = ref('')
const serverPreviewUrl = ref('')
const restoredImageUrl = ref('')
const resultPdfUrl = ref('')
const needRestore = ref(false)
const blurDetectionResult = ref<BlurDetectionResult>({
  score: 0,
  threshold: 120,
  isBlurred: false,
  checked: false,
  message: ''
})
const errorMessage = ref('')
const activeRecordId = ref('')
const records = ref<UploadRecord[]>([])
const historyDrawerVisible = ref(false)
const historySearch = ref('')
const pdfPreviewLoading = ref(false)
const pdfPreviewFailed = ref(false)
const markdownDraft = ref('')
const lastSyncedMarkdown = ref('')
const lastHydratedRecordId = ref('')
let statusPollTimer: number | null = null

const activeRecord = computed(() => records.value.find(record => record.id === activeRecordId.value) || null)
const previewUrl = computed(() => localPreviewUrl.value || serverPreviewUrl.value)
const isMarkdownDirty = computed(() => Boolean(activeRecord.value) && markdownDraft.value !== lastSyncedMarkdown.value)
const isProcessing = computed(() => isUploading.value || isRecognizing.value || isRestoring.value || isGeneratingPdf.value)
const currentFileType = computed<FileKind>(() => fileType.value || (activeRecord.value ? 'pdf' : ''))
const isCorrectionWorkspaceVisible = computed(() => workflowMode.value === 'restore_and_correct')
const isSavedPdfPreviewVisible = computed(() =>
  Boolean(activeRecord.value) && !selectedFile.value && currentFileType.value === 'pdf'
)
const previewSourceLabel = computed(() => {
  if (currentFileType.value !== 'pdf' || !previewUrl.value) return ''
  if (localPreviewUrl.value) return '本地待上传预览'
  if (activeRecord.value) return '服务端已保存预览'
  return 'PDF 预览'
})
const filteredHistoryRecords = computed(() => {
  const keyword = historySearch.value.trim().toLowerCase()
  if (!keyword) return records.value
  return records.value.filter(record => record.fileName.toLowerCase().includes(keyword))
})

const savedPdfPreviewStatus = computed(() => {
  const record = activeRecord.value
  if (!record) return '等待上传'
  if (record.ocrStatus === 'uploaded' || record.ocrStatus === 'extracting_text' || record.ocrStatus === 'processing') {
    return '文本提取中'
  }
  if (record.ocrStatus === 'needs_heavy_ocr' || record.ocrStatus === 'ocr_model_not_configured') {
    return '需要 OCR'
  }
  if (record.kbIngestStatus === 'succeeded' && !record.correctionNeedsReingest) return '已归档知识库'
  if (record.correctionNeedsReingest) return '待重新归档'
  if (markdownDraft.value.trim()) return '待知识库归档'
  return '等待文本'
})

const savedPdfPreviewHint = computed(() => {
  const record = activeRecord.value
  if (!record) return '上传 PDF 后，这里会展示准备写入知识库的文本效果。'
  if (record.kbIngestStatus === 'succeeded' && !record.correctionNeedsReingest) {
    return '当前展示的是已归档到知识库的文本效果，Agent 可以查询这份内容。'
  }
  if (record.correctionNeedsReingest) {
    return '当前展示的是最新校正内容。请点击“重新归档”，Agent 才会使用这一版内容。'
  }
  return '当前展示的是准备归档到知识库的文本效果。点击“知识库归档”后，Agent 才能查询这份内容。'
})

const savedPdfPreviewEmptyTitle = computed(() => {
  const record = activeRecord.value
  if (!record) return '等待上传 PDF'
  if (record.ocrStatus === 'needs_heavy_ocr' || record.ocrStatus === 'ocr_model_not_configured') return '当前 PDF 需要 OCR'
  if (record.ocrStatus === 'ocr_failed' || record.ocrStatus === 'failed') return '文本提取失败'
  return '正在生成文本预览'
})

const savedPdfPreviewEmptyHint = computed(() => {
  const record = activeRecord.value
  if (!record) return '确认上传后会自动读取服务端提取结果。'
  if (record.ocrStatus === 'needs_heavy_ocr' || record.ocrStatus === 'ocr_model_not_configured') {
    return '当前 PDF 的可提取文本较少，需要接入重型 OCR 后再生成可归档内容。'
  }
  if (record.ocrStatus === 'ocr_failed' || record.ocrStatus === 'failed') {
    return record.ocrError || '请检查 PDF 内容后重新上传。'
  }
  return '后端正在提取 PDF 文本，完成后这里会自动刷新。'
})

const primaryActionLabel = computed(() => {
  if (workflowMode.value === 'upload_only') return isUploading.value ? '上传中...' : '确认上传'
  if (workflowMode.value === 'restore_and_correct') return isProcessing.value ? '处理中...' : '开始复原校对'
  return '请选择处理方式'
})

const decisionHint = computed(() => {
  if (!selectedFile.value) return '请先选择文件'
  if (currentFileType.value === 'pdf') return 'PDF 默认仅上传'
  if (blurDetectionResult.value.checked) return blurDetectionResult.value.isBlurred ? '检测到疑似模糊' : '检测为清晰图片'
  return '请选择是否复原'
})

const selectedDecisionText = computed(() => {
  if (!selectedFile.value) return '上传文件后再选择处理方式'
  if (workflowMode.value === 'upload_only') return '当前选择：仅上传，不进入 Markdown 校对。'
  if (workflowMode.value === 'restore_and_correct') return '当前选择：图片复原后进入 Markdown 校对。'
  return '请选择一种处理方式。'
})

const fileSummary = computed(() => {
  if (selectedFile.value) {
    const label = fileType.value === 'image' ? '图片' : 'PDF'
    const restoreLabel = workflowMode.value === 'restore_and_correct'
      ? '需要复原并校对'
      : '仅上传'
    return {
      name: selectedFile.value.name,
      meta: `${label} · ${formatFileSize(selectedFile.value.size)} · ${selectedFile.value.type || '未知类型'} · ${restoreLabel}`
    }
  }

  const record = activeRecord.value
  if (record) {
    return {
      name: record.fileName,
      meta: `${record.fileType || 'PDF'} · ${formatFileSize(record.fileSize)} · ${describeOcrStatus(record.ocrStatus, record.ocrError)}`
    }
  }

  return {
    name: '尚未选择文件',
    meta: '选择 PDF 或图片后会在左侧立即预览'
  }
})

const ingestAction = computed(() => {
  const record = activeRecord.value
  if (!record) {
    return { label: '知识库归档', disabled: true, hint: '请先选择已上传的 PDF 记录。' }
  }
  if (isIngesting.value || record.kbIngestStatus === 'processing') {
    return { label: '归档中...', disabled: true, hint: '当前 PDF 正在归档知识库。' }
  }
  if (record.correctionNeedsReingest) {
    return { label: '重新归档', disabled: false, hint: '校正内容已保存，请重新归档后再让 Agent 使用。' }
  }
  if (record.kbIngestStatus === 'succeeded') {
    return { label: record.hasCorrection ? '已归档最新' : '已归档', disabled: true, hint: '该 PDF 已归档知识库，Agent 可直接查询。' }
  }
  if (record.ocrStatus === 'extracting_text' || record.ocrStatus === 'uploaded' || record.ocrStatus === 'processing') {
    return { label: '文本提取中...', disabled: true, hint: '请等待文本提取完成后再归档。' }
  }
  if (record.ocrStatus === 'needs_heavy_ocr' || record.ocrStatus === 'ocr_model_not_configured') {
    return { label: '需要 OCR 后归档', disabled: true, hint: '该 PDF 当前文本不足，需要重型 OCR 后才能归档。' }
  }
  if (record.kbIngestStatus === 'failed') {
    return { label: '重试归档', disabled: false, hint: record.kbError || '知识库归档失败，可重试。' }
  }
  return { label: '知识库归档', disabled: false, hint: '将当前 PDF 正文归档到上传知识库，供 Agent 查询。' }
})

const markdownStats = computed(() => {
  const text = markdownDraft.value
  if (!text) return '0 字'
  const lines = text.split(/\r\n|\r|\n/).length
  return `${text.length} 字 · ${lines} 行`
})

const resultPanelStatus = computed(() => {
  if (isRestoring.value) return '复原中'
  if (isGeneratingPdf.value) return '生成 PDF 中'
  if (renderedPdfHtml.value) return 'HTML 预览'
  if (resultPdfUrl.value) return 'PDF 已返回'
  return '等待处理'
})

const pdfResultHint = computed(() => {
  if (isRestoring.value) return '正在复原图片，完成后会用于生成 PDF。'
  if (isGeneratingPdf.value) return '正在生成或获取 PDF 结果。'
  if (currentFileType.value === 'image') return '识别完成后会用 HTML 渲染 PDF 内容预览。'
  return '识别完成后会展示 PDF 内容预览。'
})

const renderedPdfHtml = computed(() => {
  const markdown = markdownDraft.value.trim()
  if (!markdown) return ''
  const html = marked.parse(markdown, { async: false }) as string
  return DOMPurify.sanitize(html)
})

const workflowSteps = computed(() => {
  if (!workflowMode.value) {
    return [
      {
        key: 'upload',
        index: 1,
        label: '文件上传',
        description: selectedFile.value || activeRecord.value ? '已选择文件' : '等待选择',
        status: selectedFile.value || activeRecord.value ? 'done' : 'current'
      },
      {
        key: 'preview',
        index: 2,
        label: '原始预览',
        description: previewUrl.value ? '已生成预览' : '等待文件',
        status: previewUrl.value ? 'done' : 'pending'
      },
      {
        key: 'mode',
        index: 3,
        label: '处理选择',
        description: selectedFile.value ? '等待选择方式' : '等待文件',
        status: selectedFile.value ? 'current' : 'pending'
      }
    ] as Array<{ key: string; index: number; label: string; description: string; status: WorkflowStepStatus }>
  }

  if (workflowMode.value === 'upload_only') {
    return [
      {
        key: 'upload',
        index: 1,
        label: '文件上传',
        description: selectedFile.value || activeRecord.value ? '已选择文件' : '等待选择',
        status: selectedFile.value || activeRecord.value ? 'done' : 'current'
      },
      {
        key: 'preview',
        index: 2,
        label: '原始预览',
        description: previewUrl.value ? '已生成预览' : '等待文件',
        status: previewUrl.value ? 'done' : 'pending'
      },
      {
        key: 'mode',
        index: 3,
        label: '处理方式',
        description: '仅上传',
        status: 'done'
      },
      {
        key: 'done',
        index: 4,
        label: '上传完成',
        description: activeRecord.value || errorMessage.value ? '已返回状态' : '等待上传',
        status: isUploading.value ? 'current' : (activeRecord.value || errorMessage.value ? 'done' : 'pending')
      }
    ] as Array<{ key: string; index: number; label: string; description: string; status: WorkflowStepStatus }>
  }

  const steps: Array<{ key: string; index: number; label: string; description: string; status: WorkflowStepStatus }> = [
    {
      key: 'upload',
      index: 1,
      label: '文件上传',
      description: selectedFile.value || activeRecord.value ? '已选择文件' : '等待选择',
      status: selectedFile.value || activeRecord.value ? 'done' : 'current'
    },
    {
      key: 'preview',
      index: 2,
      label: '原始预览',
      description: previewUrl.value ? '已生成预览' : '等待文件',
      status: previewUrl.value ? 'done' : 'pending'
    },
    {
      key: 'ocr',
      index: 3,
      label: 'OCR识别',
      description: isRecognizing.value ? '处理中' : (markdownDraft.value.trim() ? '已完成' : '等待识别'),
      status: isRecognizing.value ? 'current' : (markdownDraft.value.trim() ? 'done' : 'pending')
    },
    {
      key: 'markdown',
      index: 4,
      label: 'Markdown生成',
      description: markdownDraft.value.trim() ? '可编辑校对' : '等待 OCR',
      status: markdownDraft.value.trim() ? 'done' : 'pending'
    },
    {
      key: 'restore',
      index: 5,
      label: '图片复原',
      description: getRestoreStepDescription(),
      status: getRestoreStepStatus()
    },
    {
      key: 'pdf',
      index: 6,
      label: 'PDF返回',
      description: isGeneratingPdf.value ? '生成中' : (resultPdfUrl.value ? '可预览下载' : '等待结果'),
      status: isGeneratingPdf.value ? 'current' : (resultPdfUrl.value ? 'done' : 'pending')
    }
  ]
  return steps
})

const statusBadge = computed<{ label: string; tone: StatusTone }>(() => {
  if (isProcessing.value) return { label: '处理中', tone: 'processing' }
  if (isSavingCorrection.value) return { label: '保存中', tone: 'processing' }
  if (isIngesting.value) return { label: '归档中', tone: 'processing' }
  if (errorMessage.value) return { label: '需确认', tone: 'warning' }

  const record = activeRecord.value
  if (record) {
    if (['ocr_failed', 'failed'].includes(record.ocrStatus) || record.kbIngestStatus === 'failed') {
      return { label: '失败', tone: 'danger' }
    }
    if (record.ocrStatus === 'needs_heavy_ocr' || record.ocrStatus === 'ocr_model_not_configured') {
      return { label: '需处理', tone: 'warning' }
    }
    if (record.ocrStatus === 'uploaded' || record.ocrStatus === 'extracting_text' || record.ocrStatus === 'processing') {
      return { label: '处理中', tone: 'processing' }
    }
    if (isMarkdownDirty.value) return { label: '已修改', tone: 'warning' }
    if (record.correctionNeedsReingest) return { label: '待归档', tone: 'warning' }
    if (markdownDraft.value.trim()) return { label: '待校对', tone: 'success' }
    return { label: '已上传', tone: 'processing' }
  }

  if (selectedFile.value && workflowMode.value === 'upload_only') return { label: '待上传', tone: 'idle' }
  if (selectedFile.value && workflowMode.value === 'restore_and_correct') return { label: '待复原', tone: 'idle' }
  if (selectedFile.value) return { label: '待选择', tone: 'idle' }
  return { label: '未上传', tone: 'idle' }
})

const statusBanner = computed<{ text: string; tone: StatusTone }>(() => {
  if (isUploading.value) return { text: '正在上传文件，请勿重复提交。', tone: 'processing' }
  if (isRecognizing.value) return { text: '正在调用 OCR 识别，Markdown 返回后会先展示，复原不会阻塞校对。', tone: 'processing' }
  if (isRestoring.value) return { text: '正在进行图片复原；Markdown 已可继续编辑。', tone: 'processing' }
  if (isGeneratingPdf.value) return { text: '正在生成或获取 PDF 结果。', tone: 'processing' }
  if (isSavingCorrection.value) return { text: '正在保存校对内容。', tone: 'processing' }
  if (isIngesting.value) return { text: '正在提交知识库归档。', tone: 'processing' }
  if (errorMessage.value) return { text: errorMessage.value, tone: 'warning' }
  if (selectedFile.value && !workflowMode.value) return { text: '请选择处理方式：仅上传，或图片复原并校对。', tone: 'warning' }

  const record = activeRecord.value
  if (!record) return { text: '', tone: 'idle' }
  if (record.lastError) return { text: record.lastError, tone: 'danger' }
  if (record.ocrStatus === 'ocr_failed' || record.ocrStatus === 'failed') {
    return { text: describeOcrStatus(record.ocrStatus, record.ocrError), tone: 'danger' }
  }
  if (record.kbIngestStatus === 'failed') {
    return { text: describeKbStatus(record.kbIngestStatus, record.kbError), tone: 'danger' }
  }
  if (record.ocrStatus === 'needs_heavy_ocr' || record.ocrStatus === 'ocr_model_not_configured') {
    return { text: describeScanHint(record) || describeOcrStatus(record.ocrStatus, record.ocrError), tone: 'warning' }
  }
  if (record.ocrStatus === 'uploaded' || record.ocrStatus === 'extracting_text' || record.ocrStatus === 'processing') {
    return { text: '后端正在提取 PDF 文本，完成后会自动刷新 Markdown。', tone: 'processing' }
  }
  if (isMarkdownDirty.value) return { text: 'Markdown 已修改，右侧预览已同步更新；保存后可重新归档。', tone: 'warning' }
  if (markdownDraft.value.trim()) return { text: '已获取 Markdown 识别结果，可开始校对。', tone: 'success' }
  return { text: '', tone: 'idle' }
})

const extractErrorMessage = (error: unknown, fallback: string) => {
  if (error instanceof Error && error.message.trim()) {
    return error.message
  }
  return fallback
}

const getUploadIdentityContext = () => ({
  userId: userIdentityStore.userId || '',
  userRole: userIdentityStore.userRole || ''
})

const appendUploadIdentityQuery = (url: string) => {
  if (!url) return url

  try {
    const nextUrl = new URL(url, window.location.origin)
    const identity = getUploadIdentityContext()
    if (identity.userId) nextUrl.searchParams.set('user_id', identity.userId)
    if (identity.userRole) nextUrl.searchParams.set('user_role', identity.userRole)
    return nextUrl.toString()
  } catch {
    return url
  }
}

function withTimeout<T>(promise: Promise<T>, timeoutMs: number, timeoutMessage: string) {
  let timeoutId: number | null = null
  const timeoutPromise = new Promise<never>((_, reject) => {
    timeoutId = window.setTimeout(() => reject(new Error(timeoutMessage)), timeoutMs)
  })
  return Promise.race([promise, timeoutPromise]).finally(() => {
    if (timeoutId !== null) window.clearTimeout(timeoutId)
  }) as Promise<T>
}

function openFilePicker() {
  fileInputRef.value?.click()
}

function handlePdfPreviewLoaded() {
  pdfPreviewLoading.value = false
}

function handlePdfPreviewError() {
  pdfPreviewLoading.value = false
  pdfPreviewFailed.value = true
}

function handleFileSelected(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (file) {
    loadFile(file).catch(error => {
      ElMessage.error(extractErrorMessage(error, '加载文件失败。'))
    })
  }
}

function handleDrop(event: DragEvent) {
  isDragOver.value = false
  const file = event.dataTransfer?.files?.[0]
  if (file) {
    loadFile(file).catch(error => {
      ElMessage.error(extractErrorMessage(error, '加载文件失败。'))
    })
  }
}

async function loadFile(file: File) {
  const nextFileType = detectFileType(file)
  if (!nextFileType) {
    ElMessage.error('仅支持上传 PDF、png、jpg、jpeg、webp 文件。')
    return
  }
  if (file.size > MAX_FILE_SIZE) {
    ElMessage.error('文件过大，单文件最多 50MB。')
    return
  }

  stopStatusPolling()
  clearServerSelection()
  revokePreviewUrl()
  resetProcessingState()
  selectedFile.value = file
  fileType.value = nextFileType
  localPreviewUrl.value = URL.createObjectURL(file)
  resetMarkdownWorkspace()
  if (nextFileType === 'pdf') {
    needRestore.value = false
    workflowMode.value = 'upload_only'
    blurDetectionResult.value = {
      score: 0,
      threshold: 120,
      isBlurred: false,
      checked: false,
      message: 'PDF 文件无需图片复原'
    }
  } else {
    const detection = await detectImageBlur(localPreviewUrl.value)
    blurDetectionResult.value = detection
    workflowMode.value = detection.isBlurred ? 'restore_and_correct' : 'upload_only'
    needRestore.value = workflowMode.value === 'restore_and_correct'
  }
  ElMessage.success(`已加载文件：${file.name}`)
}

function selectWorkflowMode(mode: WorkflowMode) {
  if (!selectedFile.value || isProcessing.value) return
  if (mode === 'restore_and_correct' && currentFileType.value !== 'image') {
    ElMessage.warning('PDF 文件无需图片复原，已保持仅上传。')
    workflowMode.value = 'upload_only'
    needRestore.value = false
    return
  }
  workflowMode.value = mode
  needRestore.value = mode === 'restore_and_correct'
  errorMessage.value = ''
}

function hasPendingProcessing(recordsList = records.value) {
  return recordsList.some(record =>
    ['uploaded', 'extracting_text', 'processing'].includes(record.ocrStatus) ||
    ['processing'].includes(record.kbIngestStatus)
  )
}

function selectHistoryRecord(record: UploadRecord) {
  selectRecord(record).then(() => {
    historyDrawerVisible.value = false
  }).catch(() => {})
}

function stopStatusPolling() {
  if (statusPollTimer !== null) {
    window.clearInterval(statusPollTimer)
    statusPollTimer = null
  }
}

function refreshStatusPolling() {
  stopStatusPolling()
  if (!props.modelValue || !hasPendingProcessing()) return
  statusPollTimer = window.setInterval(() => {
    refreshRecords(activeRecordId.value).catch(() => {})
  }, 3000)
}

async function refreshRecords(preferredRecordId = '') {
  try {
    const nextRecords = await withTimeout(
      adminPdfAPI.listRecords(getUploadIdentityContext()),
      DETAIL_TIMEOUT_MS,
      '加载上传记录超时，请稍后重试。'
    )
    records.value = nextRecords

    const nextActiveRecordId = preferredRecordId || activeRecordId.value
    if (nextActiveRecordId) {
      const matchedRecord = nextRecords.find(record => record.id === nextActiveRecordId)
      if (matchedRecord) {
        await selectRecord(matchedRecord, {
          silent: true,
          preserveLocalPreview: Boolean(localPreviewUrl.value && selectedFile.value && nextActiveRecordId === activeRecordId.value)
        })
        refreshStatusPolling()
        return
      }
    }

    if (!selectedFile.value && !nextRecords.length) {
      clearCurrentView()
    }
  } catch (error) {
    const message = extractErrorMessage(error, '加载上传记录失败。')
    ElMessage.error(message)
    if (message.includes('管理员')) {
      emit('update:modelValue', false)
    }
  } finally {
    refreshStatusPolling()
  }
}

async function handleStartProcess() {
  if (!selectedFile.value || isProcessing.value) return

  const processingFile = selectedFile.value
  const processingType = fileType.value
  errorMessage.value = ''
  resultPdfUrl.value = ''
  restoredImageUrl.value = ''

  isUploading.value = true
  isRecognizing.value = true
  try {
    const recognitionResult = await withTimeout(
      documentRecognitionAPI.uploadAndRecognizeFile(processingFile, getUploadIdentityContext()),
      REQUEST_TIMEOUT_MS,
      '上传/OCR 识别请求超时，请稍后刷新状态。'
    )

    if (recognitionResult.markdown?.trim()) {
      markdownDraft.value = recognitionResult.markdown
      lastSyncedMarkdown.value = recognitionResult.markdown
    }

    if (recognitionResult.record) {
      const record = recognitionResult.record
      records.value = [record, ...records.value.filter(item => item.id !== record.id)]
      activeRecordId.value = record.id
      serverPreviewUrl.value = appendUploadIdentityQuery(record.fileUrl)
      resultPdfUrl.value = appendUploadIdentityQuery(recognitionResult.resultPdfUrl || record.fileUrl)
      fileType.value = 'pdf'
      const detail = await loadRecordDetail(record.id, { forceMarkdown: true })
      hydrateMarkdown(detail, { force: true })
      resultPdfUrl.value = appendUploadIdentityQuery(detail.fileUrl)
      clearLocalSelection({ keepFileType: true, keepWorkflowMode: true })
      ElMessage.success('PDF 已上传，正在读取识别结果。')
    } else if (recognitionResult.resultPdfUrl) {
      resultPdfUrl.value = appendUploadIdentityQuery(recognitionResult.resultPdfUrl)
    }
  } catch (error) {
    errorMessage.value = extractErrorMessage(error, '上传识别失败。')
    ElMessage.error(errorMessage.value)
  } finally {
    isUploading.value = false
    isRecognizing.value = false
  }

  if (processingType === 'image' && needRestore.value) {
    isRestoring.value = true
    try {
      const restoreResult = await withTimeout(
        documentRecognitionAPI.restoreImage(processingFile, getUploadIdentityContext()),
        REQUEST_TIMEOUT_MS,
        '图片复原请求超时，可继续使用原图生成 PDF。'
      )
      if (restoreResult.restoredImageUrl) {
        restoredImageUrl.value = appendUploadIdentityQuery(restoreResult.restoredImageUrl)
        ElMessage.success('图片复原已完成。')
      } else {
        errorMessage.value = restoreResult.message || '复原失败，可继续使用原图生成 PDF。'
        ElMessage.warning(errorMessage.value)
      }
    } catch (error) {
      errorMessage.value = extractErrorMessage(error, '复原失败，可继续使用原图生成 PDF。')
      ElMessage.warning(errorMessage.value)
    } finally {
      isRestoring.value = false
    }
  }

  isGeneratingPdf.value = true
  try {
    const pdfResult = await withTimeout(
      documentRecognitionAPI.generatePdfFromMarkdown(
        markdownDraft.value,
        {
          restoredImageUrl: restoredImageUrl.value,
          originalFileId: activeRecordId.value,
          fileType: processingType,
          fallbackPdfUrl: resultPdfUrl.value
        },
        getUploadIdentityContext()
      ),
      REQUEST_TIMEOUT_MS,
      'PDF 生成请求超时，请稍后重试。'
    )
    const nextPdfUrl = pdfResult.resultPdfUrl || pdfResult.pdfUrl
    if (nextPdfUrl) {
      resultPdfUrl.value = appendUploadIdentityQuery(nextPdfUrl)
    } else if (pdfResult.message && processingType !== 'pdf') {
      errorMessage.value = pdfResult.message
    }
  } catch (error) {
    errorMessage.value = extractErrorMessage(error, 'PDF 生成失败，Markdown 内容仍可继续校对。')
    ElMessage.warning(errorMessage.value)
  } finally {
    isGeneratingPdf.value = false
    refreshStatusPolling()
  }
}

async function handleUploadOnly() {
  if (!selectedFile.value || isProcessing.value) return

  errorMessage.value = ''
  resultPdfUrl.value = ''
  restoredImageUrl.value = ''
  resetMarkdownWorkspace()

  isUploading.value = true
  try {
    const uploadResult = await withTimeout(
      documentRecognitionAPI.uploadOnlyFile(selectedFile.value, getUploadIdentityContext()),
      REQUEST_TIMEOUT_MS,
      '上传请求超时，请稍后刷新状态。'
    )

    if (uploadResult.record) {
      const record = uploadResult.record
      records.value = [record, ...records.value.filter(item => item.id !== record.id)]
      activeRecordId.value = record.id
      serverPreviewUrl.value = appendUploadIdentityQuery(record.fileUrl)
      resultPdfUrl.value = appendUploadIdentityQuery(uploadResult.resultPdfUrl || record.fileUrl)
      fileType.value = 'pdf'
      workflowMode.value = 'upload_only'
      clearLocalSelection({ keepFileType: true, keepWorkflowMode: true })
      ElMessage[uploadResult.duplicate ? 'info' : 'success'](uploadResult.message || '文件已上传。')
    } else {
      errorMessage.value = uploadResult.message || '图片上传接口尚未接入，当前仅完成前端预览。'
      ElMessage.warning(errorMessage.value)
    }
  } catch (error) {
    errorMessage.value = extractErrorMessage(error, '上传失败。')
    ElMessage.error(errorMessage.value)
  } finally {
    isUploading.value = false
    refreshStatusPolling()
  }
}

function handlePrimaryAction() {
  if (workflowMode.value === 'upload_only') {
    handleUploadOnly().catch(() => {})
    return
  }
  if (workflowMode.value === 'restore_and_correct') {
    handleStartProcess().catch(() => {})
  }
}

function buildEditableText(record: UploadRecord) {
  if (record.correctionText?.trim()) return record.correctionText
  if (record.kbMarkdown?.trim()) return record.kbMarkdown
  if (record.kbText?.trim()) return record.kbText

  const structured = record.structuredResult
  const parts: string[] = []
  const title = structured?.title || structured?.file_name || record.fileName
  const metaRows = [
    record.fileName ? `- 文件名：${record.fileName}` : '',
    typeof structured?.page_count === 'number' ? `- 页数：${structured.page_count}` : '',
    (structured?.ocr_backend || record.ocrBackend) ? `- 解析后端：${structured?.ocr_backend || record.ocrBackend}` : '',
    structured?.extraction_mode ? `- 处理模式：${structured.extraction_mode}` : ''
  ].filter(Boolean)

  if (title) parts.push(`# ${title}`)
  if (metaRows.length) parts.push(`## 文档元信息\n${metaRows.join('\n')}`)
  if (structured?.preview_text?.trim()) parts.push(`## 文档正文\n${structured.preview_text.trim()}`)
  if (structured?.sections?.length) {
    structured.sections.forEach(section => {
      if (!section?.heading) return
      const content = section.content || section.excerpt || ''
      parts.push(`## ${section.heading}\n${content}`.trim())
    })
  }
  if (!parts.length && record.resultPreview) parts.push(record.resultPreview)
  return parts.join('\n\n')
}

function hydrateMarkdown(record: UploadRecord, options: { force?: boolean } = {}) {
  const nextMarkdown = buildEditableText(record)
  const isDifferentRecord = lastHydratedRecordId.value !== record.id
  if (options.force || isDifferentRecord || !isMarkdownDirty.value) {
    markdownDraft.value = nextMarkdown
    lastSyncedMarkdown.value = nextMarkdown
    lastHydratedRecordId.value = record.id
  }
}

async function saveCorrection() {
  const record = activeRecord.value
  const correctedText = markdownDraft.value.trim()
  if (!record || !correctedText || isSavingCorrection.value || !isMarkdownDirty.value) return

  isSavingCorrection.value = true
  try {
    const { record: updatedRecord, message } = await withTimeout(
      adminPdfAPI.saveCorrection(record.id, correctedText, getUploadIdentityContext()),
      REQUEST_TIMEOUT_MS,
      '保存校对内容超时，请稍后重试。'
    )
    records.value = records.value.map(item => (item.id === updatedRecord.id ? updatedRecord : item))
    activeRecordId.value = updatedRecord.id
    markdownDraft.value = correctedText
    lastSyncedMarkdown.value = correctedText
    lastHydratedRecordId.value = updatedRecord.id
    ElMessage.success(message || '校正内容已保存，需要重新归档后才会被 Agent 使用。')
    await loadRecordDetail(updatedRecord.id)
  } catch (error) {
    ElMessage.error(extractErrorMessage(error, '保存校正失败。'))
  } finally {
    isSavingCorrection.value = false
    refreshStatusPolling()
  }
}

async function loadRecordDetail(recordId: string, options: { forceMarkdown?: boolean } = {}) {
  const detail = await withTimeout(
    adminPdfAPI.getRecord(recordId, getUploadIdentityContext()),
    DETAIL_TIMEOUT_MS,
    '加载 PDF 处理详情超时，请稍后重试。'
  )
  records.value = records.value.map(record => (record.id === detail.id ? detail : record))
  if (!records.value.some(record => record.id === detail.id)) {
    records.value = [detail, ...records.value]
  }
  if (activeRecordId.value === detail.id) {
    serverPreviewUrl.value = appendUploadIdentityQuery(detail.fileUrl)
    resultPdfUrl.value = appendUploadIdentityQuery(detail.fileUrl)
    fileType.value = 'pdf'
    hydrateMarkdown(detail, { force: options.forceMarkdown })
  }
  return detail
}

async function selectRecord(record: UploadRecord, options: { silent?: boolean; preserveLocalPreview?: boolean } = {}) {
  if (!options.preserveLocalPreview) {
    clearLocalSelection()
  }
  activeRecordId.value = record.id
  serverPreviewUrl.value = appendUploadIdentityQuery(record.fileUrl)
  resultPdfUrl.value = appendUploadIdentityQuery(record.fileUrl)
  fileType.value = 'pdf'
  workflowMode.value = 'upload_only'
  needRestore.value = false
  blurDetectionResult.value = {
    score: 0,
    threshold: 120,
    isBlurred: false,
    checked: false,
    message: 'PDF 文件无需图片复原'
  }
  hydrateMarkdown(record, { force: !options.silent })
  try {
    await loadRecordDetail(record.id)
  } catch (error) {
    ElMessage.error(extractErrorMessage(error, '加载 PDF 处理详情失败。'))
  }
  if (!options.silent) {
    ElMessage.success(`已切换到记录：${record.fileName}`)
  }
  refreshStatusPolling()
}

async function deleteActiveRecord() {
  if (!activeRecord.value || isDeleting.value) return

  const record = activeRecord.value
  try {
    await ElMessageBox.confirm(
      `确认删除记录「${record.fileName}」吗？`,
      '删除 PDF 记录',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }

  isDeleting.value = true
  try {
    await withTimeout(
      adminPdfAPI.deleteRecord(record.id, getUploadIdentityContext()),
      REQUEST_TIMEOUT_MS,
      '删除 PDF 记录超时，请稍后刷新状态。'
    )
    records.value = records.value.filter(item => item.id !== record.id)
    clearCurrentView()
    ElMessage.success('PDF 记录已删除。')
  } catch (error) {
    ElMessage.error(extractErrorMessage(error, '删除 PDF 记录失败。'))
  } finally {
    isDeleting.value = false
    refreshStatusPolling()
  }
}

async function triggerKnowledgeBaseIngest() {
  if (!activeRecord.value || ingestAction.value.disabled || isIngesting.value) return

  isIngesting.value = true
  try {
    const { record, scheduled, alreadyIngested, message } = await withTimeout(
      adminPdfAPI.ingestRecord(activeRecord.value.id, getUploadIdentityContext()),
      REQUEST_TIMEOUT_MS,
      '知识库归档请求超时，请稍后刷新状态。'
    )
    records.value = records.value.map(item => (item.id === record.id ? record : item))
    activeRecordId.value = record.id
    hydrateMarkdown(record)
    if (alreadyIngested) {
      ElMessage.info(message || '该 PDF 已归档知识库。')
    } else {
      ElMessage.success(message || (scheduled ? '已开始知识库归档。' : '知识库归档状态已刷新。'))
    }
    await loadRecordDetail(record.id)
  } catch (error) {
    ElMessage.error(extractErrorMessage(error, '知识库归档失败。'))
  } finally {
    isIngesting.value = false
    refreshStatusPolling()
  }
}

function clearSelectedFile() {
  clearCurrentView()
}

function clearLocalSelection(options: { keepFileType?: boolean; keepWorkflowMode?: boolean } = {}) {
  revokePreviewUrl()
  selectedFile.value = null
  if (!options.keepFileType) fileType.value = ''
  if (!options.keepWorkflowMode) workflowMode.value = ''
  if (fileInputRef.value) fileInputRef.value.value = ''
}

function clearServerSelection() {
  serverPreviewUrl.value = ''
  activeRecordId.value = ''
}

function clearCurrentView() {
  clearLocalSelection()
  clearServerSelection()
  resetProcessingState()
  resetMarkdownWorkspace()
}

function resetMarkdownWorkspace() {
  markdownDraft.value = ''
  lastSyncedMarkdown.value = ''
  lastHydratedRecordId.value = ''
}

function resetProcessingState() {
  isUploading.value = false
  isRecognizing.value = false
  isRestoring.value = false
  isGeneratingPdf.value = false
  restoredImageUrl.value = ''
  resultPdfUrl.value = ''
  needRestore.value = false
  workflowMode.value = ''
  errorMessage.value = ''
  blurDetectionResult.value = {
    score: 0,
    threshold: 120,
    isBlurred: false,
    checked: false,
    message: ''
  }
}

function isPdfFile(file: File) {
  const type = (file.type || '').toLowerCase()
  return file.name.toLowerCase().endsWith('.pdf') && (!type || type === 'application/pdf' || type === 'application/octet-stream')
}

function isImageFile(file: File) {
  const type = (file.type || '').toLowerCase()
  const name = file.name.toLowerCase()
  return ['image/png', 'image/jpeg', 'image/jpg', 'image/webp'].includes(type) ||
    /\.(png|jpe?g|webp)$/.test(name)
}

function detectFileType(file: File): FileKind {
  if (isPdfFile(file)) return 'pdf'
  if (isImageFile(file)) return 'image'
  return ''
}

function getRestoreStepDescription() {
  if (currentFileType.value === 'pdf') return '跳过：PDF无需复原'
  if (currentFileType.value === 'image' && isRestoring.value) return '处理中'
  if (currentFileType.value === 'image' && restoredImageUrl.value) return '已完成'
  if (currentFileType.value === 'image' && needRestore.value && errorMessage.value) return '失败，可继续'
  if (currentFileType.value === 'image' && needRestore.value) return '等待复原'
  if (currentFileType.value === 'image') return '跳过：图片清晰'
  return '等待文件'
}

function getRestoreStepStatus(): WorkflowStepStatus {
  if (currentFileType.value === 'pdf') return 'skipped'
  if (currentFileType.value === 'image' && isRestoring.value) return 'current'
  if (currentFileType.value === 'image' && restoredImageUrl.value) return 'done'
  if (currentFileType.value === 'image' && needRestore.value && errorMessage.value) return 'failed'
  if (currentFileType.value === 'image' && needRestore.value) return 'pending'
  if (currentFileType.value === 'image') return 'skipped'
  return 'pending'
}

function detectImageBlur(src: string): Promise<BlurDetectionResult> {
  const threshold = 120
  return new Promise((resolve) => {
    const image = new Image()
    image.onload = () => {
      const canvas = document.createElement('canvas')
      const maxSize = 360
      const ratio = Math.min(1, maxSize / Math.max(image.naturalWidth, image.naturalHeight))
      const width = Math.max(1, Math.round(image.naturalWidth * ratio))
      const height = Math.max(1, Math.round(image.naturalHeight * ratio))
      canvas.width = width
      canvas.height = height
      const context = canvas.getContext('2d', { willReadFrequently: true })
      if (!context || width < 3 || height < 3) {
        resolve({
          score: 0,
          threshold,
          isBlurred: false,
          checked: false,
          message: '未能完成清晰度检测，可手动选择是否复原'
        })
        return
      }

      context.drawImage(image, 0, 0, width, height)
      const pixels = context.getImageData(0, 0, width, height).data
      const gray = new Float32Array(width * height)
      for (let index = 0; index < gray.length; index += 1) {
        const offset = index * 4
        const red = pixels[offset] ?? 0
        const green = pixels[offset + 1] ?? 0
        const blue = pixels[offset + 2] ?? 0
        gray[index] = red * 0.299 + green * 0.587 + blue * 0.114
      }

      let sum = 0
      let squareSum = 0
      let count = 0
      for (let y = 1; y < height - 1; y += 1) {
        for (let x = 1; x < width - 1; x += 1) {
          const index = y * width + x
          const laplacian =
            (gray[index - width] ?? 0) +
            (gray[index - 1] ?? 0) -
            (gray[index] ?? 0) * 4 +
            (gray[index + 1] ?? 0) +
            (gray[index + width] ?? 0)
          sum += laplacian
          squareSum += laplacian * laplacian
          count += 1
        }
      }

      const mean = count ? sum / count : 0
      const variance = count ? squareSum / count - mean * mean : 0
      const score = Math.max(0, Math.round(variance))
      const isBlurred = score < threshold
      resolve({
        score,
        threshold,
        isBlurred,
        checked: true,
        message: isBlurred
          ? `清晰度评分 ${score}，疑似模糊，默认开启复原`
          : `清晰度评分 ${score}，图片清晰，无需复原`
      })
    }
    image.onerror = () => {
      resolve({
        score: 0,
        threshold,
        isBlurred: false,
        checked: false,
        message: '未能完成清晰度检测，可手动选择是否复原'
      })
    }
    image.src = src
  })
}

function formatFileSize(bytes: number) {
  if (!bytes) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function formatHistoryTime(timestamp: number) {
  if (!timestamp) return '暂无更新时间'
  return new Date(timestamp).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false
  })
}

function describeOcrStatus(status: string, error = '') {
  if (status === 'uploaded') return '已上传，等待处理'
  if (status === 'extracting_text' || status === 'processing') return '文本提取中'
  if (status === 'text_extracted' || status === 'succeeded') return '已提取文本'
  if (status === 'needs_heavy_ocr') return error || '该 PDF 可能是扫描件，需要重型 OCR'
  if (status === 'ocr_model_not_configured') return error || '该 PDF 可能是扫描件，当前未启用重型 OCR 模型'
  if (status === 'ocr_failed') return error ? `OCR 失败：${error}` : 'OCR 失败'
  if (status === 'failed') return error ? `处理失败：${error}` : '处理失败'
  return '待处理'
}

function describeKbStatus(status: string, error = '') {
  if (status === 'processing') return '归档中'
  if (status === 'succeeded') return '已归档知识库'
  if (status === 'failed') return error ? `归档失败：${error}` : '归档失败'
  if (status === 'skipped') return '未归档'
  return '待归档'
}

function describeScanHint(record: UploadRecord) {
  if (record.ocrStatus === 'ocr_model_not_configured') {
    return '该 PDF 可能是扫描件，当前未启用重型 OCR 模型。'
  }
  if (record.ocrStatus === 'needs_heavy_ocr') {
    return '该 PDF 可能是扫描件，轻量文本提取不足，需后续显式触发重型 OCR。'
  }
  return ''
}

function revokePreviewUrl() {
  if (localPreviewUrl.value) {
    URL.revokeObjectURL(localPreviewUrl.value)
    localPreviewUrl.value = ''
  }
}

watch(
  [previewUrl, currentFileType],
  ([nextPreviewUrl, nextFileType]) => {
    pdfPreviewLoading.value = Boolean(nextPreviewUrl) && nextFileType === 'pdf'
    pdfPreviewFailed.value = false
  },
  { immediate: true }
)

watch(
  () => props.modelValue,
  async (visible) => {
    if (visible) {
      await refreshRecords()
      return
    }
    stopStatusPolling()
  }
)

onBeforeUnmount(() => {
  stopStatusPolling()
  revokePreviewUrl()
})
</script>

<style scoped lang="scss">
.dialog-title {
  display: flex;
  align-items: baseline;
  gap: 10px;
  width: 100%;
  font-weight: 800;

  small {
    color: #64748b;
    font-weight: 500;
  }
}

:global(.upload-dialog-overlay .el-overlay-dialog) {
  overflow: hidden;
}

:global(.upload-dialog-overlay) {
  background: rgba(15, 23, 42, 0.34);
  backdrop-filter: blur(12px);
}

.upload-dialog :deep(.el-dialog),
:deep(.upload-dialog.el-dialog),
:global(.upload-dialog.el-dialog) {
  width: min(96vw, 1920px);
  height: 96vh !important;
  max-height: 96vh !important;
  margin-top: 1vh !important;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.72);
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.94), rgba(248, 250, 252, 0.88)),
    radial-gradient(circle at 16% 0%, rgba(59, 130, 246, 0.13), transparent 34%);
  box-shadow: 0 24px 70px rgba(15, 23, 42, 0.24);
  backdrop-filter: blur(18px);
}

.upload-dialog :deep(.el-dialog__header),
:deep(.upload-dialog.el-dialog .el-dialog__header),
:global(.upload-dialog.el-dialog .el-dialog__header),
:global(.upload-dialog-header) {
  flex: 0 0 auto;
  padding: 16px 18px 8px;
  margin: 0;
}

.upload-dialog :deep(.el-dialog__body),
:deep(.upload-dialog.el-dialog .el-dialog__body),
:global(.upload-dialog.el-dialog .el-dialog__body),
:global(.upload-dialog-body) {
  flex: 1;
  min-height: 0;
  padding: 0 18px 18px;
  overflow: hidden;
}

.upload-workspace {
  position: relative;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow: hidden;
  color: #0f172a;
}

.workspace-topbar {
  position: relative;
  min-height: 64px;
  display: grid;
  grid-template-columns: minmax(220px, 1fr) auto;
  gap: 14px;
  align-items: center;
  border: 1px solid rgba(226, 232, 240, 0.86);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.74);
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.07);
  backdrop-filter: blur(14px);
  padding: 10px 16px;
}

.file-summary {
  min-width: 0;

  strong,
  span {
    display: block;
  }

  strong {
    color: #111827;
    font-size: 15px;
    line-height: 1.35;
    word-break: break-word;
  }

  span {
    margin-top: 3px;
    color: #64748b;
    font-size: 12px;
  }
}

.topbar-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
  min-width: 0;
  padding-right: 78px;
}

.status-pill {
  position: absolute;
  top: 14px;
  right: 16px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 64px;
  height: 34px;
  border-radius: 999px;
  padding: 0 14px;
  font-weight: 800;
  font-size: 13px;

  &.idle {
    background: rgba(241, 245, 249, 0.9);
    color: #64748b;
  }

  &.processing {
    background: rgba(219, 234, 254, 0.95);
    color: #1d4ed8;
  }

  &.success {
    background: rgba(220, 252, 231, 0.95);
    color: #15803d;
  }

  &.warning {
    background: rgba(254, 243, 199, 0.95);
    color: #b45309;
  }

  &.danger {
    background: rgba(254, 226, 226, 0.95);
    color: #b91c1c;
  }
}

.workflow-strip {
  flex: 0 0 auto;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 8px;
}

.workflow-step {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 9px;
  border: 1px solid rgba(226, 232, 240, 0.85);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.68);
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.055);
  backdrop-filter: blur(12px);
  padding: 9px 10px;

  &__index {
    flex: 0 0 auto;
    width: 26px;
    height: 26px;
    display: grid;
    place-items: center;
    border-radius: 50%;
    background: #f1f5f9;
    color: #64748b;
    font-size: 12px;
    font-weight: 900;
  }

  &__content {
    min-width: 0;
    display: block;

    strong,
    small {
      display: block;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    strong {
      color: #111827;
      font-size: 13px;
      line-height: 1.25;
    }

    small {
      margin-top: 2px;
      color: #64748b;
      font-size: 12px;
    }
  }

  &.current {
    border-color: rgba(96, 165, 250, 0.78);
    background: rgba(239, 246, 255, 0.82);

    .workflow-step__index {
      background: #2563eb;
      color: #fff;
    }
  }

  &.done {
    border-color: rgba(134, 239, 172, 0.82);
    background: rgba(240, 253, 244, 0.82);

    .workflow-step__index {
      background: #16a34a;
      color: #fff;
    }
  }

  &.skipped {
    background: rgba(248, 250, 252, 0.7);

    .workflow-step__index {
      background: #e2e8f0;
      color: #475569;
    }
  }

  &.failed {
    border-color: rgba(254, 202, 202, 0.92);
    background: rgba(254, 242, 242, 0.82);

    .workflow-step__index {
      background: #dc2626;
      color: #fff;
    }
  }
}

.workspace-banner {
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 13px;
  font-weight: 700;
  backdrop-filter: blur(12px);

  &.processing {
    background: #eff6ff;
    color: #1d4ed8;
    border: 1px solid #bfdbfe;
  }

  &.success {
    background: #f0fdf4;
    color: #15803d;
    border: 1px solid #bbf7d0;
  }

  &.warning {
    background: #fffbeb;
    color: #b45309;
    border: 1px solid #fde68a;
  }

  &.danger {
    background: #fef2f2;
    color: #b91c1c;
    border: 1px solid #fecaca;
  }
}

.history-drawer-backdrop {
  position: absolute;
  inset: 0;
  z-index: 30;
  display: flex;
  justify-content: flex-end;
  background: rgba(15, 23, 42, 0.12);
  border-radius: 8px;
  backdrop-filter: blur(4px);
}

.history-drawer {
  width: min(420px, 92vw);
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 12px;
  border-left: 1px solid rgba(226, 232, 240, 0.9);
  background: rgba(255, 255, 255, 0.86);
  box-shadow: -18px 0 48px rgba(15, 23, 42, 0.16);
  backdrop-filter: blur(18px);
  padding: 16px;
}

.history-drawer__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;

  strong,
  span {
    display: block;
  }

  strong {
    color: #111827;
    font-size: 18px;
  }

  span {
    margin-top: 2px;
    color: #64748b;
    font-size: 13px;
  }
}

.history-search {
  flex: 0 0 auto;
  height: 40px;
  border: 1px solid rgba(203, 213, 225, 0.86);
  border-radius: 8px;
  background: rgba(248, 250, 252, 0.84);
  color: #111827;
  padding: 0 12px;
  outline: 0;
  font-weight: 700;
}

.history-list {
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow: auto;
  padding-right: 2px;
}

.history-item {
  width: 100%;
  min-height: 92px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 5px;
  border: 1px solid rgba(226, 232, 240, 0.9);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.72);
  color: #111827;
  padding: 12px 14px;
  text-align: left;
  cursor: pointer;
  transition: transform 0.18s, border-color 0.18s, background 0.18s, box-shadow 0.18s;

  &:hover,
  &.active {
    transform: translateY(-1px);
    border-color: rgba(37, 99, 235, 0.6);
    background: rgba(239, 246, 255, 0.86);
    box-shadow: 0 12px 28px rgba(37, 99, 235, 0.1);
  }

  strong {
    width: 100%;
    overflow: hidden;
    color: #111827;
    font-size: 14px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  span,
  small {
    color: #64748b;
    font-size: 12px;
  }
}

.history-empty {
  flex: 1;
  min-height: 220px;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 8px;
  border: 1px dashed rgba(148, 163, 184, 0.62);
  border-radius: 8px;
  background: rgba(248, 250, 252, 0.6);
  color: #64748b;
  text-align: center;

  strong {
    color: #334155;
  }
}

.history-drawer-enter-active,
.history-drawer-leave-active {
  transition: opacity 0.2s;

  .history-drawer {
    transition: transform 0.24s ease;
  }
}

.history-drawer-enter-from,
.history-drawer-leave-to {
  opacity: 0;

  .history-drawer {
    transform: translateX(28px);
  }
}

.workspace-grid {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(280px, 0.95fr) minmax(360px, 1.08fr) minmax(360px, 1.08fr);
  gap: 12px;
  overflow: hidden;
}

.decision-layout {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(420px, 0.92fr) minmax(420px, 1.08fr);
  gap: 12px;
  overflow: hidden;
}

.decision-preview-panel,
.decision-panel {
  min-height: 0;
}

.decision-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  background: rgba(248, 250, 252, 0.62);
  padding: 16px;
}

.saved-preview-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow: hidden;
  background: rgba(248, 250, 252, 0.62);
  padding: 16px;
}

.saved-preview-note {
  flex: 0 0 auto;
  border: 1px solid rgba(147, 197, 253, 0.72);
  border-radius: 8px;
  background: rgba(239, 246, 255, 0.9);
  color: #1e40af;
  padding: 11px 13px;
  font-size: 13px;
  line-height: 1.6;
}

.saved-preview-content {
  flex: 1;
  min-height: 0;
  border: 1px solid rgba(226, 232, 240, 0.9);
  border-radius: 8px;
  background: #fff;
  padding: 18px 20px;
}

.saved-preview-empty {
  flex: 1;
  min-height: 0;
  border: 1px dashed rgba(148, 163, 184, 0.72);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.78);
  color: #334155;

  small {
    max-width: 520px;
    color: #64748b;
    line-height: 1.7;
    text-align: center;
  }
}

.decision-card {
  position: relative;
  min-height: 140px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 10px;
  border: 1px solid rgba(203, 213, 225, 0.84);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.76);
  color: #111827;
  padding: 22px 24px;
  text-align: left;
  cursor: pointer;
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.06);
  backdrop-filter: blur(12px);
  transition: transform 0.2s, border-color 0.2s, background 0.2s, box-shadow 0.2s;

  &:hover:not(:disabled),
  &.active {
    transform: translateY(-1px);
    border-color: rgba(37, 99, 235, 0.68);
    background: rgba(239, 246, 255, 0.84);
    box-shadow: 0 18px 42px rgba(37, 99, 235, 0.12);
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.58;
  }

  strong {
    font-size: 18px;
    line-height: 1.3;
  }

  small {
    max-width: 620px;
    color: #64748b;
    font-size: 14px;
    line-height: 1.6;
  }
}

.decision-card__badge {
  display: inline-flex;
  align-items: center;
  height: 24px;
  border-radius: 999px;
  background: #dcfce7;
  color: #15803d;
  padding: 0 10px;
  font-size: 12px;
  font-weight: 900;

  &.warn {
    background: #fef3c7;
    color: #b45309;
  }
}

.decision-footer {
  margin-top: auto;
  display: flex;
  align-items: center;
  gap: 12px;
  border-top: 1px solid #e5e7eb;
  padding-top: 14px;

  span {
    color: #64748b;
    font-size: 13px;
  }
}

.workspace-panel {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid rgba(226, 232, 240, 0.86);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.72);
  box-shadow: 0 16px 46px rgba(15, 23, 42, 0.08);
  backdrop-filter: blur(14px);
}

.workspace-panel__head {
  flex: 0 0 auto;
  min-height: 48px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 18px;
  border-bottom: 1px solid rgba(226, 232, 240, 0.86);
  background: rgba(255, 255, 255, 0.55);

  h2 {
    margin: 0;
    color: #111827;
    font-size: 15px;
  }

  span {
    color: #64748b;
    font-size: 13px;
    white-space: nowrap;
  }
}

.preview-head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.preview-source-badge {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  border-radius: 999px;
  background: #eff6ff;
  color: #1d4ed8 !important;
  padding: 0 9px;
  font-size: 11px !important;
  font-weight: 800;
}

.workspace-panel__body {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.pdf-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  background: rgba(226, 232, 240, 0.64);
  padding: 10px;
}

.pdf-inline-frame,
.image-inline-preview {
  width: 100%;
  flex: 1;
  min-height: 0;
  display: block;
  border: 0;
  background: rgba(248, 250, 252, 0.86);
  border-radius: 8px;
}

.pdf-preview-shell {
  position: relative;
  flex: 1;
  min-height: 0;
  overflow: hidden;
  border-radius: 8px;
  background: rgba(248, 250, 252, 0.86);
}

.pdf-preview-shell .pdf-inline-frame {
  height: 100%;
}

.pdf-preview-state {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  background: rgba(248, 250, 252, 0.94);
  color: #334155;
  padding: 24px;
  text-align: center;

  small {
    color: #64748b;
    line-height: 1.6;
  }

  &.failed {
    background: rgba(255, 251, 235, 0.96);

    strong {
      color: #92400e;
    }
  }
}

.pdf-preview-spinner {
  width: 30px;
  height: 30px;
  border: 3px solid rgba(37, 99, 235, 0.18);
  border-top-color: #2563eb;
  border-radius: 50%;
  animation: pdf-preview-spin 0.8s linear infinite;
}

@keyframes pdf-preview-spin {
  to {
    transform: rotate(360deg);
  }
}

.image-inline-preview {
  object-fit: contain;
}

.file-meta-card {
  flex: 0 0 auto;
  border: 1px solid rgba(203, 213, 225, 0.78);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.82);
  box-shadow: 0 8px 22px rgba(15, 23, 42, 0.06);
  backdrop-filter: blur(10px);
  padding: 10px 12px;

  strong,
  span {
    display: block;
  }

  strong {
    color: #111827;
    font-size: 13px;
    word-break: break-word;
  }

  span {
    margin-top: 3px;
    color: #64748b;
    font-size: 12px;
  }
}

.upload-empty,
.markdown-empty,
.preview-empty {
  height: 100%;
  min-height: 260px;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 8px;
  background: #f8fafc;
  color: #64748b;
  text-align: center;

  strong {
    color: #334155;
    font-size: 15px;
  }

  small {
    color: #94a3b8;
    font-size: 13px;
  }
}

.upload-empty {
  width: auto;
  height: calc(100% - 32px);
  margin: 16px;
  cursor: pointer;
  border: 1px dashed rgba(148, 163, 184, 0.72);
  background: rgba(255, 255, 255, 0.7);
  border-radius: 8px;
  transition: background 0.2s, border-color 0.2s;

  &.over,
  &:hover {
    background: rgba(239, 246, 255, 0.78);
    border-color: rgba(96, 165, 250, 0.82);
  }
}

.file-mark {
  width: 62px;
  height: 62px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: #fff;
  color: #2563eb;
  border: 1px solid #dbeafe;

  svg {
    width: 30px;
    height: 30px;
  }
}

.markdown-body {
  background: #0f172a;
}

.markdown-editor {
  width: 100%;
  height: 100%;
  min-height: 0;
  display: block;
  border: 0;
  resize: none;
  outline: 0;
  overflow: auto;
  padding: 18px 22px;
  background: #0f172a;
  color: #dbeafe;
  caret-color: #93c5fd;
  font-family: "Cascadia Code", Consolas, "SFMono-Regular", monospace;
  font-size: 14px;
  line-height: 1.72;
}

.markdown-editor::placeholder {
  color: #64748b;
}

.markdown-preview,
.pdf-html-preview {
  min-height: 100%;
  overflow-wrap: anywhere;
  background: #fff;
  color: #111827;
  padding: 28px 34px;
  font-size: 15px;
  line-height: 1.75;

  :deep(h1) {
    margin: 0 0 26px;
    color: #0f172a;
    font-size: 30px;
    line-height: 1.28;
  }

  :deep(h2) {
    margin: 28px 0 14px;
    color: #0f172a;
    font-size: 21px;
    padding-bottom: 8px;
    border-bottom: 1px solid #e5e7eb;
  }

  :deep(h3) {
    margin: 22px 0 10px;
    color: #1f2937;
    font-size: 17px;
  }

  :deep(p) {
    margin: 10px 0;
  }

  :deep(ul),
  :deep(ol) {
    margin: 12px 0;
    padding-left: 22px;
  }

  :deep(li) {
    margin: 5px 0;
  }

  :deep(code) {
    border-radius: 4px;
    background: #f1f5f9;
    padding: 2px 5px;
    font-family: "Cascadia Code", Consolas, monospace;
    font-size: 0.92em;
  }

  :deep(pre) {
    overflow: auto;
    border-radius: 8px;
    background: #0f172a;
    color: #e5e7eb;
    padding: 14px 16px;
  }

  :deep(pre code) {
    background: transparent;
    padding: 0;
    color: inherit;
  }

  :deep(table) {
    width: 100%;
    border-collapse: collapse;
    margin: 16px 0;
    font-size: 14px;
  }

  :deep(th),
  :deep(td) {
    border: 1px solid #e5e7eb;
    padding: 8px 10px;
  }

  :deep(th) {
    background: #f8fafc;
  }

  :deep(blockquote) {
    margin: 16px 0;
    border-left: 4px solid #bfdbfe;
    background: #eff6ff;
    padding: 10px 14px;
    color: #334155;
  }
}

.result-body {
  position: relative;
  background: rgba(248, 250, 252, 0.72);
  padding: 0;
}

.download-link {
  color: #2563eb;
  font-size: 13px;
  font-weight: 800;
  text-decoration: none;
}

.result-download-link {
  position: absolute;
  top: 12px;
  right: 16px;
  z-index: 1;
}

.result-pdf-frame {
  width: 100%;
  height: 100%;
  display: block;
  border: 0;
  background: #f8fafc;
}

.pdf-html-preview {
  height: 100%;
  overflow: auto;
  margin: 0;
}

.pdf-result-empty {
  height: 100%;
}

.pdf-result-empty {
  flex: 1;
  min-height: 220px;
}

.primary-btn,
.secondary-btn,
.dark-btn,
.danger-btn,
.ghost-btn {
  height: 36px;
  border: 1px solid transparent;
  border-radius: 8px;
  padding: 0 16px;
  font-weight: 800;
  cursor: pointer;
  white-space: nowrap;
  transition: transform 0.18s, box-shadow 0.18s, background 0.18s, border-color 0.18s;
}

.primary-btn:not(:disabled):hover,
.secondary-btn:not(:disabled):hover,
.dark-btn:not(:disabled):hover,
.danger-btn:not(:disabled):hover {
  transform: translateY(-1px);
}

.primary-btn {
  background: linear-gradient(135deg, #2563eb, #1d4ed8);
  color: #fff;
  box-shadow: 0 10px 24px rgba(37, 99, 235, 0.22);
}

.secondary-btn {
  border-color: rgba(203, 213, 225, 0.7);
  background: rgba(255, 255, 255, 0.72);
  color: #334155;
}

.dark-btn {
  background: linear-gradient(135deg, #1f2937, #111827);
  color: #fff;
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.18);
}

.danger-btn {
  background: linear-gradient(135deg, #dc2626, #b91c1c);
  color: #fff;
  box-shadow: 0 10px 24px rgba(220, 38, 38, 0.16);
}

.ghost-btn,
.text-btn {
  border: 0;
  background: transparent;
  color: #64748b;
  font-weight: 800;
  cursor: pointer;
}

button:disabled {
  opacity: 0.52;
  cursor: not-allowed;
}

@media (max-width: 1320px) {
  .workspace-topbar {
    grid-template-columns: 1fr;
  }

  .topbar-actions {
    justify-content: flex-start;
    padding-right: 0;
  }

  .status-pill {
    top: 16px;
    right: 16px;
  }

  .workflow-strip {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 1080px) {
  .upload-dialog :deep(.el-dialog),
  :deep(.upload-dialog.el-dialog),
  :global(.upload-dialog.el-dialog) {
    height: 96vh !important;
    max-height: 96vh !important;
  }

  .workspace-grid,
  .decision-layout {
    grid-template-columns: 1fr;
    overflow-y: auto;
    padding-right: 2px;
  }

  .workspace-panel {
    min-height: 420px;
  }

  .workflow-strip {
    grid-template-columns: 1fr;
    overflow-y: auto;
  }

  .topbar-actions {
    padding-top: 8px;
  }
}
</style>
