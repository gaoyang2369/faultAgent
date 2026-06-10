<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Loading, Microphone } from '@element-plus/icons-vue'
import { useVoiceGatewaySession } from '@/composables/useVoiceGatewaySession'
import { arrayBufferToBase64, concatArrayBuffers, PcmRecorder } from '@/utils/pcmAudio'
import { useUserIdentityStore } from '@/stores/userIdentity'

defineProps<{
  modelValue: boolean
}>()

type VoiceAuthSuccessPayload = {
  role: string
  userId: string
  userRole: string
  voiceprintScore?: number | null
  transcript?: string
}

const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean): void
  (event: 'auth-success', payload: VoiceAuthSuccessPayload): void
}>()

const isRecording = ref(false)
const isAuthenticating = ref(false)
const audioChunks = ref<ArrayBuffer[]>([])
const authStatusText = ref('')
let recorder: PcmRecorder | null = null

const userIdentityStore = useUserIdentityStore()
const { authenticateWithAudio, voiceprintScore, authTranscript } = useVoiceGatewaySession()

const authScoreText = computed(() => (
  typeof voiceprintScore.value === 'number'
    ? `声纹分数：${voiceprintScore.value.toFixed(3)}`
    : ''
))

const resolveAuthUserId = () => {
  const envUserId = import.meta.env.VITE_VOICE_DEFAULT_USER_ID?.trim()
  if (envUserId) return envUserId

  const currentUserId = typeof userIdentityStore.userId === 'string'
    ? userIdentityStore.userId.trim()
    : ''
  if (currentUserId && !['guest', 'unknown'].includes(currentUserId.toLowerCase())) {
    return currentUserId
  }

  return 'admin'
}

const voiceRoleRules = [
  { keywords: ['数据录入员', 'l1', '数据查看和录入'], role: '数据录入员' },
  { keywords: ['数据分析师', 'l2', '数据查看、录入和分析', '数据查看录入和分析'], role: '数据分析师' },
  { keywords: ['系统工程师', 'l3', '数据管理和系统维护'], role: '系统工程师' },
  { keywords: ['技术专家', 'l4', '高级技术支持和权限管理'], role: '技术专家' },
  { keywords: ['总监', 'l5', '全局管理'], role: '总监' }
]

const normalizeRoleText = (role: unknown) => {
  if (Array.isArray(role)) return role.map(item => String(item).trim()).filter(Boolean).join('、')
  return typeof role === 'string' ? role.trim() : ''
}

const resolveVoiceRole = (role: unknown, userId: string) => {
  const rawRole = normalizeRoleText(role)
  const cleanRole = (rawRole || userId).toLowerCase()
  const matchedRule = voiceRoleRules.find(rule =>
    rule.keywords.some(keyword => cleanRole.includes(keyword.toLowerCase()))
  )

  if (matchedRule) return matchedRule.role

  const normalizedUserId = userId.toLowerCase()
  const visitorKeywords = ['guest', 'visitor', '游客', '访客']
  if (visitorKeywords.some(keyword =>
    cleanRole.includes(keyword.toLowerCase()) || normalizedUserId.includes(keyword.toLowerCase())
  )) {
    return '访客'
  }

  return rawRole || '管理员'
}

const resolveRolePayload = (
  userId: string,
  role: unknown,
  score?: number | null,
  transcript?: string
): VoiceAuthSuccessPayload => {
  const normalizedUserId = userId.trim() || resolveAuthUserId()
  const userRole = resolveVoiceRole(role, normalizedUserId)
  const isVisitor = userRole === '访客'

  return {
    role: isVisitor ? 'visitor' : 'admin',
    userId: normalizedUserId,
    userRole,
    voiceprintScore: score,
    transcript
  }
}

const authReasonText: Record<string, string> = {
  missing_user_or_audio: '缺少有效用户或唤醒音频',
  no_wakeup_word: '未检测到唤醒词，请说“小飞你好”后重试',
  voiceprint_mismatch: '声纹相似度不足，请确认当前用户身份'
}

const handleClose = () => {
  if (isRecording.value) {
    void stopRecorder()
  }
  emit('update:modelValue', false)
}

const startRecording = async () => {
  if (isAuthenticating.value || isRecording.value) return
  try {
    recorder = new PcmRecorder({
      onChunk: chunk => {
        audioChunks.value.push(chunk.buffer)
      }
    })
    audioChunks.value = []
    authStatusText.value = '正在采集唤醒音频...'
    await recorder.start()
    isRecording.value = true
  } catch (error) {
    console.error('录音权限开启失败:', error)
    ElMessage.error('无法访问麦克风，请检查浏览器权限设置。')
    await stopRecorder()
  }
}

const stopRecorder = async () => {
  const activeRecorder = recorder
  recorder = null
  if (activeRecorder) {
    await activeRecorder.stop()
  }
  isRecording.value = false
}

const stopRecording = async () => {
  if (!isRecording.value) return
  await stopRecorder()
  await authenticateVoice()
}

const authenticateVoice = async () => {
  if (!audioChunks.value.length) {
    ElMessage.warning('未采集到有效音频，请重试。')
    authStatusText.value = ''
    return
  }

  isAuthenticating.value = true
  authStatusText.value = '正在进行声纹认证...'

  try {
    const userId = resolveAuthUserId()
    const audio = arrayBufferToBase64(concatArrayBuffers(audioChunks.value))
    const result = await authenticateWithAudio({
      userId,
      audio,
      faceScore: 0,
    })

    const payload = resolveRolePayload(
      result.user_id || userId,
      result.user_role || result.role || result.clean_role,
      result.voiceprint_score ?? null,
      result.transcript || ''
    )
    authStatusText.value = result.transcript ? `识别文本：${result.transcript}` : '认证通过'
    ElMessage.success(`识别成功：${payload.userRole}`)
    emit('auth-success', payload)
    handleClose()
  } catch (error) {
    const rejectedPayload = (error as Error & { payload?: { reason?: string; transcript?: string } }).payload
    const reason = rejectedPayload?.reason || ''
    const message = reason ? authReasonText[reason] || `身份认证失败：${reason}` : (error as Error).message
    authStatusText.value = rejectedPayload?.transcript
      ? `${message}；识别文本：${rejectedPayload.transcript}`
      : message
    console.error('声纹认证失败:', error)
    ElMessage.error(message || '身份识别接口暂不可用，请稍后重试。')
  } finally {
    isAuthenticating.value = false
    audioChunks.value = []
  }
}

onUnmounted(() => {
  if (isRecording.value) {
    void stopRecorder()
  }
})
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="身份声纹识别"
    width="360px"
    center
    append-to-body
    custom-class="voice-auth-dialog"
    @update:model-value="handleClose"
  >
    <div class="voice-auth-container">
      <p class="hint-text">
        {{ isRecording ? '正在录音，请说“小飞你好”...' : '长按图标开始声纹校验' }}
      </p>

      <div class="recorder-visualizer">
        <div v-if="isRecording" class="pulse-ring"></div>
        <button
          class="mic-button"
          :class="{ 'is-recording': isRecording, 'is-loading': isAuthenticating }"
          :disabled="isAuthenticating"
          @mousedown="startRecording"
          @mouseup="stopRecording"
          @mouseleave="stopRecording"
          @touchstart.prevent="startRecording"
          @touchend.prevent="stopRecording"
        >
          <el-icon v-if="isAuthenticating" class="is-loading"><Loading /></el-icon>
          <el-icon v-else size="40"><Microphone /></el-icon>
        </button>
      </div>

      <div class="status-tip">
        <span v-if="isAuthenticating">正在处理中...</span>
        <span v-else-if="isRecording" class="recording-label">松开结束录制</span>
        <span v-else>长按按钮开始</span>
      </div>
      <div v-if="authStatusText || authScoreText || authTranscript" class="auth-detail">
        <span v-if="authStatusText">{{ authStatusText }}</span>
        <span v-if="authScoreText">{{ authScoreText }}</span>
        <span v-if="authTranscript && !authStatusText">识别文本：{{ authTranscript }}</span>
      </div>
    </div>
  </el-dialog>
</template>

<style scoped lang="scss">
.voice-auth-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 10px 0;

  .hint-text {
    font-size: 14px;
    color: #606266;
    margin-bottom: 30px;
  }

  .recorder-visualizer {
    position: relative;
    width: 120px;
    height: 120px;
    display: flex;
    justify-content: center;
    align-items: center;
    margin-bottom: 20px;
  }

  .mic-button {
    position: relative;
    z-index: 2;
    width: 80px;
    height: 80px;
    border-radius: 50%;
    border: none;
    background: #165dff;
    color: #ffffff;
    cursor: pointer;
    transition: all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
    box-shadow: 0 4px 12px rgba(22, 93, 255, 0.3);

    &:hover {
      transform: scale(1.05);
      background: #4080ff;
    }

    &:active {
      transform: scale(0.95);
    }

    &.is-recording {
      background: #f56c6c;
      box-shadow: 0 4px 20px rgba(245, 108, 108, 0.4);
    }

    &.is-loading {
      background: #909399;
      cursor: not-allowed;
    }
  }

  .pulse-ring {
    position: absolute;
    width: 80px;
    height: 80px;
    border: 2px solid #f56c6c;
    border-radius: 50%;
    animation: pulse-out 1.5s infinite;
  }

  .status-tip {
    font-size: 13px;
    color: #909399;
    height: 20px;
  }

  .auth-detail {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-height: 18px;
    margin-top: 10px;
    color: #606266;
    font-size: 12px;
    line-height: 1.5;
    text-align: center;
  }

  .recording-label {
    color: #f56c6c;
    font-weight: 700;
  }
}

@keyframes pulse-out {
  0% {
    transform: scale(1);
    opacity: 0.8;
  }
  100% {
    transform: scale(2);
    opacity: 0;
  }
}
</style>
