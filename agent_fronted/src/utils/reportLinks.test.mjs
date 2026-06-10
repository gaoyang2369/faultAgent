import assert from 'node:assert/strict'
import { extractReportLinks, linkifyReportMentions, normalizeReportFilename, toReportUrl } from './reportLinks.js'

const screenshotStyleText = '【报告文件】 运行状态诊断报告已生成： dcma_status_20260417_1628.html'
assert.deepEqual(
  extractReportLinks(screenshotStyleText),
  ['/reports/dcma_status_20260417_1628.html']
)

const savedStyleText = 'HTML 报告已保存至：dcma_status_20260420_1614.html'
assert.deepEqual(
  extractReportLinks(savedStyleText),
  ['/reports/dcma_status_20260420_1614.html']
)

const html = linkifyReportMentions(
  screenshotStyleText,
  (url, filename) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${filename}</a>`
)

assert.match(html, /href="\/reports\/dcma_status_20260417_1628\.html"/)
assert.match(html, />dcma_status_20260417_1628\.html</)

assert.equal(normalizeReportFilename('reports/spindle_overload.md'.split('/').pop()), 'spindle_overload.md')
assert.equal(toReportUrl('spindle_overload.md'), '/reports/spindle_overload.md')

console.log('reportLinks tests passed')
