export function normalizeMessageRole(message?: Record<string, unknown>): string
export function normalizeMessageContent(content: unknown): string
export function isMessageContentDegraded(content: unknown): boolean
export function normalizeChatMessage(message?: Record<string, unknown>): Record<string, unknown>
export function isRenderableChatMessage(message?: Record<string, unknown>): boolean
export function mergeMessagesWithLocalCache(
  serverMessages?: Array<Record<string, unknown>>,
  cachedMessages?: Array<Record<string, unknown>>
): Array<Record<string, unknown>>
