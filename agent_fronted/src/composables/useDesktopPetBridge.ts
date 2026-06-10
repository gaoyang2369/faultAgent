import { onMounted, onUnmounted, ref } from 'vue'

export type DesktopPetPayload = {
  type?: string
  user_id?: string
  user_role?: string | string[]
  role?: string | string[]
  clean_role?: string | string[]
  permission_hint?: string
  status?: string
  message?: string
  error?: string
  questions?: unknown[]
  [key: string]: unknown
}

export const DESKTOP_PET_MESSAGE_EVENT = 'fd-desktop-pet-message'
export const DESKTOP_PET_VOICE_AUTOSTART_EVENT = 'fd-desktop-pet-voice-autostart'

export type DesktopPetVoiceAutoStartPayload = {
  reason?: string
  received_at?: number
}

const PENDING_DESKTOP_PET_MESSAGE_KEY = 'fd_desktop_pet_pending_message'
const PENDING_DESKTOP_PET_VOICE_AUTOSTART_KEY = 'fd_desktop_pet_voice_autostart'
const PENDING_MESSAGE_MAX_AGE_MS = 30000
const PENDING_VOICE_AUTOSTART_MAX_AGE_MS = 30000

type DesktopPetBridgeOptions = {
  onConnecting?: () => void
  onOpen?: () => void
  onUserInfo?: (payload: DesktopPetPayload) => void
  onAuthFailed?: (payload: DesktopPetPayload) => void
  onQuestions?: (payload: DesktopPetPayload) => void
  onMessage?: (payload: DesktopPetPayload) => void
}

const reconnectDelay = 1200
const maxReconnectCount = 2

const normalizeWsUrl = (url: string) => {
  const trimmed = url.trim()
  if (!trimmed) return ''
  if (trimmed.startsWith('ws://') || trimmed.startsWith('wss://')) return trimmed
  if (trimmed.startsWith('http://')) return trimmed.replace(/^http:\/\//, 'ws://')
  if (trimmed.startsWith('https://')) return trimmed.replace(/^https:\/\//, 'wss://')
  return trimmed
}

const getQueryWsUrl = () => {
  if (typeof window === 'undefined') return ''
  const query = new URLSearchParams(window.location.search)
  return normalizeWsUrl(query.get('desktopPetWs') || query.get('wsUrl') || '')
}

const buildDefaultWsCandidates = () => {
  if (typeof window === 'undefined') return []
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const hostname = window.location.hostname || 'localhost'
  const desktopPetBaseUrl = `${protocol}//${hostname}:3000`
  const legacyBaseUrl = `${protocol}//${hostname}:8765`
  return [
    desktopPetBaseUrl,
    `${desktopPetBaseUrl}/ws`,
    legacyBaseUrl,
    `${legacyBaseUrl}/ws`
  ]
}

const resolveWsCandidates = () => {
  const queryUrl = getQueryWsUrl()
  if (queryUrl) return [queryUrl]

  const envUrl = normalizeWsUrl(import.meta.env.VITE_DESKTOP_PET_WS_URL || '')
  if (envUrl) return [envUrl]

  return buildDefaultWsCandidates()
}

const parsePayload = (rawData: unknown): DesktopPetPayload | null => {
  if (typeof rawData !== 'string') return null

  try {
    return JSON.parse(rawData)
  } catch (error) {
    console.warn('解析桌宠 WebSocket 消息失败:', error)
    return null
  }
}

const isUserInfoPayload = (payload: DesktopPetPayload) => {
  return payload.type === 'user_info' || Boolean(payload.user_id || payload.user_role || payload.clean_role)
}

const isAuthFailedPayload = (payload: DesktopPetPayload) => {
  const normalizedStatus = typeof payload.status === 'string' ? payload.status.toLowerCase() : ''
  return ['auth_failed', 'identity_failed', 'voice_auth_failed'].includes(String(payload.type)) ||
    ['failed', 'fail', 'error'].includes(normalizedStatus)
}

const getTextMessageContent = (payload: DesktopPetPayload) => (
  String(payload.text || payload.content || payload.transcript || payload.message || '').trim()
)

const isTextMessagePayload = (payload: DesktopPetPayload) => (
  payload.type === 'message' && getTextMessageContent(payload).length > 0
)

const notifyDesktopPetMessage = (payload: DesktopPetPayload) => {
  if (typeof window === 'undefined') return

  const payloadWithTimestamp = {
    ...payload,
    received_at: Date.now()
  }

  try {
    window.sessionStorage.setItem(PENDING_DESKTOP_PET_MESSAGE_KEY, JSON.stringify(payloadWithTimestamp))
  } catch (error) {
    console.warn('缓存桌宠消息失败:', error)
  }

  window.dispatchEvent(new CustomEvent(DESKTOP_PET_MESSAGE_EVENT, {
    detail: payloadWithTimestamp
  }))
}

export const consumePendingDesktopPetMessage = (): DesktopPetPayload | null => {
  if (typeof window === 'undefined') return null

  const rawPayload = window.sessionStorage.getItem(PENDING_DESKTOP_PET_MESSAGE_KEY)
  if (!rawPayload) return null

  window.sessionStorage.removeItem(PENDING_DESKTOP_PET_MESSAGE_KEY)

  try {
    const payload = JSON.parse(rawPayload) as DesktopPetPayload
    const receivedAt = Number(payload.received_at || 0)
    if (!receivedAt || Date.now() - receivedAt > PENDING_MESSAGE_MAX_AGE_MS) {
      return null
    }
    return payload
  } catch (error) {
    console.warn('读取桌宠待处理消息失败:', error)
    return null
  }
}

export const queueDesktopPetVoiceAutoStart = (reason = 'desktop_pet_launch') => {
  if (typeof window === 'undefined') return

  const payload: DesktopPetVoiceAutoStartPayload = {
    reason,
    received_at: Date.now()
  }

  try {
    window.sessionStorage.setItem(PENDING_DESKTOP_PET_VOICE_AUTOSTART_KEY, JSON.stringify(payload))
  } catch (error) {
    console.warn('缂撳瓨妗屽疇璇煶鑷姩寮€濮嬫爣璁板け璐?', error)
  }

  window.dispatchEvent(new CustomEvent(DESKTOP_PET_VOICE_AUTOSTART_EVENT, {
    detail: payload
  }))
}

export const consumeDesktopPetVoiceAutoStart = () => {
  if (typeof window === 'undefined') return null

  const rawPayload = window.sessionStorage.getItem(PENDING_DESKTOP_PET_VOICE_AUTOSTART_KEY)
  if (!rawPayload) return null

  window.sessionStorage.removeItem(PENDING_DESKTOP_PET_VOICE_AUTOSTART_KEY)

  try {
    const payload = JSON.parse(rawPayload) as DesktopPetVoiceAutoStartPayload
    const receivedAt = Number(payload.received_at || 0)
    if (!receivedAt || Date.now() - receivedAt > PENDING_VOICE_AUTOSTART_MAX_AGE_MS) {
      return null
    }
    return payload
  } catch (error) {
    console.warn('璇诲彇妗屽疇璇煶鑷姩寮€濮嬫爣璁板け璐?', error)
    return null
  }
}

export const useDesktopPetBridge = (options: DesktopPetBridgeOptions = {}) => {
  const isConnected = ref(false)
  const lastError = ref('')
  const activeUrl = ref('')

  let socket: WebSocket | null = null
  let reconnectTimer: number | null = null
  let reconnectCount = 0
  let candidateIndex = 0
  let isStopped = false

  const clearReconnectTimer = () => {
    if (reconnectTimer === null) return
    window.clearTimeout(reconnectTimer)
    reconnectTimer = null
  }

  const closeSocket = () => {
    if (!socket) return
    socket.onopen = null
    socket.onmessage = null
    socket.onerror = null
    socket.onclose = null
    socket.close()
    socket = null
  }

  const dispatchPayload = (payload: DesktopPetPayload) => {
    options.onMessage?.(payload)

    if (isTextMessagePayload(payload)) {
      notifyDesktopPetMessage(payload)
    }

    if (isAuthFailedPayload(payload)) {
      options.onAuthFailed?.(payload)
      return
    }

    if (isUserInfoPayload(payload)) {
      options.onUserInfo?.(payload)
      return
    }

    if (payload.type === 'questions') {
      options.onQuestions?.(payload)
    }
  }

  const connect = () => {
    if (isStopped) return

    const candidates = resolveWsCandidates()
    if (candidates.length === 0) return

    const wsUrl = candidates[candidateIndex] ?? candidates[0]
    if (!wsUrl) return
    activeUrl.value = wsUrl
    options.onConnecting?.()

    try {
      socket = new WebSocket(wsUrl)
    } catch (error) {
      lastError.value = String(error)
      scheduleReconnect()
      return
    }

    socket.onopen = () => {
      isConnected.value = true
      lastError.value = ''
      reconnectCount = 0
      options.onOpen?.()
    }

    socket.onmessage = (event) => {
      const payload = parsePayload(event.data)
      if (payload) dispatchPayload(payload)
    }

    socket.onerror = () => {
      lastError.value = '桌宠 WebSocket 连接异常'
    }

    socket.onclose = () => {
      isConnected.value = false
      socket = null
      if (!isStopped) scheduleReconnect()
    }
  }

  const scheduleReconnect = () => {
    clearReconnectTimer()

    const candidates = resolveWsCandidates()
    if (candidates.length === 0 || reconnectCount >= maxReconnectCount) return

    reconnectCount += 1
    candidateIndex = (candidateIndex + 1) % candidates.length
    reconnectTimer = window.setTimeout(connect, reconnectDelay)
  }

  onMounted(connect)

  onUnmounted(() => {
    isStopped = true
    clearReconnectTimer()
    closeSocket()
  })

  return {
    isConnected,
    lastError,
    activeUrl,
    reconnect: connect
  }
}
