export type PcmChunk = {
  base64: string
  buffer: ArrayBuffer
}

export type PcmRecorderOptions = {
  onChunk: (chunk: PcmChunk) => void
  sampleRate?: number
  echoCancellation?: boolean
  noiseSuppression?: boolean
  autoGainControl?: boolean
}

const DEFAULT_SAMPLE_RATE = 16000
const PROCESSOR_BUFFER_SIZE = 4096

type WebkitAudioWindow = Window & {
  webkitAudioContext?: typeof AudioContext
}

const createAudioContext = (sampleRate: number) => {
  const AudioContextCtor = window.AudioContext || (window as WebkitAudioWindow).webkitAudioContext
  if (!AudioContextCtor) {
    throw new Error('当前浏览器不支持音频采集')
  }

  try {
    return new AudioContextCtor({ sampleRate })
  } catch {
    return new AudioContextCtor()
  }
}

export const arrayBufferToBase64 = (buffer: ArrayBuffer) => {
  const bytes = new Uint8Array(buffer)
  const chunkSize = 0x8000
  let binary = ''

  for (let index = 0; index < bytes.length; index += chunkSize) {
    const chunk = bytes.subarray(index, index + chunkSize)
    binary += String.fromCharCode(...chunk)
  }

  return btoa(binary)
}

export const base64ToArrayBuffer = (base64: string) => {
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)

  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index)
  }

  return bytes.buffer
}

export const concatArrayBuffers = (buffers: ArrayBuffer[]) => {
  const totalLength = buffers.reduce((sum, buffer) => sum + buffer.byteLength, 0)
  const merged = new Uint8Array(totalLength)
  let offset = 0

  buffers.forEach(buffer => {
    merged.set(new Uint8Array(buffer), offset)
    offset += buffer.byteLength
  })

  return merged.buffer
}

const resampleFloat32 = (input: Float32Array, inputRate: number, outputRate: number) => {
  if (inputRate === outputRate) {
    return input
  }

  const ratio = inputRate / outputRate
  const outputLength = Math.max(1, Math.round(input.length / ratio))
  const output = new Float32Array(outputLength)

  for (let index = 0; index < outputLength; index += 1) {
    const sourceIndex = index * ratio
    const previousIndex = Math.floor(sourceIndex)
    const nextIndex = Math.min(previousIndex + 1, input.length - 1)
    const weight = sourceIndex - previousIndex
    const previousSample = input[previousIndex] ?? 0
    const nextSample = input[nextIndex] ?? previousSample
    output[index] = previousSample * (1 - weight) + nextSample * weight
  }

  return output
}

const float32ToPcm16Buffer = (input: Float32Array, inputRate: number, outputRate: number) => {
  const samples = resampleFloat32(input, inputRate, outputRate)
  const buffer = new ArrayBuffer(samples.length * 2)
  const view = new DataView(buffer)

  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index] ?? 0))
    const pcm = sample < 0 ? sample * 0x8000 : sample * 0x7fff
    view.setInt16(index * 2, pcm, true)
  }

  return buffer
}

export class PcmRecorder {
  private readonly options: Required<Omit<PcmRecorderOptions, 'onChunk'>> & Pick<PcmRecorderOptions, 'onChunk'>
  private audioContext: AudioContext | null = null
  private mediaStream: MediaStream | null = null
  private sourceNode: MediaStreamAudioSourceNode | null = null
  private processorNode: ScriptProcessorNode | null = null
  private silentGainNode: GainNode | null = null

  constructor(options: PcmRecorderOptions) {
    this.options = {
      sampleRate: DEFAULT_SAMPLE_RATE,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      ...options
    }
  }

  async start() {
    if (this.audioContext) return

    this.mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        sampleRate: this.options.sampleRate,
        echoCancellation: this.options.echoCancellation,
        noiseSuppression: this.options.noiseSuppression,
        autoGainControl: this.options.autoGainControl
      }
    })

    this.audioContext = createAudioContext(this.options.sampleRate)
    this.sourceNode = this.audioContext.createMediaStreamSource(this.mediaStream)
    this.processorNode = this.audioContext.createScriptProcessor(PROCESSOR_BUFFER_SIZE, 1, 1)
    this.silentGainNode = this.audioContext.createGain()
    this.silentGainNode.gain.value = 0

    this.processorNode.onaudioprocess = (event: AudioProcessingEvent) => {
      if (!this.audioContext) return
      const float32 = event.inputBuffer.getChannelData(0)
      const pcmBuffer = float32ToPcm16Buffer(
        float32,
        this.audioContext.sampleRate,
        this.options.sampleRate
      )
      this.options.onChunk({
        base64: arrayBufferToBase64(pcmBuffer),
        buffer: pcmBuffer
      })
    }

    this.sourceNode.connect(this.processorNode)
    this.processorNode.connect(this.silentGainNode)
    this.silentGainNode.connect(this.audioContext.destination)
  }

  async stop() {
    this.processorNode?.disconnect()
    this.sourceNode?.disconnect()
    this.silentGainNode?.disconnect()
    this.mediaStream?.getTracks().forEach(track => track.stop())

    const activeContext = this.audioContext
    this.processorNode = null
    this.sourceNode = null
    this.silentGainNode = null
    this.mediaStream = null
    this.audioContext = null

    if (activeContext && activeContext.state !== 'closed') {
      await activeContext.close()
    }
  }
}
