import { computed, ref } from 'vue'

import { PcmPlayer } from '@/utils/pcmPlayer'
import {
  DEFAULT_STREAMING_TTS_SEGMENT_CHARS,
  extractStreamingTtsSegments
} from '@/utils/streamingTtsText.js'

type StreamingTtsQueueOptions = {
  endpoint?: string
  maxSegmentChars?: number
}

type TtsSynthesizeResponse = {
  audio?: unknown
}

const DEFAULT_TTS_ENDPOINT = '/api/tts/synthesize'

export const useStreamingTtsQueue = ({
  endpoint = DEFAULT_TTS_ENDPOINT,
  maxSegmentChars = DEFAULT_STREAMING_TTS_SEGMENT_CHARS
}: StreamingTtsQueueOptions = {}) => {
  const isPlaying = ref(false)
  const isSynthesizing = ref(false)
  const lastError = ref('')
  const player = new PcmPlayer(value => {
    isPlaying.value = value
  })

  let acceptingTokens = false
  let generation = 0
  let textBuffer = ''
  let synthesisQueue: string[] = []
  let activeController: AbortController | null = null
  let drainPromise: Promise<void> | null = null

  const resetSynthesisState = () => {
    generation += 1
    acceptingTokens = false
    textBuffer = ''
    synthesisQueue = []
    activeController?.abort()
    activeController = null
    drainPromise = null
    isSynthesizing.value = false
    lastError.value = ''
  }

  const synthesize = async (text: string, signal: AbortSignal) => {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
      signal
    })

    if (!response.ok) {
      throw new Error(`TTS 合成请求失败：${response.status}`)
    }

    const payload = await response.json() as TtsSynthesizeResponse
    return typeof payload.audio === 'string' ? payload.audio : ''
  }

  const drainQueue = () => {
    if (drainPromise) return drainPromise

    const activeGeneration = generation
    const nextDrainPromise = (async () => {
      while (activeGeneration === generation && synthesisQueue.length) {
        const segment = synthesisQueue.shift()
        if (!segment) continue

        const controller = new AbortController()
        activeController = controller
        isSynthesizing.value = true

        try {
          const audio = await synthesize(segment, controller.signal)
          if (activeGeneration === generation && audio) {
            player.enqueueBase64(audio)
          }
        } catch (error) {
          if (!controller.signal.aborted) {
            console.error('流式语音合成失败:', error)
            if (!lastError.value) {
              lastError.value = '流式播报暂时不可用，请检查 TTS 服务配置'
            }
          }
        } finally {
          if (activeController === controller) {
            activeController = null
          }
        }
      }
    })().finally(() => {
      if (activeGeneration === generation) {
        isSynthesizing.value = false
      }
      if (drainPromise === nextDrainPromise) {
        drainPromise = null
      }
      if (generation === activeGeneration && synthesisQueue.length) {
        void drainQueue()
      }
    })
    drainPromise = nextDrainPromise

    return drainPromise
  }

  const enqueueExtractedSegments = (flush = false) => {
    const extracted = extractStreamingTtsSegments(textBuffer, {
      flush,
      maxChars: maxSegmentChars
    })
    textBuffer = extracted.remainder
    synthesisQueue.push(...extracted.segments)
    if (synthesisQueue.length) {
      void drainQueue()
    }
  }

  const start = () => {
    resetSynthesisState()
    acceptingTokens = true
  }

  const append = (token: string) => {
    if (!acceptingTokens || !token) return
    textBuffer += token
    enqueueExtractedSegments()
  }

  const finish = () => {
    if (!acceptingTokens) return
    acceptingTokens = false
    enqueueExtractedSegments(true)
  }

  const stop = () => {
    resetSynthesisState()
    player.stop()
  }

  return {
    isActive: computed(() => acceptingTokens || isSynthesizing.value || isPlaying.value),
    isPlaying,
    isSynthesizing,
    lastError,
    start,
    append,
    finish,
    stop,
    prepare: () => player.prepare()
  }
}
