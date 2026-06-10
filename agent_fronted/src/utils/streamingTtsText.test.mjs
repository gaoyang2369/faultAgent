import assert from 'node:assert/strict'

import {
  cleanStreamingTtsText,
  extractStreamingTtsSegments
} from './streamingTtsText.js'

assert.equal(
  cleanStreamingTtsText('## 结论\n请查看 [操作手册](https://example.com/manual)。'),
  '结论 请查看 操作手册。'
)

assert.deepEqual(
  extractStreamingTtsSegments('第一句。第二句！剩余内容'),
  {
    segments: ['第一句。', '第二句！'],
    remainder: '剩余内容'
  }
)

assert.deepEqual(
  extractStreamingTtsSegments('没有标点但是内容已经足够长需要提前切分', { maxChars: 12 }),
  {
    segments: ['没有标点但是内容已经足够'],
    remainder: '长需要提前切分'
  }
)

assert.deepEqual(
  extractStreamingTtsSegments('最后不足一句', { flush: true }),
  {
    segments: ['最后不足一句'],
    remainder: ''
  }
)

console.log('streamingTtsText checks passed')
