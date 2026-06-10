const REPORT_FILENAME_PATTERN = /([A-Za-z0-9._-]+\.(?:html|md))/i
const BACKTICKED_REPORT_PATTERN = /`{1,3}\s*([A-Za-z0-9._-]+\.(?:html|md))\s*`{1,3}/gi
const LINE_REPORT_PATTERN = /^(\s*)([A-Za-z0-9._-]+\.(?:html|md))(\s*)$/gim
const REPORT_CONTEXT_PATTERN = /(报告文件|报告已保存至|HTML 报告已保存至|报告已生成|诊断报告已生成)/i

const makeDirectUrlRegex = () => /\/reports\/[A-Za-z0-9._\- \u4e00-\u9fa5]+\.(?:md|html)/gi
const makeBacktickedRegex = () => /`{1,3}\s*([A-Za-z0-9._-]+\.(?:html|md))\s*`{1,3}/gi
const makeLineRegex = () => /^(\s*)([A-Za-z0-9._-]+\.(?:html|md))(\s*)$/gim

export const normalizeReportFilename = (filename) => {
  if (!filename) return ''
  const trimmed = String(filename).trim()
  return /^[A-Za-z0-9._-]+\.(?:html|md)$/i.test(trimmed) ? trimmed : ''
}

export const toReportUrl = (filename) => {
  const safeFilename = normalizeReportFilename(filename)
  return safeFilename ? `/reports/${safeFilename}` : ''
}

export const isSafeReportUrl = (url) => {
  if (!url || typeof url !== 'string') return false
  if (!/^\/reports\/[A-Za-z0-9._-]+\.(?:html|md)$/i.test(url.trim())) return false
  const filename = url.trim().split('/').pop()
  if (filename.includes('..')) return false
  if (filename.includes('\\')) return false
  if (filename.startsWith('/')) return false
  return true
}

const lineHasReportContext = (line) => {
  if (!line) return false
  const trimmed = line.trim()
  if (!trimmed) return false
  if (REPORT_CONTEXT_PATTERN.test(trimmed)) return true
  if (/^【\s*报告文件\s*】/i.test(trimmed)) return true
  if (/^[`]{1,3}\s*[A-Za-z0-9._-]+\.(?:html|md)\s*[`]{1,3}$/i.test(trimmed)) return true
  if (/^[A-Za-z0-9._-]+\.(?:html|md)$/i.test(trimmed)) return true
  return false
}

export const extractReportLinks = (text) => {
  const urls = new Set()
  if (!text) return []

  let match
  const directRx = makeDirectUrlRegex()
  while ((match = directRx.exec(text)) !== null) {
    const safeFilename = normalizeReportFilename(match[0].split('/').pop())
    if (safeFilename) {
      urls.add(`/reports/${safeFilename}`)
    }
  }

  const backtickedRx = makeBacktickedRegex()
  while ((match = backtickedRx.exec(text)) !== null) {
    const reportUrl = toReportUrl(match[1])
    if (reportUrl) {
      urls.add(reportUrl)
    }
  }

  const lineRx = makeLineRegex()
  while ((match = lineRx.exec(text)) !== null) {
    const reportUrl = toReportUrl(match[2])
    if (reportUrl) {
      urls.add(reportUrl)
    }
  }

  text.split(/\r?\n/).forEach((line) => {
    if (!lineHasReportContext(line)) return
    const filenameMatch = line.match(REPORT_FILENAME_PATTERN)
    if (!filenameMatch) return
    const reportUrl = toReportUrl(filenameMatch[1])
    if (reportUrl) {
      urls.add(reportUrl)
    }
  })

  return [...urls]
}

const HTML_A_TAG_REPORT = /<a\s[^>]*href\s*=\s*["'][^"']*\/reports\/[^"']*["'][^>]*>[\s\S]*?<\/a>/gi
const HTML_A_TAG_OPEN = /<a\s[^>]*href\s*=\s*["'][^"']*\/reports\/[^"']*["'][^>]*>[^<]*/gi
const HTML_A_TAG_PARTIAL = /<a\s[^>]*\/reports\/[^>]*$/gim
const MARKDOWN_LINK_REPORT = /\[[^\]]*\]\([^)]*\/reports\/[^)]*\)/gi
const BARE_REPORT_URL = /\/reports\/[A-Za-z0-9._\- \u4e00-\u9fa5]+\.(?:md|html)/gi
const BACKTICKED_REPORT_FILENAME = /`{1,3}\s*[A-Za-z0-9._-]+\.(?:html|md)\s*`{1,3}/gi
const LONE_FILENAME_LINE = /^[ \t]*[A-Za-z0-9._-]+\.(?:html|md)[ \t]*$/gim
const REPORT_FILE_LABEL = /【\s*报告文件\s*】[^\n]*/gi
const REPORT_SAVED_LABEL = /(?:HTML\s*)?报告已保存至[^\n]*/gi
const REPORT_GENERATED_LABEL = /(?:诊断)?报告已生成[^\n]*/gi
const ATTR_TARGET = /\s*target\s*=\s*["'][^"']*["']/gi
const ATTR_REL = /\s*rel\s*=\s*["'][^"']*["']/gi
const ATTR_CLASS_REPORT = /\s*class\s*=\s*["']report-link["']/gi
const ATTR_HREF_REPORT = /\s*href\s*=\s*["'][^"']*\/reports\/[^"']*["']/gi
const CLOSE_A_TAG = /<\/a>/gi
const EMPTY_A_TAG = /<a\s*\/?>/gi

export const stripReportMentions = (text) => {
  if (!text) return ''
  let result = text

  result = result.replace(HTML_A_TAG_REPORT, '')
  result = result.replace(HTML_A_TAG_OPEN, '')
  result = result.replace(HTML_A_TAG_PARTIAL, '')
  result = result.replace(MARKDOWN_LINK_REPORT, '')
  result = result.replace(BARE_REPORT_URL, '')
  result = result.replace(BACKTICKED_REPORT_FILENAME, '')

  result = result.split(/\r?\n/).map((line) => {
    const trimmed = line.trim()
    if (/^[A-Za-z0-9._-]+\.(?:html|md)$/i.test(trimmed)) return ''
    if (/^【\s*报告文件\s*】/i.test(trimmed)) return ''
    if (/(?:HTML\s*)?报告已保存至/i.test(trimmed)) return ''
    if (/(?:诊断)?报告已生成/i.test(trimmed)) return ''

    let clean = line
    clean = clean.replace(ATTR_TARGET, '')
    clean = clean.replace(ATTR_REL, '')
    clean = clean.replace(ATTR_CLASS_REPORT, '')
    clean = clean.replace(ATTR_HREF_REPORT, '')
    clean = clean.replace(CLOSE_A_TAG, '')
    clean = clean.replace(EMPTY_A_TAG, '')
    return clean
  }).join('\n')

  result = result.replace(/\n{3,}/g, '\n\n')
  return result.trim()
}

export const linkifyReportMentions = (text, createAnchor) => {
  if (!text) return ''

  const directRx = makeDirectUrlRegex()
  let result = text.replace(directRx, (full) => {
    const safeFilename = normalizeReportFilename(full.split('/').pop())
    if (!safeFilename) return full
    return createAnchor(`/reports/${safeFilename}`, safeFilename, full)
  })

  const backtickedRx = makeBacktickedRegex()
  result = result.replace(backtickedRx, (_full, filename) => {
    const reportUrl = toReportUrl(filename)
    return reportUrl ? createAnchor(reportUrl, filename, filename) : _full
  })

  const lineRx = makeLineRegex()
  result = result.replace(lineRx, (_full, pre, filename, post) => {
    const reportUrl = toReportUrl(filename)
    return reportUrl ? `${pre}${createAnchor(reportUrl, filename, filename)}${post}` : _full
  })

  result = result
    .split(/\r?\n/)
    .map((line) => {
      if (!lineHasReportContext(line)) return line
      const filenameMatch = line.match(REPORT_FILENAME_PATTERN)
      if (!filenameMatch) return line
      const safeFilename = normalizeReportFilename(filenameMatch[1])
      if (!safeFilename) return line
      const reportUrl = toReportUrl(safeFilename)
      if (!reportUrl) return line
      return line.replace(safeFilename, createAnchor(reportUrl, safeFilename, safeFilename))
    })
    .join('\n')

  return result
}
