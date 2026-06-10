const SECTION_DEFINITIONS = [
  { key: 'questionType', title: '问题类型', aliases: ['问题类型'] },
  { key: 'conclusion', title: '结论', aliases: ['结论', '诊断结论', '最终结论'] },
  { key: 'answer', title: '回答', aliases: ['回答', '答复'] },
  { key: 'analysis', title: '分析说明', aliases: ['分析说明', '分析', '说明'] },
  { key: 'evidence', title: '依据与证据', aliases: ['证据', '依据', '数据支撑', '知识依据', '证据来源', '来源依据', '支撑依据'] },
  { key: 'nextActions', title: '下一步建议', aliases: ['下一步动作', '下一步建议', '处置建议', '建议动作', '建议', '处理建议'] },
  { key: 'uncertainty', title: '不确定性与风险', aliases: ['不确定性', '风险提示', '仍缺少的信息', '缺失信息', '待确认信息', '不确定点', '注意事项'] }
]

const SECTION_ALIAS_LOOKUP = SECTION_DEFINITIONS.reduce((lookup, section) => {
  section.aliases.forEach((alias) => {
    lookup.set(alias, section.key)
  })
  return lookup
}, new Map())

const SOURCE_DEFINITIONS = [
  { key: 'sql', label: '数据库 SQL', match: /sql|mysql|postgres|db_|schema|table/i },
  { key: 'knowledge', label: '知识库', match: /knowledge|kb|manual|faiss|retriev|rag|search_tool/i },
  { key: 'report', label: '报告生成', match: /report|html|markdown/i },
  { key: 'analysis', label: '数据分析', match: /python|chart|figure|plot|fig/i },
  { key: 'external', label: '外部检索', match: /search|browser|http|web/i }
]

const BRACKET_SECTION_RE = /【([^】]+)】/g
const BULLET_PREFIX_RE = /^[\s>*-]*((\d+[\.\)、])|[-*•●○▪▸►])\s*/
const WHITESPACE_RE = /\s+/g
const MARKDOWN_TABLE_LINE_RE = /^\|.*\|$/

const normalizeText = (value) => String(value ?? '').replace(/\r\n/g, '\n').trim()

const compactText = (value) => normalizeText(value).replace(WHITESPACE_RE, ' ').trim()

const stripBulletPrefix = (line) => normalizeText(line).replace(BULLET_PREFIX_RE, '').trim()

const uniqueStrings = (items = []) => {
  const seen = new Set()
  return items.filter((item) => {
    const normalized = compactText(item)
    if (!normalized || seen.has(normalized)) {
      return false
    }
    seen.add(normalized)
    return true
  })
}

const resolveSectionKey = (label = '') => {
  const normalizedLabel = compactText(label).replace(/[：:]+$/, '')
  if (!normalizedLabel) {
    return null
  }
  if (SECTION_ALIAS_LOOKUP.has(normalizedLabel)) {
    return SECTION_ALIAS_LOOKUP.get(normalizedLabel)
  }

  for (const section of SECTION_DEFINITIONS) {
    if (section.aliases.some((alias) => normalizedLabel.includes(alias))) {
      return section.key
    }
  }

  return null
}

const createEmptyBuckets = () =>
  SECTION_DEFINITIONS.reduce((buckets, section) => {
    buckets[section.key] = []
    return buckets
  }, {})

const appendSectionBody = (buckets, key, body) => {
  if (!key) {
    return
  }
  const normalizedBody = normalizeText(body)
  if (!normalizedBody) {
    return
  }
  buckets[key].push(normalizedBody)
}

const extractBracketSections = (content) => {
  const matches = Array.from(content.matchAll(BRACKET_SECTION_RE))
  if (!matches.length) {
    return []
  }

  return matches.map((match, index) => {
    const label = compactText(match[1])
    const bodyStart = (match.index ?? 0) + match[0].length
    const nextMatchIndex = matches[index + 1]?.index ?? content.length
    const body = normalizeText(content.slice(bodyStart, nextMatchIndex))
    return { label, body }
  })
}

const extractColonSections = (content) => {
  const aliasPattern = SECTION_DEFINITIONS
    .flatMap((section) => section.aliases)
    .sort((left, right) => right.length - left.length)
    .map((alias) => alias.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .join('|')

  const headingRe = new RegExp(`^\\s*(${aliasPattern})\\s*[：:]\\s*(.*)$`)
  const lines = content.split('\n')
  const sections = []
  let current = null

  lines.forEach((line) => {
    const match = line.match(headingRe)
    if (match) {
      if (current) {
        sections.push({
          label: current.label,
          body: normalizeText(current.body.join('\n'))
        })
      }
      current = {
        label: compactText(match[1]),
        body: [match[2] || '']
      }
      return
    }

    if (current) {
      current.body.push(line)
    }
  })

  if (current) {
    sections.push({
      label: current.label,
      body: normalizeText(current.body.join('\n'))
    })
  }

  return sections
}

const splitSectionItems = (body) => {
  const normalizedBody = normalizeText(body)
  if (!normalizedBody) {
    return []
  }

  const rawLines = normalizedBody
    .split('\n')
    .map((line) => normalizeText(line))
    .filter(Boolean)

  const tableLines = rawLines.filter((line) => MARKDOWN_TABLE_LINE_RE.test(line))
  if (tableLines.length >= 3) {
    const rows = tableLines
      .map((line) => line.split('|').map((cell) => compactText(cell)).filter(Boolean))
      .filter((cells) => cells.length > 0)

    if (rows.length >= 3) {
      const header = rows[0]
      const dataRows = rows.slice(1).filter((cells) => !cells.every((cell) => /^-+$/.test(cell)))
      const readableRows = dataRows.map((cells) => {
        if (cells.length >= 3 && /^\d+$/.test(cells[0])) {
          return `${cells[1]}：${cells.slice(2).join('；')}`
        }
        if (cells.length >= 2) {
          return `${cells[0]}：${cells.slice(1).join('；')}`
        }
        return cells.join('；')
      })

      if (readableRows.length > 0) {
        return uniqueStrings(readableRows)
      }

      return uniqueStrings(header)
    }
  }

  const lines = rawLines
    .map(stripBulletPrefix)
    .filter(Boolean)

  if (lines.length > 1 && /[:：]$/.test(lines[0]) && lines[0].length <= 24) {
    lines.shift()
  }

  if (lines.length > 1) {
    return uniqueStrings(lines)
  }

  const [singleLine] = lines
  if (!singleLine) {
    return []
  }

  return [singleLine]
}

const parseAssistantSections = (content) => {
  const normalizedContent = normalizeText(content)
  const buckets = createEmptyBuckets()

  const sections = extractBracketSections(normalizedContent)
  const fallbackSections = sections.length ? sections : extractColonSections(normalizedContent)

  fallbackSections.forEach(({ label, body }) => {
    appendSectionBody(buckets, resolveSectionKey(label), body)
  })

  return SECTION_DEFINITIONS.reduce((parsed, section) => {
    parsed[section.key] = uniqueStrings(
      buckets[section.key].flatMap((body) => splitSectionItems(body))
    )
    return parsed
  }, {})
}

const groupToolSource = (toolName) => {
  const normalizedToolName = compactText(toolName).toLowerCase()
  if (!normalizedToolName || normalizedToolName === '工具') {
    return null
  }

  const matchedSource = SOURCE_DEFINITIONS.find((source) => source.match.test(normalizedToolName))
  if (matchedSource) {
    return matchedSource
  }

  return {
    key: 'tool',
    label: '工具调用'
  }
}

const collectToolSources = (toolEvents = []) => {
  const sourceMap = new Map()

  toolEvents.forEach((toolEvent) => {
    const toolName = compactText(toolEvent?.tool)
    if (!toolName || toolName === '工具') {
      return
    }

    const source = groupToolSource(toolName)
    if (!source) {
      return
    }

    if (!sourceMap.has(source.key)) {
      sourceMap.set(source.key, {
        key: source.key,
        label: source.label,
        tools: new Set()
      })
    }

    sourceMap.get(source.key).tools.add(toolName)
  })

  return Array.from(sourceMap.values()).map((source) => ({
    key: source.key,
    label: source.label,
    tools: Array.from(source.tools.values()),
    count: source.tools.size
  }))
}

const buildMetrics = ({ conclusionItems, evidenceItems, nextActionItems, uncertaintyItems, toolSources }) => [
  {
    label: '结论状态',
    value: conclusionItems.length ? '已给出' : '待补充',
    tone: conclusionItems.length ? 'good' : 'warning'
  },
  {
    label: '依据数量',
    value: evidenceItems.length
      ? `${evidenceItems.length} 条`
      : toolSources.length
        ? `${toolSources.length} 类来源`
        : '未显式给出',
    tone: evidenceItems.length || toolSources.length ? 'good' : 'warning'
  },
  {
    label: '下一步',
    value: nextActionItems.length ? `${nextActionItems.length} 条` : '未给出',
    tone: nextActionItems.length ? 'good' : 'warning'
  },
  {
    label: '风险提示',
    value: uncertaintyItems.length ? `${uncertaintyItems.length} 条` : '未提及',
    tone: uncertaintyItems.length ? 'warning' : 'good'
  }
]

const buildSourceItems = (toolSources = []) =>
  toolSources.map((source) => ({
    label: source.label,
    value: source.tools.join('、')
  }))

export const buildAssistantEvidenceView = (message = {}) => {
  const sections = parseAssistantSections(message?.content ?? '')
  const toolSources = collectToolSources(message?.toolEvents ?? [])

  const conclusionItems = uniqueStrings(
    sections.conclusion.length ? sections.conclusion : sections.answer
  )
  const evidenceItems = uniqueStrings([...sections.evidence, ...sections.analysis])
  const uncertaintyItems = uniqueStrings(sections.uncertainty)
  const nextActionItems = uniqueStrings(sections.nextActions)
  const questionTypeItems = uniqueStrings(sections.questionType)

  const detailSections = [
    {
      key: 'questionType',
      title: '问题类型',
      items: questionTypeItems
    },
    {
      key: 'conclusion',
      title: sections.conclusion.length ? '结论' : '回答',
      items: conclusionItems
    },
    {
      key: 'evidence',
      title: '依据与证据',
      items: evidenceItems
    },
    {
      key: 'uncertainty',
      title: '不确定性与风险',
      items: uncertaintyItems
    },
    {
      key: 'nextActions',
      title: '下一步建议',
      items: nextActionItems
    },
    {
      key: 'sources',
      title: '来源与工具',
      items: buildSourceItems(toolSources).map((item) => `${item.label}：${item.value}`)
    }
  ].filter((section) => section.items.length > 0)

  const hasStructuredSections = detailSections.some((section) => section.key !== 'sources')
  const hasSummary = hasStructuredSections || toolSources.length > 0

  return {
    hasSummary,
    hasDetails: detailSections.length > 0,
    hasStructuredSections,
    metrics: buildMetrics({
      conclusionItems,
      evidenceItems,
      nextActionItems,
      uncertaintyItems,
      toolSources
    }),
    summaryFindings: conclusionItems.slice(0, 2),
    summaryRisks: uncertaintyItems.slice(0, 2),
    sourceBadges: toolSources,
    detailSections
  }
}

export const parseAssistantSectionsForView = parseAssistantSections
