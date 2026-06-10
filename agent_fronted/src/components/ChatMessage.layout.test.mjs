import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('./ChatMessage.vue', import.meta.url), 'utf8')
const styles = readFileSync(new URL('../assets/ChatMessage.css', import.meta.url), 'utf8')

const taskPanelIndex = source.indexOf('<TaskPanel')
const processToggleIndex = source.indexOf('class="process-toggle-panel"')
const detailsPanelIndex = source.indexOf('class="details-panel"')
const finalSectionIndex = source.indexOf('class="final-answer-section"')
const finalMarkdownIndex = source.indexOf('class="text markdown-content"', finalSectionIndex)
const reportActionsIndex = source.indexOf('class="report-actions final-answer-section__actions"')
const footerIndex = source.indexOf('class="message-footer"', finalSectionIndex)

assert.ok(taskPanelIndex > -1, 'TaskPanel should remain rendered in assistant messages')
assert.ok(processToggleIndex > -1, 'final answers should keep a compact process details toggle')
assert.ok(detailsPanelIndex > -1, 'assistant process details should remain rendered')
assert.ok(finalSectionIndex > -1, 'final answer section should exist')
assert.ok(finalMarkdownIndex > finalSectionIndex, 'assistant markdown should render inside final answer section')
assert.ok(processToggleIndex < finalSectionIndex, 'process details toggle should appear before the final answer block')
assert.ok(taskPanelIndex < finalSectionIndex, 'task progress should render before final answer')
assert.ok(detailsPanelIndex < finalSectionIndex, 'process details should render before final answer')
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
  /watch\(hasFinalAnswerContent,[\s\S]*assistantDetailsExpanded\.value = false;/,
  'process details should auto-collapse when final answer content appears'
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
