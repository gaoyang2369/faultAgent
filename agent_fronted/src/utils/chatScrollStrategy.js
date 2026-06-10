export const DEFAULT_AUTO_FOLLOW_THRESHOLD = 120

export const isNearBottomPosition = (
  {
    scrollHeight = 0,
    scrollTop = 0,
    clientHeight = 0
  } = {},
  threshold = DEFAULT_AUTO_FOLLOW_THRESHOLD
) => {
  const distanceFromBottom = Number(scrollHeight) - Number(scrollTop) - Number(clientHeight)
  return distanceFromBottom <= threshold
}

export const shouldKeepAutoFollowOnScroll = ({
  nearBottom = true,
  isProgrammaticScroll = false
} = {}) => nearBottom || isProgrammaticScroll

export const shouldAutoScrollOnUpdate = ({
  force = false,
  autoFollowEnabled = true,
  nearBottom = true
} = {}) => force || autoFollowEnabled || nearBottom
