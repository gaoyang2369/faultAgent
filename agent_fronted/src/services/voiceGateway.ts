export type VoiceGatewayConnectionStatus = 'idle' | 'connecting' | 'connected' | 'disconnected' | 'error'

export type VoiceGatewaySessionState = 'idle' | 'authenticating' | 'listening' | 'thinking' | 'speaking' | 'error'

export type VoiceAuthAcceptedMessage = {
  type: 'auth_accepted'
  user_id?: string
  voiceprint_score?: number
  face_score?: number
  transcript?: string
  thread_id?: string | null
  [key: string]: unknown
}

export type VoiceAuthRejectedMessage = {
  type: 'auth_rejected'
  user_id?: string
  voiceprint_score?: number
  face_score?: number
  reason?: string
  transcript?: string
  [key: string]: unknown
}

export type VoiceGatewayMessage = {
  type: string
  session_id?: string
  state?: VoiceGatewaySessionState | string
  audio?: string
  text?: string
  content?: string
  final_content?: string
  message?: string
  actions?: unknown[]
  visual_actions?: unknown[]
  thread_id?: string | null
  todos?: unknown[]
  chartData?: unknown
  imageUrl?: string | null
  [key: string]: unknown
}

export type VoiceAuthRequestPayload = {
  userId: string
  audio: string
  sessionId?: string
  threadId?: string | null
  faceScore?: number
}

export type VoiceSessionRequestPayload = {
  userId: string
  sessionId?: string
  useVad?: boolean
}

type MessageListener = (message: VoiceGatewayMessage) => void
type StatusListener = (status: VoiceGatewayConnectionStatus) => void

const normalizeWsUrl = (url: string) => {
  const trimmed = url.trim()
  if (!trimmed) return ''
  if (trimmed.startsWith('ws://') || trimmed.startsWith('wss://')) return trimmed
  if (trimmed.startsWith('http://')) return trimmed.replace(/^http:\/\//, 'ws://')
  if (trimmed.startsWith('https://')) return trimmed.replace(/^https:\/\//, 'wss://')
  return trimmed
}

const DEFAULT_VOICE_GATEWAY_URL = 'ws://10.108.13.254:8100/ws/voice'

export const resolveVoiceGatewayUrl = () => {
  const explicitUrl = normalizeWsUrl(import.meta.env.VITE_VOICE_WS_URL || '')
  if (explicitUrl) return explicitUrl

  if (typeof window !== 'undefined') {
    const hostname = window.location.hostname || ''
    if (hostname === 'localhost' || hostname === '127.0.0.1') {
      return DEFAULT_VOICE_GATEWAY_URL
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${protocol}//${hostname}:8100/ws/voice`
  }

  return DEFAULT_VOICE_GATEWAY_URL
}

export class VoiceGatewayClient {
  private readonly wsUrl: string
  private socket: WebSocket | null = null
  private connectPromise: Promise<void> | null = null
  private sessionId: string | null = null
  private readonly messageListeners = new Set<MessageListener>()
  private readonly statusListeners = new Set<StatusListener>()

  constructor(wsUrl = resolveVoiceGatewayUrl()) {
    this.wsUrl = wsUrl
  }

  get readyState() {
    return this.socket?.readyState ?? WebSocket.CLOSED
  }

  onMessage(listener: MessageListener) {
    this.messageListeners.add(listener)
    return () => this.messageListeners.delete(listener)
  }

  onStatus(listener: StatusListener) {
    this.statusListeners.add(listener)
    return () => this.statusListeners.delete(listener)
  }

  async connect() {
    if (this.socket?.readyState === WebSocket.OPEN) return
    if (this.connectPromise) return this.connectPromise

    this.emitStatus('connecting')
    this.connectPromise = new Promise<void>((resolve, reject) => {
      try {
        this.socket = new WebSocket(this.wsUrl)
      } catch (error) {
        this.connectPromise = null
        this.emitStatus('error')
        reject(error)
        return
      }

      const activeSocket = this.socket
      if (!activeSocket) {
        this.connectPromise = null
        this.emitStatus('error')
        reject(new Error('语音网关连接创建失败'))
        return
      }

      activeSocket.onopen = () => {
        this.connectPromise = null
        this.emitStatus('connected')
        resolve()
      }

      activeSocket.onmessage = (event: MessageEvent<string>) => {
        const message = this.parseMessage(event.data)
        if (message) {
          this.messageListeners.forEach(listener => listener(message))
        }
      }

      activeSocket.onerror = () => {
        this.connectPromise = null
        this.emitStatus('error')
        reject(new Error('语音网关连接异常'))
      }

      activeSocket.onclose = () => {
        if (this.socket === activeSocket) {
          this.socket = null
        }
        this.connectPromise = null
        this.emitStatus('disconnected')
      }
    })

    return this.connectPromise
  }

  sendAuthRequest(payload: VoiceAuthRequestPayload) {
    this.sessionId = payload.sessionId || `session_${Date.now()}`
    this.send({
      type: 'auth_request',
      session_id: this.sessionId,
      user_id: payload.userId,
      face_score: payload.faceScore ?? 0,
      audio: payload.audio,
      ...(payload.threadId ? { thread_id: payload.threadId } : {})
    })
  }

  sendSessionRequest(payload: VoiceSessionRequestPayload) {
    this.sessionId = payload.sessionId || this.sessionId || `session_${Date.now()}`
    this.send({
      type: 'session_request',
      session_id: this.sessionId,
      user_id: payload.userId,
      use_vad: payload.useVad ?? false
    })
  }

  sendAudioChunk(audio: string) {
    this.send({
      type: 'audio_chunk',
      ...(this.sessionId ? { session_id: this.sessionId } : {}),
      audio
    })
  }

  sendSpeechEnd() {
    this.send({
      type: 'speech_end',
      ...(this.sessionId ? { session_id: this.sessionId } : {})
    })
  }

  sendAiStart() {
    this.send({
      type: 'ai_start',
      ...(this.sessionId ? { session_id: this.sessionId } : {})
    })
  }

  sendAiEnd() {
    this.send({
      type: 'ai_end',
      ...(this.sessionId ? { session_id: this.sessionId } : {})
    })
  }

  sendStart() {
    this.send({
      type: 'start',
      ...(this.sessionId ? { session_id: this.sessionId } : {})
    })
  }

  sendStop() {
    this.send({
      type: 'stop',
      ...(this.sessionId ? { session_id: this.sessionId } : {})
    })
  }

  close() {
    const activeSocket = this.socket
    this.socket = null
    this.connectPromise = null
    this.sessionId = null
    if (activeSocket && activeSocket.readyState !== WebSocket.CLOSED) {
      activeSocket.close()
    }
    this.emitStatus('disconnected')
  }

  private send(payload: Record<string, unknown>) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      throw new Error('语音网关尚未连接')
    }
    this.socket.send(JSON.stringify(payload))
  }

  private parseMessage(rawData: string): VoiceGatewayMessage | null {
    try {
      const message = JSON.parse(rawData) as VoiceGatewayMessage
      if (typeof message.session_id === 'string' && message.session_id.trim()) {
        this.sessionId = message.session_id
      }
      return message
    } catch (error) {
      console.warn('解析语音网关消息失败:', error)
      return null
    }
  }

  private emitStatus(status: VoiceGatewayConnectionStatus) {
    this.statusListeners.forEach(listener => listener(status))
  }
}
