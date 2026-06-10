export function extractReportLinks(text: string): string[]
export function linkifyReportMentions(
  text: string,
  createAnchor: (url: string, filename: string, matchedText: string) => string
): string
export function stripReportMentions(text: string): string
export function normalizeReportFilename(filename: string): string
export function toReportUrl(filename: string): string
export function isSafeReportUrl(url: string): boolean
