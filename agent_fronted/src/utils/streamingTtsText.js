export const DEFAULT_STREAMING_TTS_SEGMENT_CHARS = 56

const SENTENCE_END_PATTERN = /[。！？.!?\n]+/

export const cleanStreamingTtsText = (text) => (
  String(text || '')
    .replace(/```[\s\S]*?(?:```|$)/g, ' ')
    .replace(/!\[[^\]]*]\([^)]+\)/g, ' ')
    .replace(/\[([^\]]+)]\([^)]+\)/g, '$1')
    .replace(/https?:\/\/\S+/g, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/^[\s>*#-]+/gm, ' ')
    .replace(/\s+/g, ' ')
    .trim()
)

const findSegmentEnd = (buffer, maxChars) => {
  const sentenceMatch = SENTENCE_END_PATTERN.exec(buffer)
  if (sentenceMatch?.index !== undefined) {
    return sentenceMatch.index + sentenceMatch[0].length
  }

  if (buffer.length < maxChars) return -1

  const preferredBreaks = ['，', ',', '；', ';', '：', ':', ' ']
  const searchFrom = Math.max(Math.floor(maxChars * 0.6), 1)
  const candidate = buffer.slice(searchFrom, maxChars + 1)
  let preferredIndex = -1

  for (const separator of preferredBreaks) {
    preferredIndex = Math.max(preferredIndex, candidate.lastIndexOf(separator))
  }

  return preferredIndex >= 0 ? searchFrom + preferredIndex + 1 : maxChars
}

export const extractStreamingTtsSegments = (
  text,
  {
    flush = false,
    maxChars = DEFAULT_STREAMING_TTS_SEGMENT_CHARS
  } = {}
) => {
  const segments = []
  let remainder = String(text || '')
  const normalizedMaxChars = Math.max(Number(maxChars) || DEFAULT_STREAMING_TTS_SEGMENT_CHARS, 12)

  while (remainder) {
    const segmentEnd = findSegmentEnd(remainder, normalizedMaxChars)
    if (segmentEnd < 0) break

    const cleanedSegment = cleanStreamingTtsText(remainder.slice(0, segmentEnd))
    remainder = remainder.slice(segmentEnd)
    if (cleanedSegment) segments.push(cleanedSegment)
  }

  if (flush) {
    const cleanedRemainder = cleanStreamingTtsText(remainder)
    remainder = ''
    if (cleanedRemainder) segments.push(cleanedRemainder)
  }

  return { segments, remainder }
}
