import { computed, ref } from 'vue'

import {
  VoiceGatewayClient,
  type VoiceAuthAcceptedMessage,
  type VoiceAuthRejectedMessage,
  type VoiceAuthRequestPayload,
  type VoiceGatewayConnectionStatus,
  type VoiceGatewayMessage,
  type VoiceGatewaySessionState,
  type VoiceSessionRequestPayload
} from '@/services/voiceGateway'
import { PcmPlayer } from '@/utils/pcmPlayer'

export type VoiceGatewayEvent =
  | { type: 'auth_accepted'; payload: VoiceAuthAcceptedMessage }
  | { type: 'auth_rejected'; payload: VoiceAuthRejectedMessage }
  | { type: 'session_created'; payload: VoiceGatewayMessage }
  | { type: 'state_change'; payload: VoiceGatewayMessage }
  | { type: 'asr_result'; payload: VoiceGatewayMessage }
  | { type: 'asr_empty'; payload: VoiceGatewayMessage }
  | { type: 'asr_error'; payload: VoiceGatewayMessage }
  | { type: 'visual_actions'; payload: VoiceGatewayMessage }
  | { type: 'agent_token'; payload: VoiceGatewayMessage }
  | { type: 'agent_complete'; payload: VoiceGatewayMessage }
  | { type: 'complete'; payload: VoiceGatewayMessage }
  | { type: 'agent_fallback'; payload: VoiceGatewayMessage }
  | { type: 'agent_tool_start'; payload: VoiceGatewayMessage }
  | { type: 'agent_tool_end'; payload: VoiceGatewayMessage }
  | { type: 'tts_audio'; payload: VoiceGatewayMessage }
  | { type: 'tts_audio_chunk'; payload: VoiceGatewayMessage }
  | { type: 'tts_end'; payload: VoiceGatewayMessage }
  | { type: 'tts_playback_end'; payload: VoiceGatewayMessage }
  | { type: 'interaction_event'; payload: VoiceGatewayMessage }
  | { type: 'status'; payload: VoiceGatewayMessage }
  | { type: 'unknown'; payload: VoiceGatewayMessage }

type VoiceGatewayEventListener = (event: VoiceGatewayEvent) => void

type PendingAuth = {
  resolve: (payload: VoiceAuthAcceptedMessage) => void
  reject: (error: Error & { payload?: VoiceAuthRejectedMessage }) => void
  timer: number
}

type PendingSession = {
  resolve: (payload: VoiceGatewayMessage) => void
  reject: (error: Error) => void
  timer: number
}

const client = new VoiceGatewayClient()
const playbackActive = ref(false)
const connectionStatus = ref<VoiceGatewayConnectionStatus>('idle')
const sessionState = ref<VoiceGatewaySessionState>('idle')
const isAuthenticated = ref(false)
const authenticatedUserId = ref('')
const voiceprintScore = ref<number | null>(null)
const authTranscript = ref('')
const lastError = ref('')
const currentThreadId = ref<string | null>(null)
const listeners = new Set<VoiceGatewayEventListener>()
let pendingAuth: PendingAuth | null = null
let pendingSession: PendingSession | null = null
let ttsIdleFallbackTimer: number | null = null
let playbackFinishedCallback: (() => void) | null = null

const AUTH_TIMEOUT_MS = 15000
const SESSION_TIMEOUT_MS = 8000
const TTS_IDLE_FALLBACK_MS = 2500
const PLAY_GATEWAY_TTS = import.meta.env.VITE_VOICE_PLAY_GATEWAY_TTS !== '0'
const isGatewayTtsPlaybackEnabled = ref(PLAY_GATEWAY_TTS)
console.log('[VoiceGateway] PLAY_GATEWAY_TTS:', PLAY_GATEWAY_TTS, 'env:', import.meta.env.VITE_VOICE_PLAY_GATEWAY_TTS)

const clearTtsIdleFallback = () => {
  if (ttsIdleFallbackTimer === null) return
  window.clearTimeout(ttsIdleFallbackTimer)
  ttsIdleFallbackTimer = null
}

const emitTtsPlaybackEnd = () => {
  clearTtsIdleFallback()
  sessionState.value = 'idle'
  emitEvent({
    type: 'tts_playback_end',
    payload: {
      type: 'tts_playback_end',
      ...(currentThreadId.value ? { thread_id: currentThreadId.value } : {})
    }
  })
  playbackFinishedCallback?.()
}

const scheduleTtsIdleFallback = () => {
  clearTtsIdleFallback()
  ttsIdleFallbackTimer = window.setTimeout(() => {
    ttsIdleFallbackTimer = null
    if (sessionState.value === 'speaking' && !playbackActive.value) {
      emitTtsPlaybackEnd()
    }
  }, TTS_IDLE_FALLBACK_MS)
}

const player = new PcmPlayer(isPlaying => {
  playbackActive.value = isPlaying
  if (!isPlaying && sessionState.value === 'speaking') {
    emitTtsPlaybackEnd()
  }
})

const normalizeSessionState = (state: unknown): VoiceGatewaySessionState => {
  if (state === 'listening' || state === 'thinking' || state === 'speaking' || state === 'idle') {
    return state
  }
  return 'idle'
}

const resolveAuthRejectedMessage = (payload: VoiceAuthRejectedMessage) => {
  const reason = payload.reason || 'unknown'
  const reasonText: Record<string, string> = {
    missing_user_or_audio: '缺少有效用户或唤醒音频',
    no_wakeup_word: '未检测到唤醒词',
    voiceprint_mismatch: '声纹相似度不足',
    unknown: '身份认证失败'
  }
  return reasonText[reason] || `身份认证失败：${reason}`
}

const emitEvent = (event: VoiceGatewayEvent) => {
  listeners.forEach(listener => listener(event))
}

const clearPendingAuth = () => {
  if (!pendingAuth) return
  window.clearTimeout(pendingAuth.timer)
  pendingAuth = null
}

const clearPendingSession = () => {
  if (!pendingSession) return
  window.clearTimeout(pendingSession.timer)
  pendingSession = null
}

const resolvePendingAuth = (payload: VoiceAuthAcceptedMessage) => {
  if (!pendingAuth) return
  const activeAuth = pendingAuth
  clearPendingAuth()
  activeAuth.resolve(payload)
}

const rejectPendingAuth = (payload: VoiceAuthRejectedMessage) => {
  if (!pendingAuth) return
  const activeAuth = pendingAuth
  const error = new Error(resolveAuthRejectedMessage(payload)) as Error & { payload?: VoiceAuthRejectedMessage }
  error.payload = payload
  clearPendingAuth()
  activeAuth.reject(error)
}

const getMessageText = (payload: VoiceGatewayMessage) =>
  String(payload.content || payload.text || payload.final_content || payload.message || '')

const handleMessage = (payload: VoiceGatewayMessage) => {
  if (payload.thread_id) {
    currentThreadId.value = payload.thread_id
  }

  switch (payload.type) {
    case 'session_created':
      isAuthenticated.value = true
      authenticatedUserId.value = String(payload.user_id || '')
      sessionState.value = 'listening'
      lastError.value = ''
      if (pendingSession) {
        const activeSession = pendingSession
        clearPendingSession()
        activeSession.resolve(payload)
      }
      emitEvent({ type: 'session_created', payload })
      break

    case 'auth_accepted':
      isAuthenticated.value = true
      authenticatedUserId.value = String(payload.user_id || '')
      voiceprintScore.value = typeof payload.voiceprint_score === 'number' ? payload.voiceprint_score : null
      authTranscript.value = String(payload.transcript || '')
      sessionState.value = 'listening'
      lastError.value = ''
      resolvePendingAuth(payload as VoiceAuthAcceptedMessage)
      emitEvent({ type: 'auth_accepted', payload: payload as VoiceAuthAcceptedMessage })
      break

    case 'auth_rejected':
      isAuthenticated.value = false
      sessionState.value = 'error'
      lastError.value = resolveAuthRejectedMessage(payload as VoiceAuthRejectedMessage)
      rejectPendingAuth(payload as VoiceAuthRejectedMessage)
      emitEvent({ type: 'auth_rejected', payload: payload as VoiceAuthRejectedMessage })
      break

    case 'state_change':
      sessionState.value = normalizeSessionState(payload.state)
      emitEvent({ type: 'state_change', payload })
      break

    case 'asr_result':
      emitEvent({ type: 'asr_result', payload })
      break

    case 'asr_empty':
      sessionState.value = 'listening'
      emitEvent({ type: 'asr_empty', payload })
      break

    case 'asr_error':
      sessionState.value = 'error'
      lastError.value = String(payload.message || '语音识别失败')
      emitEvent({ type: 'asr_error', payload })
      break

    case 'visual_actions':
      emitEvent({ type: 'visual_actions', payload })
      break

    case 'agent_token':
    case 'agent_text':
    case 'token':
      if (getMessageText(payload)) {
        emitEvent({ type: 'agent_token', payload })
      }
      break

    case 'agent_complete':
    case 'complete':
      emitEvent({ type: 'agent_complete', payload })
      break

    case 'agent_tool_start':
      emitEvent({ type: 'agent_tool_start', payload })
      break

    case 'agent_tool_end':
      emitEvent({ type: 'agent_tool_end', payload })
      break

    case 'agent_fallback':
      emitEvent({ type: 'agent_fallback', payload })
      break

    case 'tts_audio':
    case 'tts_audio_chunk':
      console.log('[VoiceGateway] tts_audio received, audio length:', payload.audio?.length, 'enabled:', isGatewayTtsPlaybackEnabled.value)
      if (payload.audio) {
        sessionState.value = 'speaking'
        if (isGatewayTtsPlaybackEnabled.value) {
          player.enqueueBase64(String(payload.audio))
          console.log('[VoiceGateway] enqueued audio successfully')
        }
        scheduleTtsIdleFallback()
        emitEvent({ type: payload.type === 'tts_audio_chunk' ? 'tts_audio_chunk' : 'tts_audio', payload })
      }
      break

    case 'tts_end':
      clearTtsIdleFallback()
      if (sessionState.value === 'speaking') {
        sessionState.value = 'idle'
      }
      emitEvent({ type: 'tts_end', payload })
      break

    case 'interaction_event':
      emitEvent({ type: 'interaction_event', payload })
      break

    case 'status':
      emitEvent({ type: 'status', payload })
      break

    default:
      emitEvent({ type: 'unknown', payload })
      break
  }
}

client.onStatus(status => {
  connectionStatus.value = status
  if (status === 'disconnected') {
    isAuthenticated.value = false
    sessionState.value = 'idle'
    clearTtsIdleFallback()
    player.stop()
  }
  if (status === 'error') {
    sessionState.value = 'error'
    lastError.value = '语音网关连接异常'
  }
})

client.onMessage(handleMessage)

export const useVoiceGatewaySession = () => {
  const isConnected = computed(() => connectionStatus.value === 'connected')
  const isSpeaking = computed(() => sessionState.value === 'speaking' || playbackActive.value)

  const connect = async () => {
    lastError.value = ''
    await client.connect()
  }

  const createSession = async (
    payloadOrUserId: VoiceSessionRequestPayload | string,
    useVad = false,
    sessionId?: string
  ) => {
    const payload = typeof payloadOrUserId === 'string'
      ? { userId: payloadOrUserId, useVad, sessionId }
      : payloadOrUserId
    clearPendingSession()
    await connect()
    sessionState.value = 'listening'
    lastError.value = ''

    return await new Promise<VoiceGatewayMessage>((resolve, reject) => {
      const timer = window.setTimeout(() => {
        if (!pendingSession) return
        clearPendingSession()
        reject(new Error('语音会话创建超时，请重试'))
      }, SESSION_TIMEOUT_MS)

      pendingSession = { resolve, reject, timer }

      try {
        client.sendSessionRequest(payload)
      } catch (error) {
        clearPendingSession()
        reject(error)
      }
    })
  }

  const authenticateWithAudio = async (payload: VoiceAuthRequestPayload) => {
    clearPendingAuth()
    await connect()
    sessionState.value = 'authenticating'
    lastError.value = ''

    return await new Promise<VoiceAuthAcceptedMessage>((resolve, reject) => {
      const timer = window.setTimeout(() => {
        if (!pendingAuth) return
        const error = new Error('身份认证超时，请重试') as Error & { payload?: VoiceAuthRejectedMessage }
        clearPendingAuth()
        reject(error)
      }, AUTH_TIMEOUT_MS)

      pendingAuth = {
        resolve,
        reject,
        timer
      }

      try {
        client.sendAuthRequest(payload)
      } catch (error) {
        clearPendingAuth()
        reject(error)
      }
    })
  }

  const sendAudioChunk = (audio: string) => {
    client.sendAudioChunk(audio)
  }

  const sendSpeechEnd = () => {
    client.sendSpeechEnd()
  }

  const sendAiStart = () => {
    try {
      client.sendAiStart()
    } catch {}
  }

  const sendAiEnd = () => {
    try {
      client.sendAiEnd()
    } catch {}
  }

  const sendStart = () => {
    try {
      client.sendStart()
    } catch {}
  }

  const sendStop = () => {
    try {
      client.sendStop()
    } catch {}
  }

  const setGatewayTtsPlaybackEnabled = (enabled: boolean) => {
    isGatewayTtsPlaybackEnabled.value = PLAY_GATEWAY_TTS && enabled
    if (!isGatewayTtsPlaybackEnabled.value) {
      player.stop()
    }
  }

  const stopPlaybackAndSync = () => {
    clearTtsIdleFallback()
    player.stop()
    if (sessionState.value === 'speaking') {
      sessionState.value = 'idle'
    }
  }

  const disconnect = () => {
    clearPendingAuth()
    clearPendingSession()
    clearTtsIdleFallback()
    stopPlaybackAndSync()
    client.close()
  }

  const onVoiceEvent = (listener: VoiceGatewayEventListener) => {
    listeners.add(listener)
    return () => listeners.delete(listener)
  }

  return {
    connectionStatus,
    sessionState,
    isConnected,
    isAuthenticated,
    isSpeaking,
    isPlayerPlaying: playbackActive,
    isGatewayTtsPlaybackEnabled,
    authenticatedUserId,
    voiceprintScore,
    authTranscript,
    lastError,
    currentThreadId,
    connect,
    createSession,
    disconnect,
    authenticateWithAudio,
    sendAudioChunk,
    sendSpeechEnd,
    sendAiStart,
    sendAiEnd,
    sendStart,
    sendStop,
    setGatewayTtsPlaybackEnabled,
    setOnPlaybackFinished: (cb: (() => void) | null) => {
      playbackFinishedCallback = cb
    },
    stopPlayback: stopPlaybackAndSync,
    preparePlayback: () => player.prepare(),
    onVoiceEvent
  }
}
