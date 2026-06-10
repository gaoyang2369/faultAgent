import { base64ToArrayBuffer } from '@/utils/pcmAudio'

const PCM_SAMPLE_RATE = 16000

type WebkitAudioWindow = Window & {
  webkitAudioContext?: typeof AudioContext
}

const createPlaybackContext = () => {
  const AudioContextCtor = window.AudioContext || (window as WebkitAudioWindow).webkitAudioContext
  if (!AudioContextCtor) {
    throw new Error('当前浏览器不支持语音播放')
  }
  return new AudioContextCtor()
}

const pcm16ToAudioBuffer = (audioContext: AudioContext, pcmBuffer: ArrayBuffer) => {
  const samples = new Int16Array(pcmBuffer)
  const audioBuffer = audioContext.createBuffer(1, samples.length, PCM_SAMPLE_RATE)
  const channelData = audioBuffer.getChannelData(0)

  for (let index = 0; index < samples.length; index += 1) {
    channelData[index] = (samples[index] ?? 0) / 0x8000
  }

  return audioBuffer
}

type PlaybackStateListener = (isPlaying: boolean) => void

export class PcmPlayer {
  private audioContext: AudioContext | null = null
  private activeSource: AudioBufferSourceNode | null = null
  private queue: ArrayBuffer[] = []
  private playing = false
  private readonly onStateChange?: PlaybackStateListener

  constructor(onStateChange?: PlaybackStateListener) {
    this.onStateChange = onStateChange
  }

  get isPlaying() {
    return this.playing
  }

  async prepare() {
    await this.ensureAudioContext()
  }

  enqueueBase64(base64Audio: string) {
    this.queue.push(base64ToArrayBuffer(base64Audio))
    if (!this.playing) {
      void this.playNext()
    }
  }

  stop() {
    this.queue = []
    try {
      this.activeSource?.stop()
    } catch {
      // source may already be stopped; stop() should still reset UI state.
    }
    this.activeSource = null
    this.setPlaying(false)

    const context = this.audioContext
    this.audioContext = null
    if (context && context.state !== 'closed') {
      void context.close()
    }
  }

  private async playNext() {
    const nextBuffer = this.queue.shift()
    if (!nextBuffer) {
      this.setPlaying(false)
      return
    }

    this.setPlaying(true)
    let source: AudioBufferSourceNode
    try {
      const context = await this.ensureAudioContext()
      source = context.createBufferSource()
      source.buffer = pcm16ToAudioBuffer(context, nextBuffer)
      source.connect(context.destination)
      this.activeSource = source
    } catch (error) {
      console.warn('播放语音音频失败:', error)
      this.activeSource = null
      void this.playNext()
      return
    }

    source.onended = () => {
      if (this.activeSource === source) {
        this.activeSource = null
      }
      void this.playNext()
    }

    source.start(0)
  }

  private setPlaying(isPlaying: boolean) {
    if (this.playing === isPlaying) return
    this.playing = isPlaying
    this.onStateChange?.(isPlaying)
  }

  private async ensureAudioContext() {
    if (!this.audioContext || this.audioContext.state === 'closed') {
      this.audioContext = createPlaybackContext()
    }

    if (this.audioContext.state === 'suspended') {
      await this.audioContext.resume()
    }

    return this.audioContext
  }
}
