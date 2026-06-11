import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('./ChatMessage.vue', import.meta.url), 'utf8')
const styles = readFileSync(new URL('../assets/ChatMessage.css', import.meta.url), 'utf8')

const taskPanelIndex = source.indexOf('<TaskPanel')
const processToggleIndex = source.indexOf('class="process-toggle-panel"')
const detailsPanelIndex = source.indexOf('class="details-panel"')
const diagnosisCardIndex = source.indexOf('<DiagnosisResultCard')
const finalSectionIndex = source.indexOf('class="final-answer-section"')
const finalMarkdownIndex = source.indexOf('class="text markdown-content"', finalSectionIndex)
const reportActionsIndex = source.indexOf('class="report-actions final-answer-section__actions"')
const footerIndex = source.indexOf('class="message-footer"', finalSectionIndex)

assert.ok(taskPanelIndex > -1, 'TaskPanel should remain rendered in assistant messages')
assert.ok(processToggleIndex > -1, 'final answers should keep a compact process details toggle')
assert.ok(detailsPanelIndex > -1, 'assistant process details should remain rendered')
assert.ok(diagnosisCardIndex > -1, 'diagnosis artifact card should render in assistant messages')
assert.ok(finalSectionIndex > -1, 'final answer section should exist')
assert.ok(finalMarkdownIndex > finalSectionIndex, 'assistant markdown should render inside final answer section')
assert.ok(processToggleIndex < finalSectionIndex, 'process details toggle should appear before the final answer block')
assert.ok(taskPanelIndex < finalSectionIndex, 'task progress should render before final answer')
assert.ok(detailsPanelIndex < finalSectionIndex, 'process details should render before final answer')
assert.ok(diagnosisCardIndex < finalSectionIndex, 'diagnosis card should appear before final answer markdown')
assert.ok(reportActionsIndex > finalMarkdownIndex, 'report actions should stay with the final answer block')
assert.ok(finalSectionIndex < footerIndex, 'copy footer should remain after the final answer block')
assert.match(
  source,
  /<TaskPanel\s+v-if="!isUser && hasTaskSnapshot && shouldShowProcessDetails"/,
  'task progress should collapse once a final answer exists'
)
assert.match(
  source,
  /v-if="!isUser && hasAssistantSummary && shouldShowProcessDetails"/,
  'assistant summary should collapse once a final answer exists'
)
assert.match(
  source,
  /v-if="!isUser && hasWorkflowContractPanel && shouldShowProcessDetails"/,
  'structured workflow panel should collapse once a final answer exists'
)
assert.match(
  source,
  /v-if="false && !isUser && executionTimeline\.length"/,
  'legacy diagnostic timeline should stay disabled'
)
assert.match(
  source,
  /v-if="false && !isUser && governanceSuggestions\.items\.length"/,
  'legacy governance suggestions should stay disabled'
)
assert.match(
  source,
  /const legacyGovernanceExportEnabled = false;/,
  'legacy governance export API prefetch should stay disabled'
)
assert.match(
  source,
  /legacyGovernanceExportEnabled && !isUser\.value && governanceLedger\.value\.items\.length/,
  'legacy governance API loads should require the explicit export flag'
)
assert.match(
  source,
  /v-if="false && !isUser && evidenceFindings\.length"/,
  'legacy finding details should stay disabled'
)
assert.match(
  source,
  /v-if="false && !isUser && normalizedEvidences\.length"/,
  'legacy evidence catalog should stay disabled'
)
assert.match(
  source,
  /watch\(hasFinalAnswerContent,[\s\S]*assistantDetailsExpanded\.value = false;/,
  'process details should auto-collapse when final answer content appears'
)
assert.match(
  source,
  /const hasFinalAnswerSectionContent = computed/,
  'diagnosis card should not force an empty final answer markdown section'
)
assert.match(
  source,
  /v-if="shouldShowFinalAnswerMarkdown"/,
  'diagnosis card should hide the legacy final answer markdown panel'
)
assert.match(
  source,
  /const shouldShowFinalAnswerMarkdown = computed\(\(\) => \([\s\S]*!hasDiagnosisResultCard\.value/,
  'legacy markdown should not render when the diagnosis card exists'
)
assert.match(
  source,
  /const shouldShowFinalProcessToggle = computed\(\(\) => \([\s\S]*!hasDiagnosisResultCard\.value/,
  'process details toggle should not render when the diagnosis card exists'
)
assert.match(
  source,
  /toolEvents\.length && shouldShowToolDetails/,
  'tool execution details should stay hidden in diagnosis card mode'
)
assert.match(
  styles,
  /\.message:not\(\.message-user\) \.content\s*{[^}]*width:\s*80%;/s,
  'assistant message content should keep a stable fixed width during streaming'
)
assert.match(
  styles,
  /\.message:not\(\.message-user\) \.content > \*\s*{[^}]*width:\s*100%;/s,
  'assistant message child panels should fill the stable message width'
)

console.log('ChatMessage layout checks passed')
