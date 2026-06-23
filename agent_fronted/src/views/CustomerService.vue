<template>
  <div class="customer-service" :class="{ 'dark': isDark }">
    <div class="chat-container">
      <ChatSidebar
          v-if="isSidebarOpen"
          :chat-history="chatHistory"
          :current-chat-id="currentChatId"
          :history-loading="historyLoading"
          :history-error="historyError"
          :history-has-more="historyHasMore"
          :history-search-query="historySearchQuery"
          @select-chat="loadChat"
          @new-chat="startNewChat"
          @rename-chat="handleRenameChat"
          @delete-chat="handleDeleteChat"
          @load-more-history="loadMoreChatHistory"
          @update-history-search="setHistorySearchQuery"
      />

      <button class="sidebar-toggle" @click="toggleSidebar">
        <span class="toggle-icon" :class="{ open: isSidebarOpen }">⯈</span>
      </button>

      <div class="chat-main">

        <!-- 身份化提问模板 -->
        <div
          class="question-templates"
          :class="{ collapsed: areTemplatesCollapsed }"
          v-if="templateGroups.length"
        >
          <div class="qt-header">
            <div class="qt-heading">
              <div class="qt-title">快速提问</div>
            </div>
            <button
              type="button"
              class="qt-toggle"
              @click="toggleTemplatesCollapsed"
              :aria-expanded="!areTemplatesCollapsed"
            >
              <span>{{ areTemplatesCollapsed ? '展开' : '收起' }}</span>
              <ChevronDownIcon
                class="qt-toggle-icon"
                :class="{ open: !areTemplatesCollapsed }"
              />
            </button>
          </div>
          <Transition name="quick-section">
            <div
              v-show="!areTemplatesCollapsed"
              class="template-list"
              :class="{ 'all-expanded': areAllTemplateGroupsExpanded }"
              :style="templateListStyle"
            >
              <div
                class="template-card"
                :class="{ expanded: isGroupExpanded(index), collapsed: !isGroupExpanded(index) }"
                v-for="(group, index) in templateGroups"
                :key="group.title"
                ref="templateCardRefs"
              >
                <button
                  type="button"
                  class="template-title"
                  :class="{ active: isGroupExpanded(index) }"
                  @click="toggleGroup(index)"
                  :aria-expanded="isGroupExpanded(index)"
                >
                  <span class="template-index">{{ index + 1 }}.</span>
                  <span class="template-text">{{ group.title }}</span>
                  <ChevronDownIcon
                    class="chevron"
                    :class="{ open: isGroupExpanded(index) }"
                  />
                </button>
                <ul
                  class="sub-questions"
                  :class="{ collapsed: !isGroupExpanded(index) }"
                  :aria-hidden="!isGroupExpanded(index)"
                >
                  <li
                    v-for="(sub, subIndex) in group.subQuestions"
                    :key="subIndex"
                    class="sub-chip"
                    @click="useTemplate(sub)"
                  >
                    <span class="chip-text">{{ sub }}</span>
                  </li>
                </ul>
              </div>
            </div>
          </Transition>
        </div>

        <div class="messages" ref="messagesRef" @scroll.passive="handleMessagesScroll">
          <ChatMessage
              v-for="(message, index) in currentMessages"
              :key="index"
              :message="message"
              :is-stream="isStreaming && index === currentMessages.length - 1"
              :can-edit="message.role === 'user' && !message.voiceSessionActive && !isStreaming"
              @edit-user-message="handleEditUserMessage(index, $event)"
          />
        </div>

        <button
          v-if="showScrollToLatest"
          class="scroll-to-latest"
          @click="resumeAutoFollow"
        >
          {{ scrollToLatestLabel }}
        </button>

        <div class="input-area">
          <textarea
              v-model="userInput"
              @keydown.enter.prevent="handleTextSend"
              placeholder="请输入您的问题..."
              rows="1"
              ref="inputRef"
          ></textarea>

          <!-- 语音：麦克风按钮（位于发送按钮左侧，支持点击开始/停止，监听态脉冲） -->
          <button
              class="microphone-button"
              :class="{ listening: voiceIsListening }"
              :disabled="!isVoiceQuestionRecording && (isStreaming || isVoiceSpeaking || voiceProcessing)"
              @click="handleVoiceClick"
              :aria-pressed="voiceIsListening ? 'true' : 'false'"
              :title="voiceButtonTitle"
          >
            <MicrophoneIcon class="icon" />
          </button>

          <button
              class="stream-tts-button"
              :class="{ active: isTextStreamTtsEnabled, playing: isStreamingTtsActive }"
              @click="toggleTextStreamTts"
              :aria-pressed="isTextStreamTtsEnabled ? 'true' : 'false'"
              :title="textStreamTtsButtonTitle"
          >
            <SpeakerWaveIcon v-if="isTextStreamTtsEnabled" class="icon" />
            <SpeakerXMarkIcon v-else class="icon" />
          </button>

          <button
              v-if="isStreaming"
              class="stop-button"
              @click="handleStopStreaming"
              :disabled="isStopping"
              :title="isStopping ? '正在停止生成' : '停止生成'"
          >
            <StopCircleIcon class="icon" />
          </button>
          <button
              v-else
              class="send-button"
              @click="handleTextSend"
              :disabled="!userInput.trim()"
          >
            <PaperAirplaneIcon class="icon" />
          </button>
        </div>
      </div>

      <aside
        v-if="shouldShowWorkflowTaskSidebar"
        class="workflow-task-sidebar"
        aria-label="Agent 任务清单"
      >
        <div class="workflow-task-sidebar__header">
          <div>
            <div class="workflow-task-sidebar__eyebrow">Workflow</div>
            <h2 class="workflow-task-sidebar__title">任务清单</h2>
          </div>
          <span class="workflow-task-sidebar__badge">
            {{ workflowTaskBadgeText }}
          </span>
        </div>
        <TaskPanel
          v-if="workflowSidebarTaskSnapshot"
          :task-snapshot="workflowSidebarTaskSnapshot"
        />
        <div v-else class="workflow-task-sidebar__empty">
          等待任务
        </div>
      </aside>

    </div>

    <!-- 预约成功弹窗 -->
    <div v-if="showBookingModal" class="booking-modal">
      <div class="modal-content">
        <h3>预约成功！</h3>
        <div class="booking-info" v-html="bookingInfo"></div>
        <button @click="showBookingModal = false">确定</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { useDark } from '@vueuse/core'
import { marked } from 'marked'
import { ElMessage } from 'element-plus'
import {
  PaperAirplaneIcon,
  MicrophoneIcon,
  StopCircleIcon,
  ChevronDownIcon,
  SpeakerWaveIcon,
  SpeakerXMarkIcon
} from '@heroicons/vue/24/outline'
import ChatMessage from '../components/ChatMessage.vue'
import ChatSidebar from '../components/ChatSidebar.vue'
import TaskPanel from '../components/TaskPanel.vue'
import { questionTemplates } from '@/config/questionTemplates'
import { useUserIdentityStore } from '@/stores/userIdentity'
import { useTodosPanel } from '@/composables/useTodosPanel'
import { useChatStream } from '@/composables/useChatStream'
import { useStreamingTtsQueue } from '@/composables/useStreamingTtsQueue'
import { useVoiceGatewaySession, type VoiceGatewayEvent } from '@/composables/useVoiceGatewaySession'
import {
  consumeDesktopPetVoiceAutoStart,
  consumePendingDesktopPetMessage,
  DESKTOP_PET_MESSAGE_EVENT,
  DESKTOP_PET_VOICE_AUTOSTART_EVENT,
  type DesktopPetVoiceAutoStartPayload,
  type DesktopPetPayload
} from '@/composables/useDesktopPetBridge'
import { PcmRecorder } from '@/utils/pcmAudio'
import {
  DEFAULT_AUTO_FOLLOW_THRESHOLD,
  isNearBottomPosition,
  shouldAutoScrollOnUpdate,
  shouldKeepAutoFollowOnScroll
} from '@/utils/chatScrollStrategy.js'
import { hasVisibleTaskSnapshot } from '@/utils/taskState'

// 引入外部CSS文件
import '@/assets/CustomerService.css'

const isDark = useDark()
const isSidebarOpen = ref(false)
const messagesRef = ref<HTMLElement | null>(null)
const inputRef = ref<HTMLTextAreaElement | null>(null)
const userInput = ref('')
const currentChatId = ref<string | number | null>(null)
const chatHistory = ref([])

const toggleSidebar = () => {
  isSidebarOpen.value = !isSidebarOpen.value
}
const showBookingModal = ref(false)
const bookingInfo = ref('')
const userIdentityStore = useUserIdentityStore()
const expandedIndices = ref<number[]>([0, 1, 2, 3])
const areTemplatesCollapsed = ref(false)
const templateCardRefs = ref<HTMLElement[]>([])
const expandedTemplateCardHeight = ref(0)
const autoFollowEnabled = ref(true)
const hasPendingContentBelow = ref(false)
const isProgrammaticScroll = ref(false)

const AUTO_FOLLOW_THRESHOLD_PX = DEFAULT_AUTO_FOLLOW_THRESHOLD
let scrollReleaseTimer: ReturnType<typeof window.setTimeout> | null = null
let scheduledScrollFrame: number | null = null

const cancelScheduledScroll = () => {
  if (scheduledScrollFrame !== null) {
    window.cancelAnimationFrame(scheduledScrollFrame)
    scheduledScrollFrame = null
  }
}

const {
  assignTodosState,
  fetchTodosForThread,
  extractTodosFromToolResult
} = useTodosPanel(currentChatId)

const {
  sessionState: voiceSessionState,
  isSpeaking: isVoiceSpeaking,
  isPlayerPlaying,
  currentThreadId: voiceCurrentThreadId,
  connectionStatus: voiceConnectionStatus,
  connect: connectVoiceGateway,
  createSession: createVoiceSession,
  sendAudioChunk,
  sendSpeechEnd,
  sendAiStart,
  sendAiEnd,
  sendStop,
  setGatewayTtsPlaybackEnabled,
  setOnPlaybackFinished,
  stopPlayback: stopVoicePlayback,
  preparePlayback,
  onVoiceEvent
} = useVoiceGatewaySession()

const isVoiceQuestionRecording = ref(false)
const isDesktopPetVoiceAutoStarting = ref(false)
let voiceQuestionRecorder: PcmRecorder | null = null
let disposeVoiceGatewayEvent: (() => void) | null = null
let hasActiveVoiceTurn = false
let hasConsumedDesktopPetVoiceStart = false
let consumedDesktopPetVoiceStartId = ''
let desktopPetVoiceStartTimer: ReturnType<typeof window.setTimeout> | null = null
let hasDetectedVoiceQuestionSpeech = false
let voiceQuestionLastSpeechAt = 0
let isVoiceQuestionAutoStopping = false
let hasAppendedVoiceQuestion = false
let isVoiceTurnCancelledForText = false
let aiStartSent = false
let pendingAiEnd = false

const VOICE_AUTO_SEND_SILENCE_MS = 2500
const VOICE_SPEECH_RMS_THRESHOLD = 0.012
const TEXT_STREAM_TTS_STORAGE_KEY = 'fd_text_stream_tts_enabled'

const readTextStreamTtsEnabled = () => {
  try {
    return window.localStorage.getItem(TEXT_STREAM_TTS_STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

const saveTextStreamTtsEnabled = (enabled: boolean) => {
  try {
    window.localStorage.setItem(TEXT_STREAM_TTS_STORAGE_KEY, enabled ? '1' : '0')
  } catch {
    // 浏览器禁用本地存储时，播报开关仍在当前页面内生效。
  }
}

const isTextStreamTtsEnabled = ref(readTextStreamTtsEnabled())
const {
  isActive: isStreamingTtsActive,
  lastError: streamingTtsError,
  start: startStreamingTts,
  append: appendStreamingTtsToken,
  finish: finishStreamingTts,
  stop: stopStreamingTts,
  prepare: prepareStreamingTts
} = useStreamingTtsQueue()

const beginAssistantStreamTts = () => {
  stopStreamingTts()
  if (isTextStreamTtsEnabled.value) {
    startStreamingTts()
  }
}

const toggleTextStreamTts = async () => {
  const enabled = !isTextStreamTtsEnabled.value
  isTextStreamTtsEnabled.value = enabled
  saveTextStreamTtsEnabled(enabled)

  if (!enabled) {
    stopStreamingTts()
    return
  }

  try {
    await prepareStreamingTts()
  } catch (error) {
    isTextStreamTtsEnabled.value = false
    saveTextStreamTtsEnabled(false)
    console.error('启用流式播报失败:', error)
    ElMessage.error('当前浏览器无法启用流式播报')
  }
}

const textStreamTtsButtonTitle = computed(() => (
  isTextStreamTtsEnabled.value ? '关闭文字回复流式播报' : '开启文字回复流式播报'
))

watch(streamingTtsError, (message) => {
  if (message) {
    ElMessage.warning(message)
  }
})

setOnPlaybackFinished(() => {
  aiStartSent = false
  if (pendingAiEnd) {
    pendingAiEnd = false
    sendAiEnd()
  }
  if (hasActiveVoiceTurn) {
    hasActiveVoiceTurn = false
    void finishVoicePlayback(voiceCurrentThreadId.value)
  }
})

watch(voiceConnectionStatus, (status) => {
  if (status === 'disconnected' || status === 'error') {
    hasActiveVoiceTurn = false
    aiStartSent = false
    pendingAiEnd = false
  }
})

const voiceIsListening = computed(() => isVoiceQuestionRecording.value || isDesktopPetVoiceAutoStarting.value)
const voiceProcessing = computed(() => hasActiveVoiceTurn && (
  voiceSessionState.value === 'thinking'
  || voiceSessionState.value === 'speaking'
))
const voiceButtonTitle = computed(() => {
  if (isDesktopPetVoiceAutoStarting.value) return '正在启动语音提问'
  if (isVoiceQuestionRecording.value) return '结束语音提问'
  if (voiceProcessing.value) return '语音处理中'
  if (isVoiceSpeaking.value || voiceSessionState.value === 'speaking') return '正在播放语音回复'
  if (isStreaming.value) return '当前正在生成回复'
  return '开始语音提问'
})

// 根据身份选择对应模板
const identityKey = computed(() => {
  const rawRole = userIdentityStore.userRole || ''
  const rawUserId = userIdentityStore.userId || ''
  const normalizedRole = rawRole.toString().trim().toLowerCase()
  const visitorKeywords = ['guest', 'visitor', '访客', '游客']
  if (!normalizedRole && rawUserId.toString().toLowerCase() === 'guest') return 'tourist'
  if (visitorKeywords.some(k => normalizedRole.includes(k))) return 'tourist'
  return normalizedRole ? 'admin' : 'tourist'
})

// @ts-ignore - 保留以备将来使用
const identityLabel = computed(() => identityKey.value === 'tourist' ? '游客' : '管理员')
const templateGroups = computed(() => questionTemplates[identityKey.value])
const allTemplateGroupIndices = computed(() => templateGroups.value.map((_, index) => index))
const areAllTemplateGroupsExpanded = computed(() => {
  return allTemplateGroupIndices.value.length > 0
    && expandedIndices.value.length === allTemplateGroupIndices.value.length
    && allTemplateGroupIndices.value.every(index => expandedIndices.value.includes(index))
})
const templateListStyle = computed(() => expandedTemplateCardHeight.value
  ? { '--quick-card-expanded-height': `${expandedTemplateCardHeight.value}px` }
  : {}
)

const isGroupExpanded = (index: number) => expandedIndices.value.includes(index)

const updateExpandedTemplateCardHeight = () => {
  const cards = templateCardRefs.value || []
  const maxHeight = Math.max(0, ...cards.map(card => card.offsetHeight))
  if (maxHeight > 0) {
    expandedTemplateCardHeight.value = Math.ceil(maxHeight)
  }
}

const refreshExpandedTemplateCardHeight = async () => {
  await nextTick()
  updateExpandedTemplateCardHeight()
}

const expandAllTemplateGroups = () => {
  expandedIndices.value = [...allTemplateGroupIndices.value]
  refreshExpandedTemplateCardHeight()
}

const toggleTemplatesCollapsed = () => {
  areTemplatesCollapsed.value = !areTemplatesCollapsed.value
  if (!areTemplatesCollapsed.value) {
    expandAllTemplateGroups()
  }
}

const toggleGroup = (index: number) => {
  const existingIndex = expandedIndices.value.indexOf(index)
  if (areAllTemplateGroupsExpanded.value) {
    updateExpandedTemplateCardHeight()
  }

  if (existingIndex > -1) {
    expandedIndices.value = expandedIndices.value.filter(item => item !== index)
  } else {
    expandedIndices.value = [...expandedIndices.value, index].sort((a, b) => a - b)
    refreshExpandedTemplateCardHeight()
  }
}

const useTemplate = (content: string) => {
  areTemplatesCollapsed.value = true
  expandedIndices.value = []
  sendMessage(content)
}

// 配置 marked
marked.setOptions({
  breaks: true,  // 支持换行
  gfm: true     // 支持 GitHub Flavored Markdown
  // sanitize 选项在新版 marked 中已移除
})

// 自动调整输入框高度
const adjustTextareaHeight = () => {
  const textarea = inputRef.value
  if (textarea) {
    textarea.style.height = 'auto'
    textarea.style.height = textarea.scrollHeight + 'px'
  }
}

// 滚动到底部
const isNearBottom = (element: HTMLElement | null = messagesRef.value) => {
  return isNearBottomPosition(element || undefined, AUTO_FOLLOW_THRESHOLD_PX)
}

const syncAutoFollowState = () => {
  const nearBottom = isNearBottom()
  if (nearBottom) {
    autoFollowEnabled.value = true
    hasPendingContentBelow.value = false
    return
  }

  if (!shouldKeepAutoFollowOnScroll({
    nearBottom,
    isProgrammaticScroll: isProgrammaticScroll.value
  })) {
    cancelScheduledScroll()
    autoFollowEnabled.value = false
  }
}

const markProgrammaticScroll = () => {
  isProgrammaticScroll.value = true
  if (scrollReleaseTimer) {
    window.clearTimeout(scrollReleaseTimer)
  }
  scrollReleaseTimer = window.setTimeout(() => {
    isProgrammaticScroll.value = false
    syncAutoFollowState()
  }, 120)
}

const handleMessagesScroll = () => {
  syncAutoFollowState()
}

const scrollToBottom = async ({
  force = false,
  behavior = 'auto',
  markNewContent = true
}: {
  force?: boolean
  behavior?: ScrollBehavior
  markNewContent?: boolean
} = {}) => {
  await nextTick()
  const container = messagesRef.value
  if (!container) {
    return false
  }

  if (!shouldAutoScrollOnUpdate({
    force,
    autoFollowEnabled: autoFollowEnabled.value,
    nearBottom: isNearBottom(container)
  })) {
    if (markNewContent) {
      hasPendingContentBelow.value = true
    }
    return false
  }

  if (scheduledScrollFrame !== null) {
    cancelScheduledScroll()
  }

  return await new Promise<boolean>((resolve) => {
    scheduledScrollFrame = window.requestAnimationFrame(() => {
      scheduledScrollFrame = null
      const activeContainer = messagesRef.value
      if (!activeContainer) {
        resolve(false)
        return
      }

      markProgrammaticScroll()
      activeContainer.scrollTo({
        top: activeContainer.scrollHeight,
        behavior
      })
      autoFollowEnabled.value = true
      hasPendingContentBelow.value = false
      resolve(true)
    })
  })
}

const showScrollToLatest = computed(() => !autoFollowEnabled.value && hasPendingContentBelow.value)
const scrollToLatestLabel = computed(() => isStreaming.value ? '有新内容，点击回到底部' : '回到底部')

const resumeAutoFollow = async () => {
  autoFollowEnabled.value = true
  hasPendingContentBelow.value = false
  await scrollToBottom({ force: true, behavior: 'smooth', markNewContent: false })
}

const applyPdfQuestionDraft = async (event: Event) => {
  const detail = (event as CustomEvent<{ question?: string }>).detail
  const question = detail?.question?.trim()
  if (!question) return
  userInput.value = question
  await nextTick()
  adjustTextareaHeight()
  inputRef.value?.focus()
  ElMessage.success('已填入 PDF 推荐问题，可直接发送。')
}

const {
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
  updateVoiceAssistantStatus,
  appendVoiceAssistantToken,
  completeVoiceAssistantMessage,
  finishVoicePlayback,
  discardActiveVoiceTurn,
  applyVoiceVisualActions
} = useChatStream({
  currentChatId,
  chatHistory,
  userInput,
  assignTodosState,
  extractTodosFromToolResult,
  fetchTodosForThread,
  userIdentityStore,
  adjustTextareaHeight,
  scrollToBottom,
  onSendMessage: () => {
    beginAssistantStreamTts()
    expandedIndices.value = []
    areTemplatesCollapsed.value = true
  },
  onAssistantToken: appendStreamingTtsToken,
  onAssistantComplete: finishStreamingTts
})

const activeWorkflowTaskSnapshot = computed(() => {
  for (let index = currentMessages.value.length - 1; index >= 0; index -= 1) {
    const message = currentMessages.value[index]
    if (message?.role !== 'assistant') {
      continue
    }
    const snapshot = message?.taskSnapshot
    return hasVisibleTaskSnapshot(snapshot) ? snapshot : null
  }
  return null
})

const workflowSidebarTaskSnapshot = computed(() => {
  if (activeWorkflowTaskSnapshot.value) {
    return activeWorkflowTaskSnapshot.value
  }
  if (isStreaming.value || voiceProcessing.value) {
    return {
      todos: [],
      summary: {
        total: 0,
        pending: 0,
        in_progress: 0,
        completed: 0,
        interrupted: 0
      },
      isLoading: true,
      statusHint: '规划中',
      lifecycleState: 'active',
      updatedAt: new Date().toISOString()
    }
  }
  return null
})

const shouldShowWorkflowTaskSidebar = computed(() => {
  if (!isStreaming.value && !voiceProcessing.value) {
    return false
  }
  const snapshot = workflowSidebarTaskSnapshot.value
  if (!snapshot) {
    return false
  }
  if (snapshot.isLoading) {
    return true
  }
  const summary = snapshot.summary
  const hasActiveTodo = Array.isArray(snapshot.todos)
    && snapshot.todos.some((todo: Record<string, any>) => (
      ['pending', 'in_progress'].includes(todo?.status)
    ))
  return Boolean(summary?.pending || summary?.in_progress || hasActiveTodo)
})

const workflowTaskBadgeText = computed(() => {
  const snapshot = workflowSidebarTaskSnapshot.value
  const summary = snapshot?.summary
  if (snapshot?.isLoading && !summary?.total) return '规划中'
  if (!summary?.total) return '执行中'
  if (summary.interrupted) return '已停止'
  if (summary.pending + summary.in_progress === 0) return '完成'
  return '执行中'
})

const handleRenameChat = async ({ id, title }: { id: string; title: string }) => {
  try {
    await renameChatHistoryItem(id, title)
    ElMessage.success('咨询记录已重命名')
  } catch (error) {
    console.error('重命名咨询记录失败:', error)
    ElMessage.error('重命名失败，请稍后重试')
  }
}

const handleDeleteChat = async (id: string) => {
  try {
    await deleteChatHistoryItem(id)
    ElMessage.success('咨询记录已删除')
  } catch (error) {
    console.error('删除咨询记录失败:', error)
    ElMessage.error('删除失败，请稍后重试')
  }
}

const getVoicePayloadThreadId = (payload: Record<string, any>) => (
  typeof payload.thread_id === 'string' ? payload.thread_id : null
)

const getVoicePayloadText = (payload: Record<string, any>) => (
  String(payload.content || payload.text || payload.final_content || payload.message || '')
)

const appendVoiceQuestionOnce = async (content: string, threadId?: string | null) => {
  const messageContent = content.trim()
  if (!messageContent) return
  if (hasAppendedVoiceQuestion) return
  hasAppendedVoiceQuestion = true
  activateVoiceTurn()
  await appendVoiceUserMessage(messageContent, threadId)
  await updateVoiceAssistantStatus('reasoning', '正在思考...', threadId)
}

const activateVoiceTurn = () => {
  if (hasActiveVoiceTurn) return
  hasActiveVoiceTurn = true
}

const finishGatewayVoicePlayback = async (threadId?: string | null) => {
  if (!isPlayerPlaying.value) {
    await finishVoicePlayback(threadId)
    hasActiveVoiceTurn = false
    if (pendingAiEnd) {
      pendingAiEnd = false
      sendAiEnd()
    }
    return
  }

  pendingAiEnd = true
}

const handleVoiceGatewayEvent = async (event: VoiceGatewayEvent) => {
  if (isVoiceTurnCancelledForText && event.type !== 'status') return

  const payload = event.payload as Record<string, any>
  const threadId = getVoicePayloadThreadId(payload)

  switch (event.type) {
    case 'state_change':
      if (payload.state === 'thinking' && hasActiveVoiceTurn) {
        await updateVoiceAssistantStatus('reasoning', '正在思考...', threadId)
      } else if (payload.state === 'speaking') {
        activateVoiceTurn()
        await updateVoiceAssistantStatus('streaming', '正在播放语音回复...', threadId)
      }
      break

    case 'asr_result': {
      const text = getVoicePayloadText(payload).trim()
      if (!text) return
      await appendVoiceQuestionOnce(text, threadId)
      break
    }

    case 'asr_empty':
      hasActiveVoiceTurn = false
      ElMessage.info('未识别到有效语音，请重试')
      break

    case 'asr_error':
      hasActiveVoiceTurn = false
      ElMessage.error(getVoicePayloadText(payload) || '语音识别失败，请重试')
      break

    case 'visual_actions':
      if (!hasActiveVoiceTurn) hasActiveVoiceTurn = true
      await applyVoiceVisualActions(Array.isArray(payload.actions) ? payload.actions : [], threadId)
      break

    case 'agent_token':
      if (!hasActiveVoiceTurn) {
        console.log('[Voice] agent_token: auto-activating turn')
        hasActiveVoiceTurn = true
      }
      const tokenText = getVoicePayloadText(payload)
      console.log('[Voice] agent_token:', JSON.stringify(tokenText).slice(0, 100))
      await appendVoiceAssistantToken(tokenText, threadId)
      break

    case 'agent_complete':
    case 'complete':
      if (!hasActiveVoiceTurn) {
        console.log('[Voice] agent_complete: auto-activating turn')
        hasActiveVoiceTurn = true
      }
      const completeDisplayText = payload.grounded_final_content || getVoicePayloadText(payload)
      if (completeDisplayText) {
        console.log('[Voice] agent_complete text_len:', completeDisplayText.length, 'grounded:', !!payload.grounded_final_content, 'payload_text:', getVoicePayloadText(payload).length)
        await completeVoiceAssistantMessage({
          thread_id: threadId,
          content: completeDisplayText,
          grounded_final_content: payload.grounded_final_content || completeDisplayText,
          todos: payload.todos,
          evidences: payload.evidences,
          normalized_evidences: payload.normalized_evidences,
          findings: payload.findings,
          finding_links: payload.finding_links,
          workflow_stages: payload.workflow_stages,
          current_stage: payload.current_stage,
          workflow_stage_details: payload.workflow_stage_details,
          tool_lifecycle_ledger: payload.tool_lifecycle_ledger,
          evidence_count: payload.evidence_count,
          evidence_quality: payload.evidence_quality,
          evidence_coverage: payload.evidence_coverage,
          report_gate: payload.report_gate,
          report_filename: payload.report_filename,
          report_url: payload.report_url,
          report_artifact: payload.report_artifact,
          sql_artifact: payload.sql_artifact,
          knowledge_artifact: payload.knowledge_artifact,
          analysis_artifact: payload.analysis_artifact,
          artifact: payload.artifact,
          quality_gate_notice: payload.quality_gate_notice,
          release_ready: payload.release_ready,
          workflow_result: payload.workflow_result,
          scenario_result: payload.scenario_result,
          artifacts: payload.artifacts,
          timeline: payload.timeline,
          governance: payload.governance,
        })
      } else {
        await updateVoiceAssistantStatus('streaming', '正在播放语音回复...', threadId)
      }
      break

    case 'agent_fallback':
      if (!hasActiveVoiceTurn) return
      await appendVoiceAssistantToken(getVoicePayloadText(payload) || '抱歉，当前服务暂时不可用，请稍后再试。', threadId)
      await updateVoiceAssistantStatus('streaming', '正在播放语音回复...', threadId)
      break

    case 'agent_tool_start':
      if (!hasActiveVoiceTurn) hasActiveVoiceTurn = true
      await applyVoiceVisualActions([{
        type: 'tool_start',
        tool: payload.tool || '',
        input: payload.input || '',
        run_id: payload.run_id || '',
        stage: payload.stage || '',
        current_stage: payload.current_stage || '',
      }], threadId)
      break

    case 'agent_tool_end':
      if (!hasActiveVoiceTurn) hasActiveVoiceTurn = true
      await applyVoiceVisualActions([{
        type: 'tool_end',
        tool: payload.tool || '',
        result_preview: payload.result_preview || payload.result || '',
        result: payload.result || '',
        evidence: payload.evidence || [],
        evidence_count: payload.evidence_count || 0,
        truncated: Boolean(payload.truncated),
        stage: payload.stage || '',
        current_stage: payload.current_stage || '',
        stage_duration_ms: payload.stage_duration_ms || 0,
        action_guard: payload.action_guard || null,
      }], threadId)
      break

    case 'tts_audio':
    case 'tts_audio_chunk':
      activateVoiceTurn()
      await updateVoiceAssistantStatus('streaming', '正在播放语音回复...', threadId)
      if (!aiStartSent) {
        aiStartSent = true
        sendAiStart()
      }
      break

    case 'tts_end':
    case 'tts_playback_end':
      await finishGatewayVoicePlayback(threadId)
      break

    case 'interaction_event': {
      const evtSegments = Array.isArray(payload.segments) ? payload.segments : []
      const evtEvents = Array.isArray(payload.events) ? payload.events : []
      const evtText = String(payload.text || '').trim()
      const isInterrupt = payload.source === 'fast_interrupt' ||
        evtSegments.some((s: Record<string, any>) => s.event === 'interrupt')

      if (isInterrupt) {
        stopVoicePlayback()
        pendingAiEnd = false
        aiStartSent = false
        if (evtText) {
          await appendVoiceUserMessage(evtText, threadId)
        }
        ElMessage.info('检测到打断，AI 已停止说话')
      } else if (evtText) {
        await appendVoiceQuestionOnce(evtText, threadId)
      } else {
        const hasOverlap = evtEvents.some(
          (e: Record<string, any>) => e.type === 'overlap' || e.action === 'OVERLAP_DETECTED_ONLY'
        )
        if (hasOverlap) {
          ElMessage.info('检测到多人同时说话')
        }
      }
      break
    }

    case 'status':
      break

    default:
      break
  }
}

const resetVoiceQuestionSilenceState = () => {
  hasDetectedVoiceQuestionSpeech = false
  voiceQuestionLastSpeechAt = 0
  isVoiceQuestionAutoStopping = false
}

const calculatePcmRms = (buffer: ArrayBuffer) => {
  const samples = new Int16Array(buffer)
  if (!samples.length) return 0

  let sumSquares = 0
  for (let index = 0; index < samples.length; index += 1) {
    const normalized = (samples[index] ?? 0) / 32768
    sumSquares += normalized * normalized
  }

  return Math.sqrt(sumSquares / samples.length)
}

const trackVoiceQuestionAudioActivity = (buffer: ArrayBuffer) => {
  if (!isVoiceQuestionRecording.value || isVoiceQuestionAutoStopping) return

  const now = Date.now()
  const rms = calculatePcmRms(buffer)
  if (rms >= VOICE_SPEECH_RMS_THRESHOLD) {
    hasDetectedVoiceQuestionSpeech = true
    voiceQuestionLastSpeechAt = now
    return
  }

  if (!hasDetectedVoiceQuestionSpeech || !voiceQuestionLastSpeechAt) return
  if (now - voiceQuestionLastSpeechAt < VOICE_AUTO_SEND_SILENCE_MS) return

  isVoiceQuestionAutoStopping = true
  void stopVoiceQuestionRecording()
}

const stopVoiceQuestionRecording = async (shouldSendSpeechEnd = true) => {
  const activeRecorder = voiceQuestionRecorder
  voiceQuestionRecorder = null
  isVoiceQuestionRecording.value = false

  if (activeRecorder) {
    await activeRecorder.stop()
  }
  resetVoiceQuestionSilenceState()

  if (!shouldSendSpeechEnd) return

  try {
    sendSpeechEnd()
  } catch (error) {
    console.error('发送语音结束标记失败:', error)
    ElMessage.error((error as Error).message || '语音网关未连接')
  }
}

const isVoiceTurnActive = () => (
  isVoiceQuestionRecording.value
  || hasActiveVoiceTurn
  || isVoiceSpeaking.value
  || isPlayerPlaying.value
  || voiceSessionState.value === 'thinking'
  || voiceSessionState.value === 'speaking'
)

const resetVoiceTurnRuntimeState = () => {
  hasActiveVoiceTurn = false
  hasAppendedVoiceQuestion = false
  pendingAiEnd = false
  aiStartSent = false
}

const stopActiveVoiceTurn = async () => {
  if (!isVoiceTurnActive()) return
  isVoiceTurnCancelledForText = true
  await stopVoiceQuestionRecording(false)
  stopVoicePlayback()
  await discardActiveVoiceTurn()
  resetVoiceTurnRuntimeState()

  try {
    sendStop()
  } catch (error) {
    console.warn('停止语音网关会话失败:', error)
  }
}

const handleStopStreaming = async () => {
  stopStreamingTts()
  await stopStreaming()
}

const handleTextSend = async () => {
  if (!userInput.value.trim()) return
  stopStreamingTts()
  if (isVoiceTurnActive()) {
    await stopActiveVoiceTurn()
  }
  await sendMessage()
}

const handleEditUserMessage = async (index: number, payload: { content?: string }) => {
  const nextContent = String(payload?.content || '').trim()
  if (!nextContent) return
  stopStreamingTts()
  if (isVoiceTurnActive()) {
    await stopActiveVoiceTurn()
  }

  try {
    await editUserMessage(index, nextContent)
  } catch (error) {
    console.error('编辑用户消息失败:', error)
    ElMessage.error((error as Error).message || '编辑失败，请稍后重试')
  }
}

const startVoiceQuestionRecording = async () => {
  if (isVoiceQuestionRecording.value) return
  if (isStreaming.value) {
    ElMessage.warning('当前正在生成回复，请稍后再试')
    return
  }
  if (isVoiceSpeaking.value || voiceSessionState.value === 'speaking') {
    ElMessage.warning('正在播放语音回复，请稍后再提问')
    return
  }
  if (hasActiveVoiceTurn) {
    ElMessage.warning('语音回复处理中，请稍后再提问')
    return
  }

  let hasRequestedMicrophone = false

  try {
    hasAppendedVoiceQuestion = false
    isVoiceTurnCancelledForText = false
    pendingAiEnd = false
    aiStartSent = false
    resetVoiceQuestionSilenceState()
    setGatewayTtsPlaybackEnabled(true)
    await connectVoiceGateway()
    await createVoiceSession(
      userIdentityStore.userId || 'web_user',
      false,
      typeof currentChatId.value === 'string' ? currentChatId.value : undefined
    )
    await preparePlayback()
    console.log('[Voice] AudioContext 预热完成')
    voiceQuestionRecorder = new PcmRecorder({
      onChunk: chunk => {
        try {
          trackVoiceQuestionAudioActivity(chunk.buffer)
          sendAudioChunk(chunk.base64)
        } catch (error) {
          console.error('发送语音片段失败:', error)
          void stopVoiceQuestionRecording(false)
        }
      }
    })
    hasRequestedMicrophone = true
    await voiceQuestionRecorder.start()
    isVoiceQuestionRecording.value = true
    ElMessage.info('正在聆听，请说出您的问题')
  } catch (error) {
    console.error('语音提问启动失败:', error)
    console.info('[Voice] 启动失败阶段:', hasRequestedMicrophone ? 'microphone' : 'gateway')
    await stopVoiceQuestionRecording(false)
    ElMessage.error('无法访问麦克风，请检查浏览器权限设置。')
  }
}

const handleVoiceClick = () => {
  if (isDesktopPetVoiceAutoStarting.value) return
  if (isVoiceQuestionRecording.value) {
    void stopVoiceQuestionRecording()
    return
  }
  void startVoiceQuestionRecording()
}

const getDesktopPetVoiceStartId = (payload: DesktopPetVoiceAutoStartPayload) => {
  const receivedAt = Number(payload.received_at || 0)
  return `${payload.reason || 'desktop_pet_launch'}:${receivedAt || 'unknown'}`
}

const isDesktopPetVoiceAutoStartBlocked = () => {
  if (isVoiceQuestionRecording.value) return true

  if (isStreaming.value) {
    ElMessage.warning('当前正在生成回复，请稍后再试')
    return true
  }

  if (
    isVoiceSpeaking.value
    || isPlayerPlaying.value
    || voiceSessionState.value === 'speaking'
  ) {
    ElMessage.warning('正在播放语音回复，请稍后再提问')
    return true
  }

  if (hasActiveVoiceTurn) {
    ElMessage.warning('语音回复处理中，请稍后再提问')
    return true
  }

  return false
}

const startDesktopPetVoiceInput = async () => {
  await nextTick()
  isDesktopPetVoiceAutoStarting.value = true
  console.info('[DesktopPetVoice] 收到自动语音启动请求')

  if (desktopPetVoiceStartTimer !== null) {
    window.clearTimeout(desktopPetVoiceStartTimer)
  }

  desktopPetVoiceStartTimer = window.setTimeout(() => {
    desktopPetVoiceStartTimer = null
    void (async () => {
      if (isDesktopPetVoiceAutoStartBlocked()) {
        console.info('[DesktopPetVoice] 自动语音启动被当前状态阻止')
        isDesktopPetVoiceAutoStarting.value = false
        return
      }

      console.info('[DesktopPetVoice] 开始调用语音录音流程')
      await startVoiceQuestionRecording()
      isDesktopPetVoiceAutoStarting.value = false
    })()
  }, 300)
}

const handleDesktopPetVoiceAutoStart = (event?: Event) => {
  const eventPayload = (event as CustomEvent<DesktopPetVoiceAutoStartPayload> | undefined)?.detail
  const payload = consumeDesktopPetVoiceAutoStart() || eventPayload
  if (!payload) return
  console.info('[DesktopPetVoice] 消费自动启动标记:', payload)

  const voiceStartId = getDesktopPetVoiceStartId(payload)
  if (hasConsumedDesktopPetVoiceStart && voiceStartId === consumedDesktopPetVoiceStartId) return

  hasConsumedDesktopPetVoiceStart = true
  consumedDesktopPetVoiceStartId = voiceStartId
  void startDesktopPetVoiceInput()
}

const getDesktopPetMessageText = (payload: DesktopPetPayload | null | undefined) => {
  if (!payload || payload.type !== 'message') return ''
  return String(payload.text || payload.content || payload.transcript || payload.message || '').trim()
}

const isIgnorableDesktopPetMessage = (text: string) => {
  const normalizedText = text.replace(/[，。！？,.!?]/g, '').trim().toLowerCase()
  if (!normalizedText) return true
  if (text.includes('已为你准备好故障诊断工具')) return true
  if (text.includes('请问需要什么帮助')) return true

  const wakePhrases = ['小飞你好', '你好小飞', '小v你好', '你好小v', '小V你好', '你好小V']
  return wakePhrases.some(phrase => normalizedText === phrase.toLowerCase())
}

const submitDesktopPetMessage = async (payload: DesktopPetPayload | null | undefined) => {
  const text = getDesktopPetMessageText(payload)
  if (isIgnorableDesktopPetMessage(text)) return

  if (isStreaming.value) {
    userInput.value = text
    adjustTextareaHeight()
    ElMessage.warning('当前正在生成回复，已将桌宠识别内容放入输入框')
    return
  }

  userInput.value = text
  adjustTextareaHeight()
  await nextTick()
  await sendMessage()
}

const handleDesktopPetMessageEvent = (event: Event) => {
  const payload = (event as CustomEvent<DesktopPetPayload>).detail
  void submitDesktopPetMessage(payload)
}

// 开始新对话
onMounted(() => {
  disposeVoiceGatewayEvent = onVoiceEvent(event => {
    void handleVoiceGatewayEvent(event)
  })
  reviveStreamLifecycle()
  adjustTextareaHeight()
  window.addEventListener(DESKTOP_PET_MESSAGE_EVENT, handleDesktopPetMessageEvent)
  window.addEventListener(DESKTOP_PET_VOICE_AUTOSTART_EVENT, handleDesktopPetVoiceAutoStart)
  void submitDesktopPetMessage(consumePendingDesktopPetMessage())
  handleDesktopPetVoiceAutoStart()
  refreshExpandedTemplateCardHeight()
  window.addEventListener('resize', updateExpandedTemplateCardHeight)
  userIdentityStore.setStatus('idle')
  loadChatHistory()
  window.addEventListener('dcma:pdf-question-draft', applyPdfQuestionDraft)
})

onBeforeRouteLeave(() => {
  stopStreamingTts()
  disposeStream()
})

onBeforeUnmount(() => {
  if (desktopPetVoiceStartTimer !== null) {
    window.clearTimeout(desktopPetVoiceStartTimer)
    desktopPetVoiceStartTimer = null
  }
  isDesktopPetVoiceAutoStarting.value = false
  disposeVoiceGatewayEvent?.()
  disposeVoiceGatewayEvent = null
  void stopVoiceQuestionRecording(false)
  stopVoicePlayback()
  stopStreamingTts()
  window.removeEventListener(DESKTOP_PET_MESSAGE_EVENT, handleDesktopPetMessageEvent)
  window.removeEventListener(DESKTOP_PET_VOICE_AUTOSTART_EVENT, handleDesktopPetVoiceAutoStart)
  disposeStream()
  window.removeEventListener('dcma:pdf-question-draft', applyPdfQuestionDraft)
  userIdentityStore.setStatus('disconnected')
  if (scrollReleaseTimer) {
    window.clearTimeout(scrollReleaseTimer)
  }
  if (scheduledScrollFrame !== null) {
    cancelScheduledScroll()
  }
  window.removeEventListener('resize', updateExpandedTemplateCardHeight)
})
</script>
