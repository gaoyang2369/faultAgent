export const DEFAULT_AUTO_FOLLOW_THRESHOLD: number

export function isNearBottomPosition(
  position?: {
    scrollHeight?: number
    scrollTop?: number
    clientHeight?: number
  },
  threshold?: number
): boolean

export function shouldKeepAutoFollowOnScroll(options?: {
  nearBottom?: boolean
  isProgrammaticScroll?: boolean
}): boolean

export function shouldAutoScrollOnUpdate(options?: {
  force?: boolean
  autoFollowEnabled?: boolean
  nearBottom?: boolean
}): boolean
