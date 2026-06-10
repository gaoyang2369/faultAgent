import assert from 'node:assert/strict'

import {
  DEFAULT_AUTO_FOLLOW_THRESHOLD,
  isNearBottomPosition,
  shouldAutoScrollOnUpdate,
  shouldKeepAutoFollowOnScroll
} from './chatScrollStrategy.js'

assert.equal(DEFAULT_AUTO_FOLLOW_THRESHOLD, 120)

assert.equal(
  isNearBottomPosition({
    scrollHeight: 2000,
    scrollTop: 1688,
    clientHeight: 200
  }),
  true
)

assert.equal(
  isNearBottomPosition({
    scrollHeight: 2000,
    scrollTop: 1500,
    clientHeight: 200
  }),
  false
)

assert.equal(
  shouldAutoScrollOnUpdate({
    force: false,
    autoFollowEnabled: true,
    nearBottom: false
  }),
  true
)

assert.equal(
  shouldAutoScrollOnUpdate({
    force: false,
    autoFollowEnabled: false,
    nearBottom: false
  }),
  false
)

assert.equal(
  shouldAutoScrollOnUpdate({
    force: true,
    autoFollowEnabled: false,
    nearBottom: false
  }),
  true
)

assert.equal(
  shouldKeepAutoFollowOnScroll({
    nearBottom: false,
    isProgrammaticScroll: false
  }),
  false
)

assert.equal(
  shouldKeepAutoFollowOnScroll({
    nearBottom: true,
    isProgrammaticScroll: false
  }),
  true
)

console.log('chatScrollStrategy checks passed')
