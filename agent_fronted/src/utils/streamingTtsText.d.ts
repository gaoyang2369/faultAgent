export const DEFAULT_STREAMING_TTS_SEGMENT_CHARS: number

export function cleanStreamingTtsText(text: unknown): string

export function extractStreamingTtsSegments(
  text: unknown,
  options?: {
    flush?: boolean
    maxChars?: number
  }
): {
  segments: string[]
  remainder: string
}
