<template>
  <div class="message" :class="{ 'message-user': isUser }">
    <div class="avatar">
      <UserCircleIcon v-if="isUser" class="icon" />
      <ComputerDesktopIcon v-else class="icon" :class="{ 'assistant': !isUser }" />
    </div>
    <div class="content">
      <div v-if="isUser" class="text-container">
        <div v-if="isEditingUserMessage" class="user-edit-box">
          <textarea
            v-model="editDraft"
            class="user-edit-input"
            rows="3"
            @keydown.esc.prevent="cancelUserEdit"
          ></textarea>
          <div class="user-edit-actions">
            <button type="button" class="user-edit-action secondary" @click="cancelUserEdit">
              取消
            </button>
            <button
              type="button"
              class="user-edit-action primary"
              :disabled="!editDraft.trim() || editDraft.trim() === String(message.content || '').trim()"
              @click="submitUserEdit"
            >
              保存并重新生成
            </button>
          </div>
        </div>
        <template v-else>
          <div class="user-message-actions">
            <button v-if="canEditUserMessage" class="user-icon-button" @click="startUserEdit" title="编辑并重新生成">
              <PencilSquareIcon class="copy-icon" />
            </button>
            <button
              v-if="editHistoryItems.length"
              class="user-icon-button"
              @click="showEditHistory = !showEditHistory"
              title="查看编辑历史"
            >
              <ClockIcon class="copy-icon" />
            </button>
          </div>
          <div class="text" ref="contentRef">
            {{ message.content }}
          </div>
          <button
            v-if="message.isEdited"
            type="button"
            class="user-edited-badge"
            @click="showEditHistory = !showEditHistory"
          >
            已编辑<span v-if="editHistoryItems.length"> · {{ editHistoryItems.length }} 次</span>
          </button>
          <div v-if="showEditHistory && editHistoryItems.length" class="user-edit-history">
            <div
              v-for="item in editHistoryItems"
              :key="item.key"
              class="user-edit-history__item"
            >
              <div class="user-edit-history__meta">{{ item.label }}</div>
              <div class="user-edit-history__content">{{ item.content }}</div>
            </div>
          </div>
        </template>
      </div>

      <div
        v-if="!isUser && props.message.statusText"
        class="stream-status"
        :class="`stream-status--${props.message.streamState || 'idle'}`"
      >
        {{ props.message.statusText }}
      </div>

      <!-- 轻量图片预览 -->
      <div v-if="imagePreview.visible" class="image-viewer" @click.self="closeImage" @wheel.prevent="onWheel">
        <img
          :src="imagePreview.url"
          alt="预览图片"
          class="image-viewer-img"
          :style="{ transform: `translate(${imagePreview.tx}px, ${imagePreview.ty}px) scale(${imagePreview.scale})` }"
          @mousedown="onDragStart"
          @mousemove="onDragMove"
          @mouseup="onDragEnd"
          @mouseleave="onDragEnd"
          @dblclick.stop="resetPreviewTransform"
        />
        <span class="image-viewer-tips">点击任意处关闭</span>
      </div>

      <div
        v-if="shouldShowFinalProcessToggle"
        class="process-toggle-panel"
        :class="{ 'process-toggle-panel--expanded': assistantDetailsExpanded }"
      >
        <div class="process-toggle-panel__copy">
          <div class="process-toggle-panel__title">
            {{ assistantDetailsExpanded ? '过程详情已展开' : '过程详情已收起' }}
          </div>
          <div class="process-toggle-panel__meta">{{ finalProcessToggleHint }}</div>
        </div>
        <button
          type="button"
          class="process-toggle-panel__button"
          @click="assistantDetailsExpanded = !assistantDetailsExpanded"
        >
          {{ assistantDetailsButtonLabel }}
        </button>
      </div>

      <TaskPanel
        v-if="!isUser && hasTaskSnapshot && shouldShowProcessDetails"
        :task-snapshot="props.message.taskSnapshot"
      />

      <div v-if="!isUser && hasAssistantSummary && shouldShowProcessDetails" class="summary-panel">
        <div class="summary-panel__header">
          <div>
            <div class="summary-panel__title">本次诊断摘要</div>
            <div v-if="assistantDetailsHint" class="summary-panel__subtitle">详情里可查看：{{ assistantDetailsHint }}</div>
          </div>
          <button
            v-if="shouldShowInlineAssistantDetailsToggle"
            class="summary-panel__toggle"
            @click="assistantDetailsExpanded = !assistantDetailsExpanded"
          >
            {{ assistantDetailsButtonLabel }}
          </button>
        </div>

        <div v-if="assistantSummaryMetrics.length" class="summary-panel__metrics">
          <div
            v-for="metric in assistantSummaryMetrics"
            :key="metric.label"
            class="summary-panel__metric"
            :class="`summary-panel__metric--${metric.tone}`"
          >
            <div class="summary-panel__metric-label">{{ metric.label }}</div>
            <div class="summary-panel__metric-value">{{ metric.value }}</div>
          </div>
        </div>

        <div v-if="assistantSummaryFindings.length" class="summary-panel__section">
          <div class="summary-panel__section-title">关键判断</div>
          <div
            v-for="item in assistantSummaryFindings"
            :key="item.id"
            class="summary-panel__finding"
          >
            <span class="summary-panel__finding-text">{{ item.text }}</span>
            <span class="summary-panel__finding-meta">{{ item.evidenceCount }} 条依据</span>
          </div>
        </div>

        <div v-if="assistantSummaryRisks.length" class="summary-panel__section">
          <div class="summary-panel__section-title">需要留意</div>
          <div
            v-for="risk in assistantSummaryRisks"
            :key="risk"
            class="summary-panel__risk"
          >
            {{ risk }}
          </div>
        </div>
      </div>

      <div v-if="!isUser && evidenceSourceCards.length && shouldShowProcessDetails" class="evidence-strip">
        <div class="evidence-strip__header">
          <span>依据与来源</span>
          <span>{{ normalizedEvidences.length }} 条</span>
        </div>
        <div class="evidence-strip__list">
          <div
            v-for="item in evidenceSourceCards"
            :key="item.id"
            class="evidence-strip__item"
          >
            <div class="evidence-strip__title">{{ item.title }}</div>
            <div class="evidence-strip__meta">{{ item.meta }}</div>
            <div class="evidence-strip__summary">{{ item.summary }}</div>
          </div>
        </div>
      </div>

      <div v-if="!isUser && hasWorkflowContractPanel && shouldShowProcessDetails" class="phase4-panel">
        <div class="phase4-panel__header">
          <div>
            <div class="phase4-panel__title">结构化诊断结果</div>
            <div class="phase4-panel__subtitle">来自结构化诊断产物，供证据链、报告和后续分析复用</div>
          </div>
          <span v-if="phase4GateBadge" class="phase4-panel__badge" :class="`phase4-panel__badge--${phase4GateBadge.tone}`">
            {{ phase4GateBadge.label }}
          </span>
        </div>

        <div v-if="phase4Summary" class="phase4-panel__summary">
          {{ phase4Summary }}
        </div>

        <div v-if="phase4MetricCards.length" class="phase4-panel__metrics">
          <div v-for="metric in phase4MetricCards" :key="metric.label" class="phase4-panel__metric">
            <div class="phase4-panel__metric-label">{{ metric.label }}</div>
            <div class="phase4-panel__metric-value">{{ metric.value }}</div>
          </div>
        </div>

        <div v-if="phase4Findings.length" class="phase4-panel__section">
          <div class="phase4-panel__section-title">关键结论</div>
          <div v-for="finding in phase4Findings" :key="finding.id" class="phase4-panel__item">
            <div class="phase4-panel__item-main">{{ finding.title }}</div>
            <div class="phase4-panel__item-meta">
              {{ finding.severityLabel }} · {{ finding.evidenceCount }} 条证据
            </div>
          </div>
        </div>

        <div v-if="phase4EvidenceItems.length" class="phase4-panel__section">
          <div class="phase4-panel__section-title">证据摘要</div>
          <div v-for="evidence in phase4EvidenceItems" :key="evidence.id" class="phase4-panel__item">
            <div class="phase4-panel__item-main">{{ evidence.title }}</div>
            <div class="phase4-panel__item-meta">{{ evidence.typeLabel }} · {{ evidence.summary }}</div>
          </div>
        </div>

        <div v-if="phase4TimelineItems.length" class="phase4-panel__section">
          <div class="phase4-panel__section-title">诊断时间线</div>
          <div v-for="item in phase4TimelineItems" :key="item.id" class="phase4-panel__timeline-item">
            <span class="phase4-panel__timeline-dot"></span>
            <div>
              <div class="phase4-panel__item-main">{{ item.event }}</div>
              <div class="phase4-panel__item-meta">{{ item.typeLabel }} · {{ item.timeLabel }}</div>
            </div>
          </div>
        </div>

        <div v-if="phase4GovernanceItems.length" class="phase4-panel__section">
          <div class="phase4-panel__section-title">治理建议</div>
          <div v-for="item in phase4GovernanceItems" :key="item" class="phase4-panel__governance-item">
            {{ item }}
          </div>
        </div>

        <div v-if="phase4Artifacts.length" class="phase4-panel__section">
          <div class="phase4-panel__section-title">报告 / 产物</div>
          <div v-for="artifact in phase4Artifacts" :key="artifact.id" class="phase4-panel__item">
            <div class="phase4-panel__item-main">{{ artifact.title }}</div>
            <div class="phase4-panel__item-meta">{{ artifact.typeLabel }}{{ artifact.path ? ` · ${artifact.path}` : '' }}</div>
          </div>
        </div>
      </div>

      <div v-if="!isUser && hasAssistantDetails && assistantDetailsExpanded && shouldShowProcessDetails" class="details-panel">

      <div v-if="false && !isUser && workflowStages.length" class="workflow-panel">
        <div class="workflow-panel__title">Workflow Stages</div>
        <div class="workflow-panel__stages">
          <span
            v-for="stage in workflowStages"
            :key="stage.key"
            class="workflow-panel__chip"
            :class="{
              'workflow-panel__chip--active': stage.key === currentWorkflowStage,
              'workflow-panel__chip--completed': stage.status === 'completed'
            }"
          >
            {{ stage.label }}
          </span>
        </div>
        <div v-if="workflowStageDetails.length" class="workflow-panel__details">
          <div
            v-for="detail in workflowStageDetails"
            :key="detail.stage"
            class="workflow-panel__detail"
          >
            <span class="workflow-panel__detail-name">{{ detail.label }}</span>
            <span class="workflow-panel__detail-meta">
              {{ detail.statusLabel }} · {{ detail.toolCount }} tools · {{ detail.durationLabel }}
            </span>
          </div>
        </div>
      </div>

      <div v-if="false && !isUser && toolLifecycleEntries.length" class="lifecycle-panel">
        <div class="lifecycle-panel__header">
          <span class="lifecycle-panel__title">Tool Lifecycle</span>
          <span class="lifecycle-panel__count">{{ toolLifecycleEntries.length }} steps</span>
        </div>
        <div class="lifecycle-panel__items">
          <div
            v-for="(item, index) in toolLifecycleEntries"
            :key="`${item.runId || item.tool}-${item.event}-${index}`"
            class="lifecycle-panel__item"
            :class="[
              `lifecycle-panel__item--${item.event}`,
              { 'lifecycle-panel__item--active': activeLifecycleRunId === item.runId }
            ]"
            @click="focusToolLifecycle(item)"
          >
            <div class="lifecycle-panel__row">
              <span class="lifecycle-panel__event">{{ item.eventLabel }}</span>
              <span class="lifecycle-panel__tool">{{ item.tool }}</span>
              <span class="lifecycle-panel__stage">{{ item.stageLabel }}</span>
            </div>
            <div class="lifecycle-panel__meta">
              <span>{{ item.timeLabel }}</span>
              <span v-if="item.durationLabel">{{ item.durationLabel }}</span>
              <span v-if="item.runId">run {{ item.runId }}</span>
            </div>
            <div v-if="item.evidenceCount || item.findingCount" class="lifecycle-panel__links">
              <span v-if="item.evidenceCount">evidence {{ item.evidenceCount }}</span>
              <span v-if="item.findingCount">finding {{ item.findingCount }}</span>
            </div>
            <div v-if="item.preview" class="lifecycle-panel__preview">{{ item.preview }}</div>
          </div>
        </div>
      </div>

      <div
        v-if="false && !isUser && evidenceQualityGate"
        class="quality-panel"
        :class="`quality-panel--${evidenceQualityGate.gate}`"
      >
        <div class="quality-panel__header">
          <span class="quality-panel__title">Evidence Gate</span>
          <span class="quality-panel__meta">
            {{ evidenceQualityGate.gateLabel }} 路 coverage {{ evidenceQualityGate.coverageLabel }}
          </span>
        </div>
        <div v-if="evidenceQualityGate.notice" class="quality-panel__notice">
          {{ evidenceQualityGate.notice }}
        </div>
        <div class="quality-panel__metrics">
          <span class="quality-panel__metric">
            findings {{ evidenceQualityGate.totalFindings }}
          </span>
          <span class="quality-panel__metric">
            grounded {{ evidenceQualityGate.linkedFindings }}
          </span>
          <span class="quality-panel__metric">
            unsupported {{ evidenceQualityGate.unsupportedFindings }}
          </span>
          <span class="quality-panel__metric">
            low confidence {{ evidenceQualityGate.lowConfidenceFindings }}
          </span>
        </div>
      </div>

      <div v-if="false && !isUser && evidenceCoverageScorecard" class="scorecard-panel">
        <div class="scorecard-panel__header">
          <span class="scorecard-panel__title">Evidence Coverage Scorecard</span>
          <span class="scorecard-panel__grade" :class="`scorecard-panel__grade--${evidenceCoverageScorecard.grade.toLowerCase()}`">
            {{ evidenceCoverageScorecard.grade }}
          </span>
        </div>
        <div class="scorecard-panel__metrics">
          <div
            v-for="metric in evidenceCoverageScorecard.metrics"
            :key="metric.label"
            class="scorecard-panel__metric"
          >
            <div class="scorecard-panel__metric-label">{{ metric.label }}</div>
            <div class="scorecard-panel__metric-value">{{ metric.value }}</div>
          </div>
        </div>
      </div>

      <div v-if="!isUser && safeActionGuard" class="safe-action-panel" :class="`safe-action-panel--${safeActionGuard.publicationStatus}`">
        <div class="safe-action-panel__header">
          <span class="safe-action-panel__title">输出处理状态</span>
          <span class="safe-action-panel__badge" :class="`safe-action-panel__badge--${safeActionGuard.publicationStatus}`">
            {{ safeActionGuard.publicationStatusLabel }}
          </span>
        </div>
        <div class="safe-action-panel__summary">
          <div><strong>工具：</strong> {{ safeActionGuard.toolName }}</div>
          <div><strong>动作：</strong> {{ safeActionGuard.action }}</div>
          <div><strong>原始文件名：</strong> {{ safeActionGuard.targetFilename }}</div>
          <div><strong>实际文件名：</strong> {{ safeActionGuard.finalFilename }}</div>
        </div>
        <div v-if="safeActionGuard.statusText" class="safe-action-panel__notice">
          {{ safeActionGuard.statusText }}
        </div>
        <div v-if="safeActionGuard.reviewReasons.length" class="safe-action-panel__reasons">
          <div class="safe-action-panel__reasons-title">需要复核的原因</div>
          <ul class="safe-action-panel__reasons-list">
            <li v-for="(reason, index) in safeActionGuard.reviewReasons" :key="`${safeActionGuard.toolName}-${index}`">
              {{ reason }}
            </li>
          </ul>
        </div>
      </div>

      <div v-if="false && !isUser && actionLedger.length" class="action-ledger-panel">
        <div class="action-ledger-panel__header">
          <span class="action-ledger-panel__title">Action Ledger</span>
          <span class="action-ledger-panel__count">{{ actionLedger.length }} items</span>
        </div>
        <div
          v-for="(item, index) in actionLedger"
          :key="`${item.toolName}-${item.finalFilename}-${index}`"
          class="action-ledger-panel__item"
          :class="`action-ledger-panel__item--${item.publicationStatus}`"
        >
          <div class="action-ledger-panel__item-header">
            <span class="action-ledger-panel__tool">{{ item.toolName }}</span>
            <span class="action-ledger-panel__badge" :class="`action-ledger-panel__badge--${item.publicationStatus}`">
              {{ item.publicationStatusLabel }}
            </span>
          </div>
          <div class="action-ledger-panel__meta">
            <span>action {{ item.action }}</span>
            <span>target {{ item.targetFilename }}</span>
            <span>final {{ item.finalFilename }}</span>
          </div>
          <div v-if="item.statusText" class="action-ledger-panel__notice">{{ item.statusText }}</div>
        </div>
      </div>

      <div v-if="false && !isUser && diagnosticTimeline.length" class="timeline-panel">
        <div class="timeline-panel__title">Diagnostic Timeline</div>
        <div
          v-for="item in diagnosticTimeline"
          :key="item.stage"
          class="timeline-panel__item"
        >
          <div class="timeline-panel__header">
            <span class="timeline-panel__stage">{{ item.label }}</span>
            <span class="timeline-panel__meta">
              {{ item.statusLabel }} · {{ item.evidenceCount }} evidences · {{ item.findingCount }} findings
            </span>
          </div>
          <div v-if="item.summary" class="timeline-panel__summary">
            {{ item.summary }}
          </div>
          <div v-if="item.evidenceIds.length" class="timeline-panel__links">
            <span
              v-for="evidenceId in item.evidenceIds"
              :key="evidenceId"
              class="timeline-panel__tag"
            >
              {{ evidenceId }}
            </span>
          </div>
        </div>
      </div>

      <details v-if="!isUser && executionTimeline.length" class="details-section details-section--timeline">
        <summary class="details-section__summary">
          <span class="details-section__title">诊断过程时间线</span>
          <span class="details-section__meta">{{ filteredExecutionTimeline.length }} / {{ executionTimeline.length }} 个节点</span>
        </summary>
        <div class="details-section__body">
      <div class="unified-timeline-panel">
        <div class="unified-timeline-panel__header">
          <span class="unified-timeline-panel__title">诊断过程时间线</span>
          <div class="unified-timeline-panel__header-actions">
            <span class="unified-timeline-panel__count">{{ filteredExecutionTimeline.length }} / {{ executionTimeline.length }} 个节点</span>
            <button class="unified-timeline-panel__action" @click.stop="toggleBadCaseTimelineMode">
              {{ badCaseTimelineOnly ? '显示全部' : '只看问题点' }}
            </button>
            <button class="unified-timeline-panel__action" @click.stop="toggleAllTimelineItems">
              {{ areAllTimelineItemsExpanded ? '全部收起' : '全部展开' }}
            </button>
            <button class="unified-timeline-panel__action unified-timeline-panel__action--primary" @click.stop="copyExecutionTimeline">
              复制时间线
            </button>
          </div>
        </div>
        <div class="unified-timeline-panel__items">
          <div
            v-for="(item, index) in filteredExecutionTimeline"
            :key="`${item.kind}-${item.id || index}`"
            class="unified-timeline-panel__item"
            :class="[
              `unified-timeline-panel__item--${item.kind}`,
              `unified-timeline-panel__item--risk-${item.riskLevel}`,
              {
                'unified-timeline-panel__item--active': item.isActive,
                'unified-timeline-panel__item--governance': item.isGovernanceRelevant
              }
            ]"
            @click="toggleTimelineItem(item)"
          >
            <div class="unified-timeline-panel__line" v-if="index !== filteredExecutionTimeline.length - 1"></div>
            <div class="unified-timeline-panel__dot"></div>
            <div class="unified-timeline-panel__content">
              <div class="unified-timeline-panel__row">
                <span class="unified-timeline-panel__kind">{{ item.kindLabel }}</span>
                <span class="unified-timeline-panel__name">{{ item.title }}</span>
                <span
                  v-if="item.riskLevel !== 'none'"
                  class="unified-timeline-panel__risk"
                  :class="`unified-timeline-panel__risk--${item.riskLevel}`"
                >
                  {{ item.riskLabel }}
                </span>
                <span v-if="item.timeLabel" class="unified-timeline-panel__time">{{ item.timeLabel }}</span>
              </div>
              <div v-if="item.summary" class="unified-timeline-panel__summary">{{ item.summary }}</div>
              <div class="unified-timeline-panel__meta">
                <span v-for="meta in item.meta" :key="`${item.id}-${meta}`">{{ meta }}</span>
              </div>
              <div v-if="item.linkBadges.length" class="unified-timeline-panel__links">
                <span
                  v-for="badge in item.linkBadges"
                  :key="`${item.id}-${badge}`"
                  class="unified-timeline-panel__badge"
                >
                  {{ badge }}
                </span>
              </div>
              <div v-if="item.isExpanded && item.detailLines.length" class="unified-timeline-panel__details">
                <div
                  v-for="(detail, detailIndex) in item.detailLines"
                  :key="`${item.id}-${detailIndex}`"
                  class="unified-timeline-panel__detail"
                >
                  {{ detail }}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
        </div>
      </details>

      <div v-if="false && !isUser && badCaseReplay.items.length" class="replay-panel">
        <div class="replay-panel__title">Bad-Case Replay</div>
        <div v-if="badCaseReplay.risks.length" class="replay-panel__risks">
          <div
            v-for="(risk, index) in badCaseReplay.risks"
            :key="`${risk.type}-${index}`"
            class="replay-panel__risk"
          >
            {{ risk.text }}
          </div>
        </div>
        <div
          v-for="item in badCaseReplay.items"
          :key="item.findingId"
          class="replay-panel__item"
          :class="{ 'replay-panel__item--active': activeFindingId === item.findingId }"
          @click="focusReplayItem(item)"
        >
          <div class="replay-panel__header">
            <span class="replay-panel__stage">{{ item.stageLabel }}</span>
            <span class="replay-panel__meta">
              {{ formatConfidenceText(item.confidence) }} · {{ formatSeverityText(item.severity) }} · 匹配分 {{ item.matchScore }}
            </span>
          </div>
          <div class="replay-panel__finding">{{ item.text }}</div>
          <div v-if="item.keywords.length" class="replay-panel__keywords">
            <span
              v-for="keyword in item.keywords"
              :key="keyword"
              class="replay-panel__keyword"
            >
              {{ keyword }}
            </span>
          </div>
          <div v-if="item.evidenceIds.length" class="replay-panel__links">
            <span
              v-for="evidenceId in item.evidenceIds"
              :key="evidenceId"
              class="replay-panel__tag"
            >
              {{ evidenceId }}
            </span>
          </div>
        </div>
      </div>

      <details v-if="!isUser && governanceSuggestions.items.length" class="details-section details-section--governance">
        <summary class="details-section__summary">
          <span class="details-section__title">系统改进建议</span>
          <span class="details-section__meta">{{ governanceSuggestions.items.length }} 个方向</span>
        </summary>
        <div class="details-section__body">
      <div class="governance-panel">
        <div class="governance-panel__title">系统改进建议</div>
        <div
          v-for="item in governanceSuggestions.items"
          :key="item.layer"
          class="governance-panel__item"
          :class="{ 'governance-panel__item--active': activeGovernanceLayer === item.layer }"
          @click="focusGovernanceLayer(item.layer)"
        >
          <div class="governance-panel__header">
            <span class="governance-panel__layer">{{ item.layerLabel }}</span>
            <span class="governance-panel__count">
              {{ item.hitCount }} hits · priority {{ item.priority }}
            </span>
          </div>
          <div class="governance-panel__reason"><span class="governance-panel__label">发现的问题：</span>{{ item.reason }}</div>
          <div class="governance-panel__action"><span class="governance-panel__label">建议动作：</span>{{ item.action }}</div>
          <button
            v-if="item.startNode"
            class="governance-panel__jump"
            @click.stop="focusTimelineNodeById(item.startNode.id)"
          >
            从 {{ item.startNode.kindLabel }} / {{ item.startNode.title }} 开始看
          </button>
        </div>
      </div>
        </div>
      </details>

      <div v-if="false && !isUser && governanceLedger.items.length" class="ledger-panel">
        <div class="ledger-panel__title">Governance Ledger</div>
        <div class="ledger-panel__summary">
          <span
            v-for="metric in governanceLedger.summary"
            :key="metric.label"
            class="ledger-panel__metric"
          >
            {{ metric.label }} {{ metric.value }}
          </span>
        </div>
        <div
          v-for="item in governanceLedger.items"
          :key="`${item.layer}-${item.priority}`"
          class="ledger-panel__item"
          :class="{ 'ledger-panel__item--active': activeGovernanceLayer === item.layer }"
          @click="focusGovernanceLayer(item.layer)"
        >
          <div class="ledger-panel__header">
            <span class="ledger-panel__name">{{ item.title }}</span>
            <span class="ledger-panel__priority">{{ item.priority }}</span>
          </div>
          <div class="ledger-panel__meta">
            {{ item.owner }} · {{ item.stage }} · {{ item.triggerCount }} triggers
          </div>
          <div class="ledger-panel__goal">{{ item.goal }}</div>
          <div class="ledger-panel__checklist">
            <div
              v-for="(task, index) in item.checklist"
              :key="`${item.layer}-${index}`"
              class="ledger-panel__task"
            >
              {{ index + 1 }}. {{ task }}
            </div>
          </div>
        </div>
      </div>

      <div v-if="false && !isUser && governanceOptimizationCandidates.length" class="candidate-panel">
        <div class="candidate-panel__title">Optimization Candidates</div>
        <div
          v-for="item in governanceOptimizationCandidates"
          :key="`${item.layer}-${item.source}`"
          class="candidate-panel__item"
          :class="{ 'candidate-panel__item--active': activeGovernanceLayer === item.layer }"
          @click="focusGovernanceLayer(item.layer)"
        >
          <div class="candidate-panel__header">
            <span class="candidate-panel__name">{{ item.title }}</span>
            <span class="candidate-panel__meta">{{ item.layer }} · {{ item.priority }}</span>
          </div>
          <div class="candidate-panel__reason">{{ item.reason }}</div>
          <div class="candidate-panel__action">{{ item.recommendation }}</div>
          <div class="candidate-panel__signal">来源：{{ item.source }}</div>
        </div>
      </div>

      <div v-if="false && !isUser && governanceLedger.items.length" class="export-panel">
        <div class="export-panel__title">导出治理快照</div>
        <div class="export-panel__actions">
          <button class="export-panel__button" @click="copyGovernanceMarkdown">
            复制 Markdown
          </button>
          <button class="export-panel__button export-panel__button--secondary" @click="copyGovernanceJson">
            复制 JSON
          </button>
          <button class="export-panel__button export-panel__button--tertiary" @click="copyGovernanceDocTemplate">
            复制文档模板
          </button>
          <button class="export-panel__button export-panel__button--report" @click="copyGovernanceWeeklyReport">
            复制周报
          </button>
          <button class="export-panel__button export-panel__button--backlog" @click="copyTechnicalUpgradeBacklog">
            复制技术待办
          </button>
          <button
            class="export-panel__button export-panel__button--quaternary"
            :disabled="governanceSaveState.saving"
            @click="saveGovernanceSnapshot"
          >
            {{ governanceSaveState.saving ? '保存中...' : '保存快照' }}
          </button>
          <button
            class="export-panel__button export-panel__button--ledger"
            :disabled="governanceSaveState.creatingLedger"
            @click="createGovernanceLedgerRecord"
          >
            {{ governanceSaveState.creatingLedger ? '创建中...' : '创建台账记录' }}
          </button>
        </div>
        <div v-if="governanceSaveState.savedPaths.length" class="export-panel__saved">
          <div class="export-panel__saved-title">已保存文件</div>
          <a
            v-for="item in governanceSaveState.savedPaths"
            :key="item.path"
            :href="item.path"
            target="_blank"
            rel="noreferrer"
            class="export-panel__saved-link"
          >
            {{ item.label }}: {{ item.path }}
          </a>
        </div>
        <div class="export-panel__history">
          <div class="export-panel__history-header">
            <div class="export-panel__saved-title">最近快照</div>
            <button
              class="export-panel__link-button"
              :disabled="governanceSaveState.loadingHistory"
              @click="loadGovernanceHistory"
            >
              {{ governanceSaveState.loadingHistory ? '加载中...' : '刷新' }}
            </button>
          </div>
          <div v-if="governanceSaveState.historyItems.length" class="export-panel__history-list">
            <div
              v-for="item in governanceSaveState.historyItems"
              :key="item.snapshot_id"
              class="export-panel__history-item"
            >
              <div class="export-panel__history-meta">
                <span>{{ item.created_at || item.snapshot_id }}</span>
                <span>{{ item.thread_hint }}</span>
              </div>
              <div class="export-panel__history-links">
                <a v-if="item.markdown_path" :href="item.markdown_path" target="_blank" rel="noreferrer" class="export-panel__saved-link">Markdown</a>
                <a v-if="item.json_path" :href="item.json_path" target="_blank" rel="noreferrer" class="export-panel__saved-link">JSON</a>
                <a v-if="item.doc_template_path" :href="item.doc_template_path" target="_blank" rel="noreferrer" class="export-panel__saved-link">文档模板</a>
              </div>
            </div>
          </div>
          <div v-else class="export-panel__history-empty">暂时还没有保存过的快照。</div>
        </div>
        <div class="export-panel__history">
          <div class="export-panel__history-header">
            <div class="export-panel__saved-title">台账记录</div>
            <button
              class="export-panel__link-button"
              :disabled="governanceSaveState.loadingLedger"
              @click="loadGovernanceLedger"
            >
              {{ governanceSaveState.loadingLedger ? '加载中...' : '刷新' }}
            </button>
          </div>
          <div class="export-panel__filter-bar">
            <select
              class="export-panel__input export-panel__select"
              :value="governanceSaveState.ledgerFilters.status"
              @change="setLedgerFilter('status', $event.target.value)"
            >
              <option value="">全部状态</option>
              <option value="open">待处理</option>
              <option value="in_progress">处理中</option>
              <option value="blocked">已阻塞</option>
              <option value="verified">已确认</option>
            </select>
            <select
              class="export-panel__input export-panel__select"
              :value="governanceSaveState.ledgerFilters.priority"
              @change="setLedgerFilter('priority', $event.target.value)"
            >
              <option value="">全部优先级</option>
              <option value="P0">P0</option>
              <option value="P1">P1</option>
              <option value="P2">P2</option>
            </select>
            <input
              class="export-panel__input"
              type="text"
              :value="governanceSaveState.ledgerFilters.owner"
              placeholder="筛选负责人"
              @input="setLedgerFilter('owner', $event.target.value)"
            />
            <input
              class="export-panel__input"
              type="text"
              :value="governanceSaveState.ledgerFilters.tag"
              placeholder="筛选标签"
              @input="setLedgerFilter('tag', $event.target.value)"
            />
          </div>
          <div v-if="governanceLedgerSummary.total" class="export-panel__ledger-summary">
            <span class="export-panel__metric">总数 {{ governanceLedgerSummary.total }}</span>
            <span
              v-for="item in governanceLedgerSummary.statusItems"
              :key="`status-${item.key}`"
              class="export-panel__metric"
            >
              {{ getLedgerStatusLabel(item.key) }} {{ item.value }}
            </span>
            <span
              v-for="item in governanceLedgerSummary.priorityItems"
              :key="`priority-${item.key}`"
              class="export-panel__metric"
            >
              {{ item.key }} {{ item.value }}
            </span>
          </div>
          <div v-if="governanceLedgerKanban.total" class="export-panel__kanban">
            <div
              v-for="column in governanceLedgerKanban.columns"
              :key="column.key"
              class="export-panel__kanban-column"
            >
              <div class="export-panel__kanban-title">{{ getLedgerStatusLabel(column.key) }} {{ column.items.length }}</div>
              <div
                v-for="item in column.items"
                :key="`${column.key}-${item.record_id}`"
                class="export-panel__kanban-card"
              >
                <div class="export-panel__kanban-card-title">{{ item.owner || '未分配' }} · {{ item.priority || 'P2' }}</div>
                <div class="export-panel__kanban-card-meta">{{ item.next_action || '暂无下一步动作' }}</div>
                <div class="export-panel__kanban-card-meta">{{ item.due_date || '暂无截止日期' }}</div>
              </div>
            </div>
          </div>
          <div v-if="governanceSaveState.ledgerItems.length" class="export-panel__history-list">
            <div
              v-for="item in governanceSaveState.ledgerItems"
              :key="item.record_id"
              class="export-panel__history-item"
            >
              <div class="export-panel__history-meta">
                <span>{{ item.created_at || item.record_id }}</span>
                <span>{{ item.thread_hint }}</span>
              </div>
              <div class="export-panel__history-meta">
                <span>{{ item.risk_count }} 个风险</span>
                <span>{{ item.item_count }} 条记录</span>
              </div>
              <div class="export-panel__history-meta">
                <span>状态：{{ getLedgerStatusLabel(item.status || 'open') }}</span>
                <span>负责人：{{ item.owner || '未分配' }}</span>
              </div>
              <div class="export-panel__history-meta">
                <span>优先级：{{ item.priority || 'P2' }}</span>
                <span>截止时间：{{ item.due_date || '暂无' }}</span>
              </div>
              <div v-if="item.tags?.length" class="export-panel__history-tags">
                <span
                  v-for="tag in item.tags"
                  :key="`${item.record_id}-${tag}`"
                  class="export-panel__history-tag"
                >
                  {{ tag }}
                </span>
              </div>
              <div class="export-panel__ledger-editor">
                <select
                  class="export-panel__input export-panel__select"
                  :value="getLedgerDraft(item).status"
                  @change="setLedgerDraftField(item.record_id, 'status', $event.target.value)"
                >
                  <option value="open">待处理</option>
                  <option value="in_progress">处理中</option>
                  <option value="blocked">已阻塞</option>
                  <option value="verified">已确认</option>
                </select>
                <select
                  class="export-panel__input export-panel__select"
                  :value="getLedgerDraft(item).priority"
                  @change="setLedgerDraftField(item.record_id, 'priority', $event.target.value)"
                >
                  <option value="P0">P0</option>
                  <option value="P1">P1</option>
                  <option value="P2">P2</option>
                </select>
                <input
                  class="export-panel__input"
                  type="text"
                  :value="getLedgerDraft(item).owner"
                  placeholder="负责人"
                  @input="setLedgerDraftField(item.record_id, 'owner', $event.target.value)"
                />
                <input
                  class="export-panel__input"
                  type="date"
                  :value="getLedgerDraft(item).due_date"
                  @input="setLedgerDraftField(item.record_id, 'due_date', $event.target.value)"
                />
                <input
                  class="export-panel__input"
                  type="text"
                  :value="getLedgerDraft(item).next_action"
                  placeholder="下一步动作"
                  @input="setLedgerDraftField(item.record_id, 'next_action', $event.target.value)"
                />
                <input
                  class="export-panel__input"
                  type="text"
                  :value="getLedgerDraft(item).verified_result"
                  placeholder="确认结果"
                  @input="setLedgerDraftField(item.record_id, 'verified_result', $event.target.value)"
                />
                <input
                  class="export-panel__input export-panel__input--wide"
                  type="text"
                  :value="getLedgerDraft(item).tags_text"
                  placeholder="标签：rag, prompt, workflow"
                  @input="setLedgerDraftField(item.record_id, 'tags_text', $event.target.value)"
                />
                <button
                  class="export-panel__button export-panel__button--secondary"
                  :disabled="isLedgerUpdating(item.record_id)"
                  @click="saveLedgerRecord(item.record_id)"
                >
                  {{ isLedgerUpdating(item.record_id) ? '保存中...' : '保存记录' }}
                </button>
              </div>
              <div class="export-panel__history-links">
                <a :href="item.detail_path" target="_blank" rel="noreferrer" class="export-panel__saved-link">打开台账 JSON</a>
              </div>
            </div>
          </div>
          <div v-else class="export-panel__history-empty">暂时还没有台账记录。</div>
        </div>
        <details class="export-panel__details">
          <summary>Markdown 预览</summary>
          <pre class="export-panel__content">{{ governanceExportMarkdown }}</pre>
        </details>
        <details class="export-panel__details">
          <summary>JSON 预览</summary>
          <pre class="export-panel__content">{{ governanceExportJson }}</pre>
        </details>
        <details class="export-panel__details">
          <summary>文档模板预览</summary>
          <pre class="export-panel__content">{{ governanceDocTemplate }}</pre>
        </details>
        <details class="export-panel__details">
          <summary>周报预览</summary>
          <pre class="export-panel__content">{{ governanceWeeklyReport }}</pre>
        </details>
        <details class="export-panel__details">
          <summary>技术待办预览</summary>
          <pre class="export-panel__content">{{ technicalUpgradeBacklog }}</pre>
        </details>
      </div>

      <details v-if="!isUser && evidenceFindings.length" class="details-section details-section--findings">
        <summary class="details-section__summary">
          <span class="details-section__title">结论依据</span>
          <span class="details-section__meta">{{ evidenceFindings.length }} 条判断</span>
        </summary>
        <div class="details-section__body">
      <div class="evidence-panel">
        <div class="evidence-panel__title">结论依据</div>
        <div
          v-for="finding in evidenceFindings"
          :key="finding.finding_id || finding.text"
          class="evidence-panel__finding"
          :class="{ 'evidence-panel__finding--active': activeFindingId === (finding.finding_id || null) }"
          @click="setActiveFinding(finding.finding_id || null)"
        >
          <div class="evidence-panel__finding-text">{{ finding.text }}</div>
          <div v-if="getFindingEvidenceCards(finding.finding_id).length" class="evidence-panel__cards">
            <div
              v-for="item in getFindingEvidenceCards(finding.finding_id)"
              :key="item.evidence_id || item.source_locator || item.title"
              class="evidence-panel__card"
              :class="[
                `evidence-panel__card--${item.family || 'generic'}`,
                { 'evidence-panel__card--highlighted': isEvidenceHighlighted(item.evidence_id) }
              ]"
            >
              <div class="evidence-panel__card-header">
                <span class="evidence-panel__card-name">{{ getEvidenceTitle(item) }}</span>
                <div class="evidence-panel__card-badges">
                  <span class="evidence-panel__tag">{{ getEvidenceKindLabel(item.kind) }}</span>
                  <span class="evidence-panel__tag evidence-panel__tag--family">{{ getEvidenceFamilyLabel(item.family) }}</span>
                  <span class="evidence-panel__tag evidence-panel__tag--channel">{{ getEvidenceChannelLabel(item.channel) }}</span>
                </div>
              </div>
              <div class="evidence-panel__card-summary">{{ getEvidenceReadableSummary(item) }}</div>
              <div class="evidence-panel__card-meta">
                <span>阶段：{{ getEvidenceStageLabel(item.stage) }}</span>
                <span>工具：{{ getToolDisplayName(item.tool_name) }}</span>
              </div>
              <div class="evidence-panel__card-meta">
                <span>来源：{{ getEvidenceSourceLabel(item) }}</span>
              </div>
              <details v-if="getEvidenceDetailLines(item).length || getEvidenceRawPreview(item)" class="evidence-panel__card-details">
                <summary class="evidence-panel__card-detail">查看详细依据</summary>
                <div
                  v-for="(detail, index) in getEvidenceDetailLines(item)"
                  :key="`${item.evidence_id || item.title}-${index}`"
                  class="evidence-panel__card-detail"
                >
                  {{ detail }}
                </div>
                <div v-if="getEvidenceRawPreview(item)" class="evidence-panel__card-detail">
                  原始内容：{{ getEvidenceRawPreview(item) }}
                </div>
              </details>
            </div>
          </div>
        </div>
      </div>
        </div>
      </details>

      <details v-if="!isUser && normalizedEvidences.length" class="details-section details-section--catalog">
        <summary class="details-section__summary">
          <span class="details-section__title">依据清单</span>
          <span class="details-section__meta">{{ filteredNormalizedEvidences.length }} / {{ normalizedEvidences.length }} 条</span>
        </summary>
        <div class="details-section__body">
      <div class="evidence-catalog-panel">
        <div class="evidence-catalog-panel__header">
          <span class="evidence-catalog-panel__title">依据清单</span>
          <div class="evidence-catalog-panel__header-actions">
            <span class="evidence-catalog-panel__count">{{ filteredNormalizedEvidences.length }} / {{ normalizedEvidences.length }} 条</span>
            <button
              v-if="hasActiveWorkbenchFocus"
              class="evidence-catalog-panel__clear"
              @click="clearWorkbenchFocus"
            >
              清除筛选
            </button>
          </div>
        </div>
        <div class="evidence-catalog-panel__filters">
          <button
            class="evidence-catalog-panel__filter"
            :class="{ 'evidence-catalog-panel__filter--active': evidenceCatalogFilter.family === 'all' }"
            @click="setEvidenceCatalogFilter('family', 'all')"
          >
            类型全部
          </button>
          <button
            v-for="family in evidenceCatalogFamilies"
            :key="`family-${family}`"
            class="evidence-catalog-panel__filter"
            :class="{ 'evidence-catalog-panel__filter--active': evidenceCatalogFilter.family === family }"
            @click="setEvidenceCatalogFilter('family', family)"
          >
            类型 {{ getEvidenceFamilyLabel(family) }}
          </button>
          <button
            class="evidence-catalog-panel__filter"
            :class="{ 'evidence-catalog-panel__filter--active': evidenceCatalogFilter.channel === 'all' }"
            @click="setEvidenceCatalogFilter('channel', 'all')"
          >
            来源全部
          </button>
          <button
            v-for="channel in evidenceCatalogChannels"
            :key="`channel-${channel}`"
            class="evidence-catalog-panel__filter"
            :class="{ 'evidence-catalog-panel__filter--active': evidenceCatalogFilter.channel === channel }"
            @click="setEvidenceCatalogFilter('channel', channel)"
          >
            来源 {{ getEvidenceChannelLabel(channel) }}
          </button>
          <button
            class="evidence-catalog-panel__filter"
            :class="{ 'evidence-catalog-panel__filter--active': evidenceCatalogFilter.stage === 'all' }"
            @click="setEvidenceCatalogFilter('stage', 'all')"
          >
            阶段全部
          </button>
          <button
            v-for="stage in evidenceCatalogStages"
            :key="`stage-${stage}`"
            class="evidence-catalog-panel__filter"
            :class="{ 'evidence-catalog-panel__filter--active': evidenceCatalogFilter.stage === stage }"
            @click="setEvidenceCatalogFilter('stage', stage)"
          >
            阶段 {{ getEvidenceStageLabel(stage) }}
          </button>
        </div>
        <div
          v-for="item in filteredNormalizedEvidences"
          :key="item.evidence_id || item.source_locator || item.title"
          class="evidence-catalog-panel__item"
          :class="{
            'evidence-catalog-panel__item--highlighted': isEvidenceHighlighted(item.evidence_id),
            'evidence-catalog-panel__item--governance': isEvidenceGovernanceHighlighted(item)
          }"
        >
          <div class="evidence-catalog-panel__item-header">
            <span class="evidence-catalog-panel__name">{{ getEvidenceTitle(item) }}</span>
            <div class="evidence-catalog-panel__badges">
              <span class="evidence-catalog-panel__badge">{{ getEvidenceKindLabel(item.kind) }}</span>
              <span class="evidence-catalog-panel__badge evidence-catalog-panel__badge--family">{{ getEvidenceFamilyLabel(item.family) }}</span>
              <span class="evidence-catalog-panel__badge evidence-catalog-panel__badge--channel">{{ getEvidenceChannelLabel(item.channel) }}</span>
            </div>
          </div>
          <div class="evidence-catalog-panel__summary">{{ getEvidenceReadableSummary(item) }}</div>
          <div class="evidence-catalog-panel__meta">
            <span>阶段：{{ getEvidenceStageLabel(item.stage) }}</span>
            <span>工具：{{ getToolDisplayName(item.tool_name) }}</span>
            <span>来源：{{ getEvidenceSourceLabel(item) }}</span>
          </div>
          <details v-if="getEvidenceDetailLines(item).length || getEvidenceRawPreview(item)" class="evidence-catalog-panel__details">
            <summary class="evidence-catalog-panel__detail">查看详细依据</summary>
            <div
              v-for="(detail, index) in getEvidenceDetailLines(item)"
              :key="`${item.evidence_id || item.title}-catalog-${index}`"
              class="evidence-catalog-panel__detail"
            >
              {{ detail }}
            </div>
            <div v-if="getEvidenceRawPreview(item)" class="evidence-catalog-panel__detail">
              原始内容：{{ getEvidenceRawPreview(item) }}
            </div>
          </details>
          <div v-if="item.governance && (item.governance.publication_status || item.governance.report_gate)" class="evidence-catalog-panel__governance">
            <span v-if="item.governance.publication_status">发布状态：{{ getPublicationStatusLabel(item.governance.publication_status) }}</span>
            <span v-if="item.governance.report_gate">发布校验：{{ getReportGateLabel(item.governance.report_gate) }}</span>
            <span v-if="typeof item.governance.release_ready === 'boolean'">可直接发布：{{ item.governance.release_ready ? '是' : '否' }}</span>
          </div>
        </div>
      </div>
        </div>
      </details>

      <div v-if="!isUser && toolEvents.length" class="tool-details" :class="{ 'tool-details--active': hasRunningTool }">
        <button class="tool-details__toggle" @click="toolDetailsExpanded = !toolDetailsExpanded">
          <div class="tool-details__copy">
            <span class="tool-details__title">{{ toolDetailsExpanded ? '收起执行明细' : '查看执行明细' }}</span>
            <span class="tool-details__summary">{{ toolDetailsSummary }}</span>
          </div>
          <span class="tool-details__meta">
            {{ toolEvents.length }} 条
          </span>
          <span class="tool-details__chevron">{{ toolDetailsExpanded ? '⌃' : '⌄' }}</span>
        </button>

        <ul v-if="toolDetailsExpanded" class="tool-events">
          <li
            v-for="(toolEvent, index) in toolEvents"
            :key="toolEvent.key || `${toolEvent.tool || 'tool'}-${index}`"
            class="tool-event"
            :class="`tool-event--${toolEvent.type || 'info'}`"
          >
            <span class="tool-event__icon">{{ toolEvent.type === 'tool_start' ? '◐' : '✔' }}</span>
            <div class="tool-event__body">
              <p class="tool-event__title">{{ toolEvent.label || toolEvent.tool || '工具事件' }}</p>
              <p v-if="toolEvent.summary" class="tool-event__summary">{{ toolEvent.summary }}</p>
              <pre
                v-if="toolEvent.details && toolEvent.details !== toolEvent.summary"
                class="tool-event__details"
              >{{ toolEvent.details }}</pre>
            </div>
          </li>
        </ul>
      </div>
      </div>

      <section v-if="!isUser && hasFinalAnswerContent" class="final-answer-section">
        <div class="final-answer-section__header">最终回答</div>
        <div v-if="processedContent" class="text-container final-answer-section__body">
          <div class="text markdown-content" ref="contentRef" v-html="processedContent"></div>
        </div>

        <!-- 温度图表区域 - 仅助手消息且有图表数据时显示 -->
        <div v-if="showChart" class="chart-container final-answer-section__artifact">
          <canvas ref="chartRef" width="400" height="200"></canvas>
        </div>

        <!-- 在图表区域下方添加普通图片显示 -->
        <div v-if="props.message.imageUrl" class="image-container final-answer-section__artifact">
          <img
            :src="props.message.imageUrl"
            alt="生成的图片"
            class="chat-image-thumb"
            @click="showImage(props.message.imageUrl)"
          />
        </div>

        <!-- 报告查看按钮（自动识别 /reports/*.html） -->
        <div v-if="reportLinks.length" class="report-actions final-answer-section__actions">
          <div class="report-section-header">📄 诊断报告</div>
          <template v-if="reportLinks.length === 1">
            <el-button type="primary" size="small" @click="openReport(reportLinks[0])">
              查看报告
            </el-button>
            <el-button size="small" @click="openReportInNewTab(reportLinks[0])">
              新窗口打开
            </el-button>
          </template>
          <template v-else>
            <el-select v-model="selectedReport" size="small" placeholder="选择报告" style="width: 260px; margin-right: 8px;">
              <el-option v-for="u in reportLinks" :key="u" :label="u" :value="u" />
            </el-select>
            <el-button type="primary" size="small" :disabled="!selectedReport" @click="openReport(selectedReport)">
              查看报告
            </el-button>
            <el-button size="small" :disabled="!selectedReport" @click="openReportInNewTab(selectedReport)">
              新窗口打开
            </el-button>
          </template>
        </div>
        <div v-else-if="hasReportMentionButNoLinks" class="report-actions final-answer-section__actions">
          <div class="report-section-header">📄 诊断报告</div>
          <div class="report-unavailable">报告文件暂不可用</div>
        </div>
      </section>

      <div class="message-footer" v-if="!isUser">
        <button class="copy-button" @click="copyContent" :title="copyButtonTitle">
          <DocumentDuplicateIcon v-if="!copied" class="copy-icon" />
          <CheckIcon v-else class="copy-icon copied" />
        </button>
      </div>
    </div>
  </div>

  <!-- 右侧侧边栏：内嵌报告 -->
  <el-drawer
    v-model="drawerVisible"
    :with-header="true"
    :append-to-body="true"
    :modal="true"
    :size="drawerSize"
    direction="rtl"
    title="报告预览"
  >
    <template #default>
      <div class="report-toolbar">
        <el-button size="small" @click="reloadReport" :disabled="!reportUrl">刷新</el-button>
        <el-button size="small" @click="openReportInNewTab(reportUrl)" :disabled="!reportUrl">新窗口打开</el-button>
        <el-button size="small" @click="toggleDrawerSize">{{ drawerSize === '420px' ? '加宽' : '还原' }}</el-button>
      </div>
      
      <!-- HTML 报告：使用 iframe 渲染 -->
      <template v-if="reportUrl">
        <div class="report-frame-wrap" v-if="!isMarkdownReport" style="height: calc(100vh - 160px);">
          <div v-if="drawerLoading" class="report-loading muted">报告加载中...</div>
          <iframe
            class="report-iframe"
            :src="reportUrl"
            sandbox="allow-scripts allow-same-origin"
            @load="onIframeLoad"
            @error="onIframeError"
            style="width: 100%; height: 100%; border: none;"
          ></iframe>
        </div>
        
        <!-- Markdown 报告：使用 marked 渲染 -->
        <div class="report-frame-wrap" v-else style="height: calc(100vh - 160px); overflow-y: auto;">
          <div v-if="drawerLoading" class="report-loading muted">报告加载中...</div>
          <div v-else class="markdown-report-content" v-html="markdownContent"></div>
        </div>
      </template>
      
      <!-- 当没有报告链接时显示 -->
      <div v-else class="report-empty muted">未检测到报告链接</div>
    </template>
  </el-drawer>
</template>

<script setup>
import { computed, onMounted, nextTick, ref, watch, onUnmounted } from 'vue';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import {
  UserCircleIcon,
  ComputerDesktopIcon,
  DocumentDuplicateIcon,
  CheckIcon,
  PencilSquareIcon,
  ClockIcon
} from '@heroicons/vue/24/outline';
import TaskPanel from './TaskPanel.vue'
import hljs from 'highlight.js';
import 'highlight.js/styles/github-dark.css';
import { ElMessage } from 'element-plus';
import { hasVisibleTaskSnapshot } from '@/utils/taskState'
import { chatAPI } from '@/services/api'
import { extractReportLinks, stripReportMentions, normalizeReportFilename, toReportUrl, isSafeReportUrl } from '@/utils/reportLinks.js'

// 引入外部CSS文件
import '@/assets/ChatMessage.css';

// 后端基础地址（用于跨源加载静态图片），可在 .env 中配置：VITE_BACKEND_BASE="http://localhost:8000"
const BACKEND_BASE = import.meta.env.VITE_BACKEND_BASE || '';

// Props定义
const props = defineProps({
  message: {
    type: Object,
    required: true
  },
  isStream: {
    type: Boolean,
    default: false
  },
  canEdit: {
    type: Boolean,
    default: false
  }
});


// 引入Chart.js
import {
  Chart,
  LineController,
  LineElement,
  PointElement,
  LinearScale,
  Title,
  CategoryScale,
  Legend,
  Tooltip
} from 'chart.js';

// 注册Chart组件
Chart.register(
    LineController, LineElement, PointElement,
    LinearScale, Title, CategoryScale, Legend, Tooltip
);

// 组件状态
const emit = defineEmits(['edit-user-message']);

const contentRef = ref(null);
const copied = ref(false);
const isEditingUserMessage = ref(false);
const editDraft = ref('');
const showEditHistory = ref(false);

const showChart = ref(false);
const chartRef = ref(null);
const chartInstance = ref(null);

const imagePreview = ref({
  visible: false,
  url: '',
  scale: 1,
  tx: 0,
  ty: 0,
  dragging: false,
  lastX: 0,
  lastY: 0
});


// 计算属性
const governanceSaveState = ref({
  saving: false,
  savedPaths: [],
  loadingHistory: false,
  historyItems: [],
  creatingLedger: false,
  loadingLedger: false,
  ledgerItems: [],
  ledgerDrafts: {},
  updatingLedgerIds: [],
  ledgerFilters: {
    status: '',
    priority: '',
    owner: '',
    tag: ''
  },
  ledgerSummary: {
    total: 0,
    status_counts: {},
    priority_counts: {}
  }
});
const governanceLedgerSummary = computed(() => ({
  total: governanceSaveState.value.ledgerSummary?.total || 0,
  statusItems: Object.entries(governanceSaveState.value.ledgerSummary?.status_counts || {}).map(([key, value]) => ({ key, value })),
  priorityItems: Object.entries(governanceSaveState.value.ledgerSummary?.priority_counts || {}).map(([key, value]) => ({ key, value }))
}));
const governanceLedgerKanban = computed(() => {
  const order = ['open', 'in_progress', 'blocked', 'verified'];
  const columns = order.map((key) => ({
    key,
    items: governanceSaveState.value.ledgerItems.filter((item) => (item.status || 'open') === key).slice(0, 4)
  }));
  return {
    total: governanceSaveState.value.ledgerItems.length,
    columns
  };
});
const isUser = computed(() => props.message.role === 'user');
const canEditUserMessage = computed(() => isUser.value && props.canEdit && !props.isStream);
const copyButtonTitle = computed(() => copied.value ? '已复制' : '复制内容');
const editHistoryItems = computed(() => {
  const history = Array.isArray(props.message?.editHistory) ? props.message.editHistory : [];
  return history
    .map((item, index) => {
      const content = String(item?.content || '').trim();
      if (!content) return null;
      const editedAt = item?.editedAt ? new Date(item.editedAt) : null;
      const timeLabel = editedAt && !Number.isNaN(editedAt.getTime())
        ? editedAt.toLocaleString()
        : '';
      return {
        key: `${index}-${item?.editedAt || content.slice(0, 12)}`,
        label: timeLabel ? `版本 ${index + 1} · ${timeLabel}` : `版本 ${index + 1}`,
        content
      };
    })
    .filter(Boolean)
    .reverse();
});
const toolEvents = computed(() => Array.isArray(props.message?.toolEvents) ? props.message.toolEvents : []);
const hasTaskSnapshot = computed(() => hasVisibleTaskSnapshot(props.message?.taskSnapshot));
const currentWorkflowStage = computed(() => props.message?.currentWorkflowStage || null);
const workflowStages = computed(() => {
  const stages = Array.isArray(props.message?.workflowStages) ? props.message.workflowStages : [];
  const stageLabelMap = {
    collect: '1. 收集',
    retrieve: '2. 检索',
    analyze: '3. 分析',
    report: '4. 报告'
  };

  return stages.map((stage) => ({
    key: stage,
    label: stageLabelMap[stage] || stage,
    status: stage === currentWorkflowStage.value ? 'active' : 'completed'
  }));
});
const workflowStageDetails = computed(() => {
  const details = Array.isArray(props.message?.workflowStageDetails) ? props.message.workflowStageDetails : [];
  const stageLabelMap = {
    collect: '收集',
    retrieve: '检索',
    analyze: '分析',
    report: '报告'
  };
  const statusLabelMap = {
    pending: '待开始',
    active: '进行中',
    completed: '已完成'
  };

  return details.map((detail) => ({
    stage: detail.stage,
    label: stageLabelMap[detail.stage] || detail.stage,
    statusLabel: statusLabelMap[detail.status] || detail.status || '未知',
    toolCount: detail.tool_count ?? 0,
    durationLabel: typeof detail.duration_ms === 'number' ? `${detail.duration_ms} ms` : 'n/a'
  }));
});
const toolLifecycleEntries = computed(() => {
  const entries = Array.isArray(props.message?.toolLifecycleLedger)
    ? props.message.toolLifecycleLedger
    : Array.isArray(props.message?.tool_lifecycle_ledger)
      ? props.message.tool_lifecycle_ledger
      : [];
  const stageLabelMap = {
    collect: '收集',
    retrieve: '检索',
    analyze: '分析',
    report: '报告'
  };
  const eventLabelMap = {
    start: 'START',
    end: 'END'
  };

  return entries.map((item) => {
    const explicitEvidenceIds = Array.isArray(item?.evidence_ids) ? item.evidence_ids.filter(Boolean) : [];
    const linkedEvidenceIds = explicitEvidenceIds.length ? explicitEvidenceIds : normalizedEvidences.value
      .filter((evidence) => {
        if (!evidence) return false;
        if (item?.tool && evidence.tool_name && evidence.tool_name !== item.tool) {
          return false;
        }
        if (item?.stage && evidence.stage && evidence.stage !== item.stage) {
          return false;
        }
        return Boolean(evidence.evidence_id);
      })
      .map((evidence) => evidence.evidence_id);
    const explicitFindingIds = Array.isArray(item?.finding_ids) ? item.finding_ids.filter(Boolean) : [];
    const linkedFindingIds = explicitFindingIds.length ? explicitFindingIds : findingLinks.value
      .filter((link) => {
        const evidenceIds = Array.isArray(link?.evidence_ids) ? link.evidence_ids : [];
        const chartIds = Array.isArray(link?.chart_evidence_ids) ? link.chart_evidence_ids : [];
        return [...evidenceIds, ...chartIds].some((evidenceId) => linkedEvidenceIds.includes(evidenceId));
      })
      .map((link) => link?.finding_id)
      .filter(Boolean);
    const startedAt = typeof item?.started_at_ms === 'number' ? `${item.started_at_ms} ms` : null;
    const endedAt = typeof item?.ended_at_ms === 'number' ? `${item.ended_at_ms} ms` : null;
    return {
      runId: item?.run_id || null,
      tool: item?.tool || 'unknown_tool',
      event: item?.event || 'end',
      eventLabel: eventLabelMap[item?.event] || String(item?.event || 'end').toUpperCase(),
      stage: item?.stage || null,
      stageLabel: stageLabelMap[item?.stage] || item?.stage || 'unknown',
      timeLabel: startedAt || endedAt || 'n/a',
      durationLabel: typeof item?.duration_ms === 'number' ? `${item.duration_ms} ms` : '',
      preview: item?.input_preview || item?.result_preview || '',
      evidenceIds: [...new Set(linkedEvidenceIds)],
      findingIds: [...new Set(linkedFindingIds)],
      evidenceCount: [...new Set(linkedEvidenceIds)].length,
      findingCount: [...new Set(linkedFindingIds)].length
    };
  });
});
const evidenceQualityGate = computed(() => {
  const quality = props.message?.evidenceQuality || props.message?.evidence_quality || null;
  if (!quality || typeof quality !== 'object') {
    return null;
  }

  const gateLabelMap = {
    pass: '证据充分',
    review_required: '需人工复核',
    blocked: '证据不足'
  };

  return {
    gate: quality.gate || props.message?.reportGate || props.message?.report_gate || 'pass',
    gateLabel: gateLabelMap[quality.gate] || String(quality.gate || 'pass').toUpperCase(),
    coverageLabel: `${Math.round((Number(quality.coverage_ratio || 0) || 0) * 100)}%`,
    totalFindings: quality.total_findings ?? 0,
    linkedFindings: quality.linked_findings ?? 0,
    unsupportedFindings: quality.unsupported_findings ?? 0,
    lowConfidenceFindings: quality.low_confidence_findings ?? 0,
    notice: props.message?.qualityGateNotice || props.message?.quality_gate_notice || null
  };
});
const safeActionGuard = computed(() => {
  const toolGuard = [...toolEvents.value]
    .reverse()
    .find((item) => item?.actionGuard && typeof item.actionGuard === 'object')?.actionGuard;

  const normalizedGuard = normalizedEvidences.value.find((item) => item?.governance?.action_guard)?.governance?.action_guard
    || null;
  const evidenceGuard = evidences.value.find((item) => item?.metadata?.action_guard)?.metadata?.action_guard
    || evidences.value.find((item) => item?.action_guard)?.action_guard
    || null;

  const raw = toolGuard || normalizedGuard || evidenceGuard;
  if (!raw || typeof raw !== 'object') {
    return null;
  }

  const publicationStatus = String(raw.publication_status || 'draft');
  const badgeLabelMap = {
    published: '可直接使用',
    draft: '先出草稿',
    blocked: '暂不输出'
  };

  return {
    toolName: raw.tool_name || 'unknown_tool',
    action: raw.action === 'publish'
      ? '正式输出'
      : raw.action === 'review'
        ? '复核后输出'
        : raw.action === 'block'
          ? '暂缓输出'
          : (raw.action || '复核后输出'),
    publicationStatus,
    publicationStatusLabel: badgeLabelMap[publicationStatus] || publicationStatus.toUpperCase(),
    targetFilename: raw.target_filename || 'n/a',
    finalFilename: raw.final_filename || 'n/a',
    statusText: raw.status_text || '',
    reviewReasons: Array.isArray(raw.review_reasons) ? raw.review_reasons : []
  };
});
const actionLedger = computed(() => {
  const badgeLabelMap = {
    published: '可直接使用',
    draft: '先出草稿',
    blocked: '暂不输出'
  };

  const toolEntries = toolEvents.value
    .filter((item) => item?.actionGuard && typeof item.actionGuard === 'object')
    .map((item) => item.actionGuard);

  const normalizedEntries = normalizedEvidences.value
    .map((item) => item?.governance?.action_guard || null)
    .filter((item) => item && typeof item === 'object');
  const evidenceEntries = evidences.value
    .map((item) => item?.metadata?.action_guard || item?.action_guard || null)
    .filter((item) => item && typeof item === 'object');

  const merged = [];
  const seen = new Set();

  [...toolEntries, ...normalizedEntries, ...evidenceEntries].forEach((raw) => {
    const key = `${raw.tool_name || 'unknown'}::${raw.final_filename || raw.target_filename || 'n/a'}::${raw.action || 'review'}`;
    if (seen.has(key)) return;
    seen.add(key);
    const publicationStatus = String(raw.publication_status || 'draft');
    merged.push({
      toolName: raw.tool_name || 'unknown_tool',
      action: raw.action === 'publish'
        ? '正式输出'
        : raw.action === 'draft'
          ? '转为草稿'
          : raw.action === 'block'
            ? '阻止输出'
            : (raw.action || '复核后输出'),
      publicationStatus,
      publicationStatusLabel: badgeLabelMap[publicationStatus] || publicationStatus.toUpperCase(),
      targetFilename: raw.target_filename || 'n/a',
      finalFilename: raw.final_filename || 'n/a',
      statusText: raw.status_text || '',
      reviewReasons: Array.isArray(raw.review_reasons) ? raw.review_reasons : []
    });
  });

  return merged;
});
const activeFindingId = ref(null);
const activeGovernanceLayer = ref(null);
const activeLifecycleRunId = ref(null);
const expandedTimelineItemIds = ref([]);
const badCaseTimelineOnly = ref(false);
const assistantDetailsExpanded = ref(false);
const evidenceCatalogFilter = ref({
  family: 'all',
  channel: 'all',
  stage: 'all'
});
const hasActiveWorkbenchFocus = computed(() => (
  Boolean(activeFindingId.value) ||
  Boolean(activeGovernanceLayer.value) ||
  Boolean(activeLifecycleRunId.value) ||
  evidenceCatalogFilter.value.family !== 'all' ||
  evidenceCatalogFilter.value.channel !== 'all' ||
  evidenceCatalogFilter.value.stage !== 'all'
));
const assistantSummaryMetrics = computed(() => {
  const metrics = [];
  const linkedFindings = evidenceQualityGate.value?.linkedFindings
    ?? evidenceFindings.value.filter((finding) => getFindingEvidence(finding?.finding_id).length).length;

  if (workflowStages.value.length) {
    const activeStage = workflowStages.value.find((stage) => stage.key === currentWorkflowStage.value);
    metrics.push({
      label: '当前阶段',
      value: activeStage?.label || `${workflowStages.value.length} 步流程`,
      tone: 'neutral'
    });
  }

  if (evidenceFindings.value.length) {
    metrics.push({
      label: '判断点',
      value: `${linkedFindings}/${evidenceFindings.value.length} 条有依据`,
      tone: linkedFindings === evidenceFindings.value.length ? 'good' : 'warning'
    });
  }

  if (normalizedEvidences.value.length) {
    metrics.push({
      label: '依据数',
      value: `${normalizedEvidences.value.length} 条`,
      tone: 'neutral'
    });
  }

  if (evidenceQualityGate.value) {
    metrics.push({
      label: '可信状态',
      value: evidenceQualityGate.value.gateLabel,
      tone: evidenceQualityGate.value.gate === 'pass'
        ? 'good'
        : evidenceQualityGate.value.gate === 'blocked'
          ? 'danger'
          : 'warning'
    });
  }

  if (safeActionGuard.value) {
    metrics.push({
      label: '输出状态',
      value: safeActionGuard.value.publicationStatusLabel,
      tone: safeActionGuard.value.publicationStatus === 'published'
        ? 'good'
        : safeActionGuard.value.publicationStatus === 'blocked'
          ? 'danger'
          : 'warning'
    });
  }

  return metrics.slice(0, 5);
});
const assistantSummaryFindings = computed(() =>
  evidenceFindings.value
    .slice(0, 3)
    .map((finding) => ({
      id: finding?.finding_id || finding?.text || Math.random().toString(16).slice(2, 8),
      text: finding?.text || '',
      evidenceCount: getFindingEvidenceCards(finding?.finding_id).length
    }))
    .filter((item) => item.text)
);
const assistantSummaryRisks = computed(() => {
  const riskTexts = [];

  if (evidenceQualityGate.value?.notice) {
    riskTexts.push(evidenceQualityGate.value.notice);
  }

  badCaseReplay.value.risks.forEach((item) => {
    if (item?.text && !riskTexts.includes(item.text)) {
      riskTexts.push(item.text);
    }
  });

  return riskTexts.slice(0, 2);
});
const hasAssistantSummary = computed(() => (
  assistantSummaryMetrics.value.length > 0 ||
  assistantSummaryFindings.value.length > 0 ||
  assistantSummaryRisks.value.length > 0 ||
  hasAssistantDetails.value
));
const hasAssistantDetails = computed(() => (
  Boolean(safeActionGuard.value) ||
  executionTimeline.value.length > 0 ||
  evidenceFindings.value.length > 0 ||
  normalizedEvidences.value.length > 0 ||
  toolEvents.value.length > 0 ||
  governanceSuggestions.value.items.length > 0
));
const workflowContract = computed(() => {
  const raw = props.message?.workflowResult || props.message?.workflow_result || props.message?.workflowEnvelope || props.message?.workflow_envelope || null;
  return raw && typeof raw === 'object' ? raw : null;
});
const scenarioContract = computed(() => {
  const raw = props.message?.scenarioResult || props.message?.scenario_result || null;
  return raw && typeof raw === 'object' ? raw : null;
});
const phase4Summary = computed(() => (
  workflowContract.value?.summary ||
  scenarioContract.value?.summary ||
  ''
));
const phase4GateBadge = computed(() => {
  const gate = String(props.message?.reportGate || props.message?.report_gate || '').trim();
  if (!gate) return null;
  const labelMap = { pass: '证据通过', review_required: '需复核', blocked: '证据不足' };
  const toneMap = { pass: 'good', review_required: 'warning', blocked: 'danger' };
  return { label: labelMap[gate] || gate, tone: toneMap[gate] || 'neutral' };
});
const phase4Findings = computed(() => {
  const raw = Array.isArray(props.message?.findings)
    ? props.message.findings
    : Array.isArray(workflowContract.value?.findings)
      ? workflowContract.value.findings
      : [];
  const severityLabelMap = { critical: '严重', high: '高风险', medium: '中风险', low: '低风险' };
  return raw.slice(0, 5).map((item, index) => {
    const evidenceIds = Array.isArray(item?.evidence_ids) ? item.evidence_ids : [];
    return {
      id: item?.id || item?.finding_id || `phase4-finding-${index}`,
      title: item?.title || item?.text || item?.description || '未命名结论',
      severityLabel: severityLabelMap[item?.severity] || item?.severity || '风险未知',
      evidenceCount: evidenceIds.length
    };
  });
});
const phase4EvidenceItems = computed(() => {
  const raw = Array.isArray(props.message?.normalizedEvidences) && props.message.normalizedEvidences.length
    ? props.message.normalizedEvidences
    : Array.isArray(props.message?.evidences)
      ? props.message.evidences
      : Array.isArray(workflowContract.value?.evidence)
        ? workflowContract.value.evidence
        : [];
  const typeLabelMap = {
    sql: 'SQL 数据', sql_result: 'SQL 数据', rag: '知识库', knowledge_base: '知识库',
    analysis: '分析结论', inspection: '巡检结果', report: '报告产物'
  };
  return raw.slice(0, 5).map((item, index) => {
    const kind = item?.kind || item?.type || item?.source_type || 'evidence';
    return {
      id: item?.id || item?.evidence_id || `phase4-evidence-${index}`,
      title: item?.title || item?.source || '证据项',
      typeLabel: typeLabelMap[kind] || kind,
      summary: item?.summary || item?.content || item?.source_locator || '暂无摘要'
    };
  });
});
const phase4TimelineItems = computed(() => {
  const raw = Array.isArray(props.message?.timeline)
    ? props.message.timeline
    : Array.isArray(workflowContract.value?.timeline)
      ? workflowContract.value.timeline
      : [];
  const typeLabelMap = { sql_result: '数据查询', knowledge_lookup: '知识检索', finding: '形成结论', report: '生成报告' };
  return raw.slice(0, 6).map((item, index) => ({
    id: item?.id || `${item?.type || 'timeline'}-${index}`,
    event: item?.event || item?.summary || '流程事件',
    typeLabel: typeLabelMap[item?.type] || item?.type || '事件',
    timeLabel: item?.time ? String(item.time).replace('T', ' ').slice(0, 19) : '时间未记录'
  }));
});
const phase4GovernanceItems = computed(() => {
  const raw = props.message?.governance || workflowContract.value?.governance || null;
  if (!raw || typeof raw !== 'object') return [];
  const items = [];
  if (raw.risk_level) items.push(`风险等级：${raw.risk_level}`);
  if (Array.isArray(raw.suggested_actions)) items.push(...raw.suggested_actions);
  if (Array.isArray(raw.next_steps)) items.push(...raw.next_steps);
  if (Array.isArray(raw.warnings)) items.push(...raw.warnings);
  return [...new Set(items.filter(Boolean))].slice(0, 6);
});
const phase4Artifacts = computed(() => {
  const raw = Array.isArray(props.message?.artifacts)
    ? props.message.artifacts
    : Array.isArray(workflowContract.value?.artifacts)
      ? workflowContract.value.artifacts
      : [];
  const typeLabelMap = { report: '报告', chart: '图表', table: '表格', evidence_review: '证据复核' };
  return raw.slice(0, 4).map((item, index) => ({
    id: item?.id || `phase4-artifact-${index}`,
    title: item?.title || item?.summary || '产物',
    typeLabel: typeLabelMap[item?.type] || item?.type || '产物',
    path: item?.path || item?.url || ''
  }));
});
const phase4ReportLinks = computed(() => {
  const urls = new Set();
  phase4Artifacts.value
    .filter((item) => item?.typeLabel === '报告' || /report|报告/i.test(`${item?.typeLabel || ''} ${item?.title || ''}`))
    .forEach((item) => {
      const rawPath = String(item?.path || '').trim();
      if (!rawPath) return;
      if (/^https?:\/\//i.test(rawPath) || rawPath.startsWith('/reports/')) {
        urls.add(rawPath);
        return;
      }
      const filename = normalizeReportFilename(rawPath.split(/[\\/]/).pop());
      const reportUrl = toReportUrl(filename);
      if (reportUrl) urls.add(reportUrl);
    });
  return [...urls];
});
const phase4MetricCards = computed(() => {
  const metrics = [];
  const evidenceCount = props.message?.evidenceCount ?? props.message?.evidence_count ?? phase4EvidenceItems.value.length;
  if (phase4Findings.value.length) metrics.push({ label: '关键结论', value: phase4Findings.value.length });
  if (Number(evidenceCount) > 0) metrics.push({ label: '证据数量', value: Number(evidenceCount) });
  if (phase4TimelineItems.value.length) metrics.push({ label: '时间线', value: phase4TimelineItems.value.length });
  if (phase4Artifacts.value.length) metrics.push({ label: '产物', value: phase4Artifacts.value.length });
  if (props.message?.releaseReady !== undefined && props.message?.releaseReady !== null) {
    metrics.push({ label: '发布状态', value: props.message.releaseReady ? '可发布' : '需复核' });
  }
  return metrics.slice(0, 5);
});
const hasWorkflowContractPanel = computed(() => (
  Boolean(workflowContract.value || scenarioContract.value) ||
  phase4Findings.value.length > 0 ||
  phase4EvidenceItems.value.length > 0 ||
  phase4TimelineItems.value.length > 0 ||
  phase4GovernanceItems.value.length > 0 ||
  phase4Artifacts.value.length > 0
));
const assistantDetailsButtonLabel = computed(() =>
  assistantDetailsExpanded.value ? '收起详情' : '查看详情'
);
const assistantDetailsHint = computed(() => {
  const sections = [];
  if (executionTimeline.value.length) sections.push('过程');
  if (evidenceFindings.value.length || normalizedEvidences.value.length) sections.push('依据');
  if (toolEvents.value.length) sections.push('工具');
  if (governanceSuggestions.value.items.length) sections.push('改进建议');
  return sections.join(' / ');
});
const formatConfidenceText = (value) => {
  if (value === 'high') return '把握高';
  if (value === 'medium') return '把握中';
  if (value === 'low') return '把握低';
  return '把握未知';
};
const formatSeverityText = (value) => {
  if (value === 'high') return '严重度高';
  if (value === 'medium') return '严重度中';
  if (value === 'low') return '严重度低';
  return '严重度未知';
};
const evidences = computed(() => Array.isArray(props.message?.evidences) ? props.message.evidences : []);
const EVIDENCE_KIND_LABELS = {
  sql: 'SQL数据',
  rag: '知识库',
  chart: '图表',
  report: '报告',
  action: '执行动作',
  unknown: '其他'
};
const EVIDENCE_FAMILY_LABELS = {
  telemetry: '现场数据',
  knowledge: '知识说明',
  artifact: '生成产物',
  action: '执行动作',
  generic: '其他'
};
const EVIDENCE_CHANNEL_LABELS = {
  database: '数据库',
  retrieval: '知识检索',
  visualization: '图表生成',
  reporting: '报告输出',
  execution: '动作执行',
  generic: '其他'
};
const EVIDENCE_STAGE_LABELS = {
  collect: '收集',
  retrieve: '检索',
  analyze: '分析',
  report: '报告'
};
const TOOL_DISPLAY_NAMES = {
  sql_db_query: '数据库查询',
  sql_db_schema: '表结构查看',
  sql_db_list_tables: '数据表查看',
  query_knowledge_base: '知识库检索',
  fig_inter: '图表生成',
  save_report: 'Markdown报告生成',
  get_time: '时间工具',
  write_todos: '任务规划'
};
const REPORT_GATE_LABELS = {
  pass: '通过',
  review_required: '需要复核',
  blocked: '已拦截'
};
const PUBLICATION_STATUS_LABELS = {
  published: '正式发布',
  draft: '草稿',
  blocked: '已拦截'
};
const getEvidenceKindLabel = (kind) => EVIDENCE_KIND_LABELS[kind] || kind || '其他';
const getEvidenceFamilyLabel = (family) => EVIDENCE_FAMILY_LABELS[family] || family || '其他';
const getEvidenceChannelLabel = (channel) => EVIDENCE_CHANNEL_LABELS[channel] || channel || '其他';
const getEvidenceStageLabel = (stage) => EVIDENCE_STAGE_LABELS[stage] || stage || '未标注';
const getToolDisplayName = (toolName) => TOOL_DISPLAY_NAMES[toolName] || toolName || '未标注';
const getReportGateLabel = (gate) => REPORT_GATE_LABELS[gate] || gate || '未标注';
const getPublicationStatusLabel = (status) => PUBLICATION_STATUS_LABELS[status] || status || '未标注';
const LEDGER_STATUS_LABELS = {
  open: '待处理',
  in_progress: '处理中',
  blocked: '已阻塞',
  verified: '已确认'
};
const getLedgerStatusLabel = (status) => LEDGER_STATUS_LABELS[status] || status || '未标注';
const normalizeRawEvidence = (item) => {
  const metadata = item?.metadata || {};
  const kind = item?.type || 'unknown';
  let family = 'generic';
  let channel = 'generic';
  if (kind === 'sql') {
    family = 'telemetry';
    channel = 'database';
  } else if (kind === 'rag') {
    family = 'knowledge';
    channel = 'retrieval';
  } else if (kind === 'chart') {
    family = 'artifact';
    channel = 'visualization';
  } else if (kind === 'report') {
    family = 'artifact';
    channel = 'reporting';
  } else if (kind === 'action') {
    family = 'action';
    channel = 'execution';
  }

  return {
    evidence_id: item?.evidence_id,
    kind,
    family,
    channel,
    stage: item?.stage,
    tool_name: metadata.tool_name || null,
    title: item?.title || '',
    summary: item?.summary || '',
    source_locator: metadata.web_path || metadata.artifact_path || item?.source || item?.raw_ref || '',
    source_details: {
      source: item?.source || '',
      raw_ref: item?.raw_ref || '',
      page: metadata.page,
      tables: Array.isArray(metadata.tables) ? metadata.tables : [],
      query: metadata.query || null,
      artifact_path: metadata.artifact_path || null,
      web_path: metadata.web_path || null
    },
    artifact: {
      artifact_path: metadata.artifact_path || null,
      web_path: metadata.web_path || null,
      figure_name: metadata.figure_name || null,
      chart_type: metadata.chart_type || null,
      publication_status: metadata.publication_status || null
    },
    governance: {
      publication_status: metadata.publication_status || null,
      report_gate: metadata.report_gate || null,
      release_ready: typeof metadata.release_ready === 'boolean' ? metadata.release_ready : null,
      action_guard: metadata.action_guard || null
    },
    payload: {
      query: metadata.query || null,
      tables: Array.isArray(metadata.tables) ? metadata.tables : [],
      row_count: metadata.row_count ?? null,
      total_rows: metadata.total_rows ?? null,
      truncated: metadata.truncated ?? null,
      figure_name: metadata.figure_name || null,
      chart_type: metadata.chart_type || null,
      dataframe_refs: Array.isArray(metadata.dataframe_refs) ? metadata.dataframe_refs : [],
      work_order_id: metadata.work_order_id || null,
      publication_status: metadata.publication_status || null,
      report_gate: metadata.report_gate || null,
      release_ready: typeof metadata.release_ready === 'boolean' ? metadata.release_ready : null
    }
  };
};
const getEvidenceTitle = (item) => {
  if (item?.title && String(item.title).trim()) {
    return String(item.title).trim();
  }
  if (item?.kind === 'sql') {
    return 'SQL 查询结果';
  }
  if (item?.kind === 'rag') {
    return '知识库命中结果';
  }
  if (item?.kind === 'chart') {
    return '图表证据';
  }
  if (item?.kind === 'report') {
    return '报告产物';
  }
  if (item?.kind === 'action') {
    return '执行动作结果';
  }
  return '依据记录';
};
const getEvidenceSourceLabel = (item) => {
  if (item?.kind === 'sql') {
    const tables = Array.isArray(item?.payload?.tables) ? item.payload.tables.filter(Boolean) : [];
    if (tables.length) {
      return `数据库表：${tables.join('、')}`;
    }
    return '数据库查询';
  }
  if (item?.kind === 'rag') {
    return item?.source_locator ? `知识来源：${item.source_locator}` : '知识库文档';
  }
  if (item?.kind === 'chart') {
    return item?.artifact?.figure_name ? `图表：${item.artifact.figure_name}` : '图表文件';
  }
  if (item?.kind === 'report') {
    return item?.source_locator ? `报告文件：${item.source_locator}` : '报告文件';
  }
  return item?.source_locator || '系统生成';
};
const getEvidenceReadableSummary = (item) => {
  if (!item || typeof item !== 'object') return '本次流程产出了一条依据。';
  const query = String(item?.payload?.query || '').trim();
  if (item.kind === 'sql') {
    if (/^show\s+tables/i.test(query)) {
      return '系统先查看了当前数据库里有哪些可用数据表。';
    }
    const describeMatch = query.match(/^describe\s+([a-zA-Z0-9_]+)/i);
    if (describeMatch) {
      return `系统查看了 ${describeMatch[1]} 表的字段结构，用来确认后续能查哪些数据。`;
    }
    const rowCount = item?.payload?.row_count;
    const totalRows = item?.payload?.total_rows;
    if (typeof totalRows === 'number' || typeof rowCount === 'number') {
      const count = typeof totalRows === 'number' ? totalRows : rowCount;
      return `系统执行了一次结构化数据查询，本次返回 ${count ?? 0} 条结果。`;
    }
    return '系统执行了一次数据库查询，用来收集现场运行数据或告警信息。';
  }
  if (item.kind === 'rag') {
    return '系统从知识库中检索到了相关手册、故障码或处理说明。';
  }
  if (item.kind === 'chart') {
    return '系统生成了一张图表，用来辅助说明趋势变化。';
  }
  if (item.kind === 'report') {
    return '系统生成了一份报告文件，可继续查看或导出。';
  }
  if (item.kind === 'action') {
    return '系统执行了一次动作类工具，并记录了执行结果。';
  }
  return item?.summary || '本次流程产出了一条依据。';
};
const getEvidenceRawPreview = (item) => {
  const raw = typeof item?.summary === 'string' ? item.summary.trim() : '';
  if (!raw) return '';
  if (raw === getEvidenceReadableSummary(item)) return '';
  if (raw.length <= 220) return raw;
  return `${raw.slice(0, 220)}...`;
};
const normalizedEvidences = computed(() => {
  const backendRecords = props.message?.normalizedEvidences || props.message?.normalized_evidences || null;
  if (Array.isArray(backendRecords) && backendRecords.length) {
    return backendRecords;
  }
  return evidences.value.map((item) => normalizeRawEvidence(item));
});
const evidenceSourceCards = computed(() => normalizedEvidences.value.slice(0, 3).map((item, index) => ({
  id: item?.evidence_id || item?.source_locator || item?.title || `evidence-${index}`,
  title: getEvidenceTitle(item),
  meta: [
    getEvidenceKindLabel(item?.kind),
    getEvidenceSourceLabel(item),
    item?.metadata?.file_name || item?.file_name || item?.metadata?.source_file || '',
  ].filter(Boolean).join(' · '),
  summary: getEvidenceReadableSummary(item),
})));
const evidenceCatalogFamilies = computed(() => [...new Set(normalizedEvidences.value.map((item) => item?.family).filter(Boolean))]);
const evidenceCatalogChannels = computed(() => [...new Set(normalizedEvidences.value.map((item) => item?.channel).filter(Boolean))]);
const evidenceCatalogStages = computed(() => [...new Set(normalizedEvidences.value.map((item) => item?.stage).filter(Boolean))]);
const filteredNormalizedEvidences = computed(() =>
  normalizedEvidences.value.filter((item) => {
    if (activeLifecycleRunId.value) {
      const activeEntry = toolLifecycleEntries.value.find((entry) => entry.runId === activeLifecycleRunId.value);
      if (activeEntry && !activeEntry.evidenceIds.includes(item?.evidence_id)) {
        return false;
      }
    }
    if (evidenceCatalogFilter.value.family !== 'all' && item?.family !== evidenceCatalogFilter.value.family) {
      return false;
    }
    if (evidenceCatalogFilter.value.channel !== 'all' && item?.channel !== evidenceCatalogFilter.value.channel) {
      return false;
    }
    if (evidenceCatalogFilter.value.stage !== 'all' && item?.stage !== evidenceCatalogFilter.value.stage) {
      return false;
    }
    return true;
  })
);
const evidenceFindings = computed(() => Array.isArray(props.message?.findings) ? props.message.findings : []);
const findingLinks = computed(() => Array.isArray(props.message?.findingLinks) ? props.message.findingLinks : []);
const chartEvidenceRecords = computed(() => normalizedEvidences.value.filter((item) => item?.kind === 'chart'));
const evidenceCoverageScorecard = computed(() => {
  const backendCoverage = props.message?.evidenceCoverage || props.message?.evidence_coverage || null;
  if (backendCoverage && typeof backendCoverage === 'object') {
    return {
      grade: backendCoverage.grade || 'D',
      score: Number(backendCoverage.score || 0),
      totalEvidences: Number(backendCoverage.total_evidences || 0),
      sqlCount: Number(backendCoverage.sql_count || 0),
      ragCount: Number(backendCoverage.rag_count || 0),
      chartCount: Number(backendCoverage.chart_count || 0),
      linkedChartCount: Number(backendCoverage.linked_chart_count || 0),
      orphanChartCount: Number(backendCoverage.orphan_chart_count || 0),
      totalFindings: Number(backendCoverage.total_findings || 0),
      linkedFindings: Number(backendCoverage.linked_findings || 0),
      findingBindingRate: Number(backendCoverage.finding_binding_rate || 0),
      chartCoverageRate: Number(backendCoverage.chart_coverage_rate || 0),
      metrics: Array.isArray(backendCoverage.metrics) ? backendCoverage.metrics : []
    };
  }

  const totalEvidences = normalizedEvidences.value.length;
  const sqlCount = normalizedEvidences.value.filter((item) => item?.kind === 'sql').length;
  const ragCount = normalizedEvidences.value.filter((item) => item?.kind === 'rag').length;
  const chartCount = chartEvidenceRecords.value.length;
  const totalFindings = evidenceFindings.value.length;
  const linkedFindings = evidenceFindings.value.filter((finding) => getFindingEvidence(finding?.finding_id).length).length;
  const linkedChartCount = new Set(
    findingLinks.value.flatMap((item) => Array.isArray(item?.chart_evidence_ids) ? item.chart_evidence_ids : [])
  ).size;
  const orphanChartCount = Math.max(chartCount - linkedChartCount, 0);
  const findingBindingRate = totalFindings ? Math.round((linkedFindings / totalFindings) * 100) : 0;
  const chartCoverageRate = chartCount ? Math.round((linkedChartCount / chartCount) * 100) : 0;

  const score =
    (sqlCount > 0 ? 25 : 0) +
    (ragCount > 0 ? 25 : 0) +
    Math.min(findingBindingRate, 100) * 0.3 +
    Math.min(chartCoverageRate, 100) * 0.2;

  let grade = 'C';
  if (score >= 85) grade = 'A';
  else if (score >= 70) grade = 'B';
  else if (score >= 50) grade = 'C';
  else grade = 'D';

  return {
    grade,
    score: Math.round(score),
    totalEvidences,
    sqlCount,
    ragCount,
    chartCount,
    linkedChartCount,
    orphanChartCount,
    totalFindings,
    linkedFindings,
    findingBindingRate,
    chartCoverageRate,
    metrics: [
      { label: 'SQL coverage', value: sqlCount > 0 ? 'Yes' : 'No' },
      { label: 'RAG coverage', value: ragCount > 0 ? 'Yes' : 'No' },
      { label: 'Chart coverage', value: `${linkedChartCount}/${chartCount}` },
      { label: 'Finding binding', value: `${linkedFindings}/${totalFindings}` },
      { label: 'Orphan charts', value: String(orphanChartCount) },
      { label: 'Evidence count', value: String(totalEvidences) }
    ]
  };
});
const evidenceById = computed(() => {
  const mapping = new Map();
  normalizedEvidences.value.forEach((item) => {
    if (item?.evidence_id) {
      mapping.set(item.evidence_id, item);
    }
  });
  return mapping;
});
const getFindingEvidence = (findingId) => {
  if (!findingId) return [];
  const matched = findingLinks.value.find(item => item?.finding_id === findingId);
  return Array.isArray(matched?.evidence_ids) ? matched.evidence_ids : [];
};
const getFindingChartEvidence = (findingId) => {
  if (!findingId) return [];
  const matched = findingLinks.value.find(item => item?.finding_id === findingId);
  return Array.isArray(matched?.chart_evidence_ids) ? matched.chart_evidence_ids : [];
};
const getFindingLink = (findingId) => {
  if (!findingId) return null;
  return findingLinks.value.find(item => item?.finding_id === findingId) || null;
};
const setActiveFinding = (findingId) => {
  activeFindingId.value = activeFindingId.value === findingId ? null : findingId;
};
const resetEvidenceCatalogFilters = () => {
  evidenceCatalogFilter.value = {
    family: 'all',
    channel: 'all',
    stage: 'all'
  };
};
const clearWorkbenchFocus = () => {
  activeFindingId.value = null;
  activeGovernanceLayer.value = null;
  activeLifecycleRunId.value = null;
  resetEvidenceCatalogFilters();
};
const isEvidenceHighlighted = (evidenceId) => {
  if (!evidenceId) return false;
  if (activeLifecycleRunId.value) {
    const activeEntry = toolLifecycleEntries.value.find((entry) => entry.runId === activeLifecycleRunId.value);
    if (activeEntry?.evidenceIds?.includes(evidenceId)) {
      return true;
    }
  }
  if (!activeFindingId.value) return false;
  return getFindingEvidenceCards(activeFindingId.value).some((item) => item?.evidence_id === evidenceId);
};
const isEvidenceGovernanceHighlighted = (item) => {
  if (!activeGovernanceLayer.value || !item) return false;
  if (activeGovernanceLayer.value === 'rag') {
    return item.family === 'knowledge';
  }
  if (activeGovernanceLayer.value === 'prompt') {
    return item.stage === 'analyze';
  }
  if (activeGovernanceLayer.value === 'tool') {
    return ['visualization', 'reporting', 'execution', 'database'].includes(item.channel);
  }
  if (activeGovernanceLayer.value === 'workflow') {
    return ['collect', 'retrieve', 'analyze', 'report'].includes(item.stage);
  }
  return false;
};
const setEvidenceCatalogFilter = (key, value) => {
  evidenceCatalogFilter.value = {
    ...evidenceCatalogFilter.value,
    [key]: value
  };
};
const applyGovernanceLayerFilters = (layer) => {
  resetEvidenceCatalogFilters();
  if (layer === 'rag') {
    setEvidenceCatalogFilter('family', 'knowledge');
  } else if (layer === 'prompt') {
    setEvidenceCatalogFilter('stage', 'analyze');
  } else if (layer === 'tool') {
    setEvidenceCatalogFilter('channel', 'visualization');
  } else if (layer === 'workflow') {
    setEvidenceCatalogFilter('stage', 'report');
  }
};
const focusReplayItem = (item) => {
  if (!item || typeof item !== 'object') return;
  activeGovernanceLayer.value = null;
  activeLifecycleRunId.value = null;
  setActiveFinding(item.findingId || null);
  resetEvidenceCatalogFilters();
  if (item.stage) {
    setEvidenceCatalogFilter('stage', item.stage);
  }
};
const focusGovernanceLayer = (layer) => {
  const nextLayer = activeGovernanceLayer.value === layer ? null : layer;
  activeGovernanceLayer.value = nextLayer;
  activeLifecycleRunId.value = null;
  if (!nextLayer) {
    resetEvidenceCatalogFilters();
    return;
  }
  applyGovernanceLayerFilters(nextLayer);
};
const getFindingEvidenceCards = (findingId) => {
  const evidenceIds = getFindingEvidence(findingId);
  const chartEvidenceIds = getFindingChartEvidence(findingId);
  const mergedIds = [...new Set([...evidenceIds, ...chartEvidenceIds])];
  return mergedIds
    .map((evidenceId) => evidenceById.value.get(evidenceId))
    .filter(Boolean);
};
const focusToolLifecycle = (item) => {
  const nextRunId = activeLifecycleRunId.value === item?.runId ? null : item?.runId;
  activeLifecycleRunId.value = nextRunId;
  activeGovernanceLayer.value = null;
  activeFindingId.value = nextRunId && item?.findingIds?.length === 1 ? item.findingIds[0] : null;
  resetEvidenceCatalogFilters();
  if (nextRunId && item?.stage) {
    setEvidenceCatalogFilter('stage', item.stage);
  }
};
const focusTimelineItem = (item) => {
  if (!item || typeof item !== 'object') return;
  if (item.kind === 'tool') {
    focusToolLifecycle(item.lifecycleRef || item);
    return;
  }
  if (item.kind === 'finding') {
    focusReplayItem({
      findingId: item.findingId,
      stage: item.stage || null
    });
    return;
  }
  if (item.kind === 'evidence') {
    activeLifecycleRunId.value = null;
    activeGovernanceLayer.value = null;
    activeFindingId.value = item.findingId || null;
    resetEvidenceCatalogFilters();
    if (item.stage) {
      setEvidenceCatalogFilter('stage', item.stage);
    }
    return;
  }
  if (item.kind === 'stage') {
    activeLifecycleRunId.value = null;
    activeGovernanceLayer.value = null;
    activeFindingId.value = null;
    resetEvidenceCatalogFilters();
    if (item.stage) {
      setEvidenceCatalogFilter('stage', item.stage);
    }
  }
};
const focusTimelineNodeById = (nodeId) => {
  if (!nodeId) return;
  const node = executionTimeline.value.find((item) => item.id === nodeId);
  if (!node) return;
  focusTimelineItem(node);
  toggleTimelineExpansion(node.id);
};
const toggleTimelineExpansion = (itemId) => {
  if (!itemId) return;
  expandedTimelineItemIds.value = expandedTimelineItemIds.value.includes(itemId)
    ? expandedTimelineItemIds.value.filter((id) => id !== itemId)
    : [...expandedTimelineItemIds.value, itemId];
};
const toggleTimelineItem = (item) => {
  if (!item || typeof item !== 'object') return;
  focusTimelineItem(item);
  toggleTimelineExpansion(item.id);
};
const toggleBadCaseTimelineMode = () => {
  badCaseTimelineOnly.value = !badCaseTimelineOnly.value;
};
const getEvidenceDetailLines = (item) => {
  if (!item || typeof item !== 'object') return [];
  const details = [];
  const payload = item.payload || {};
  const sourceDetails = item.source_details || {};
  const governance = item.governance || {};
  const artifact = item.artifact || {};

  if (sourceDetails.page) {
    details.push(`文档页码：${sourceDetails.page}`);
  }
  if (Array.isArray(payload.tables) && payload.tables.length) {
    details.push(`关联数据表：${payload.tables.join('、')}`);
  }
  if (payload.query) {
    details.push(`执行语句：${String(payload.query).slice(0, 160)}`);
  }
  if (typeof payload.row_count === 'number') {
    details.push(`本次返回行数：${payload.row_count}`);
  }
  if (typeof payload.total_rows === 'number') {
    details.push(`结果总行数：${payload.total_rows}`);
  }
  if (typeof payload.truncated === 'boolean') {
    details.push(`是否截断：${payload.truncated ? '是' : '否'}`);
  }
  if (artifact.figure_name) {
    details.push(`图表名称：${artifact.figure_name}`);
  }
  if (artifact.chart_type) {
    details.push(`图表类型：${artifact.chart_type}`);
  }
  if (Array.isArray(payload.dataframe_refs) && payload.dataframe_refs.length) {
    const dataframeNames = payload.dataframe_refs
      .map((ref) => ref?.name)
      .filter(Boolean)
      .slice(0, 3);
    if (dataframeNames.length) {
      details.push(`关联数据集：${dataframeNames.join('、')}`);
    }
  }
  if (payload.work_order_id) {
    details.push(`工单编号：${payload.work_order_id}`);
  }
  if (governance.action_guard?.final_filename) {
    details.push(`最终文件名：${governance.action_guard.final_filename}`);
  }
  if (governance.action_guard?.status_text) {
    details.push(`状态说明：${governance.action_guard.status_text}`);
  }
  return details;
};
const diagnosticTimeline = computed(() => {
  const stageOrder = ['collect', 'retrieve', 'analyze', 'report'];
  const stageLabelMap = {
    collect: '1. 收集',
    retrieve: '2. 检索',
    analyze: '3. 分析',
    report: '4. 报告'
  };
  const statusByStage = new Map(
    workflowStageDetails.value.map((detail) => [detail.stage, detail.statusLabel])
  );

  return stageOrder
    .filter((stage) => workflowStages.value.some((item) => item.key === stage))
    .map((stage) => {
      const stageEvidences = normalizedEvidences.value.filter((item) => item?.stage === stage);
      const stageEvidenceIds = stageEvidences
        .map((item) => item?.evidence_id)
        .filter(Boolean);
      const stageFindings = evidenceFindings.value.filter((finding) => {
        const linkedIds = getFindingEvidence(finding?.finding_id);
        return linkedIds.some((evidenceId) => stageEvidenceIds.includes(evidenceId));
      });
      const summary =
        stageFindings[0]?.text ||
        stageEvidences[0]?.title ||
        stageEvidences[0]?.summary ||
        '';

      return {
        stage,
        label: stageLabelMap[stage] || stage,
        statusLabel: statusByStage.get(stage) || (stage === currentWorkflowStage.value ? '进行中' : '已完成'),
        evidenceCount: stageEvidences.length,
        findingCount: stageFindings.length,
        evidenceIds: stageEvidenceIds.slice(0, 4),
        summary
      };
    });
});
const executionTimeline = computed(() => {
  const stageLabelMap = {
    collect: '1. 收集',
    retrieve: '2. 检索',
    analyze: '3. 分析',
    report: '4. 报告'
  };
  const stageStatusMap = new Map(
    workflowStageDetails.value.map((detail) => [detail.stage, detail.statusLabel])
  );

  const items = [];

  workflowStages.value.forEach((stageItem) => {
    const stage = stageItem.key;
    const stageEvidenceIds = normalizedEvidences.value
      .filter((item) => item?.stage === stage)
      .map((item) => item?.evidence_id)
      .filter(Boolean);
    const linkedFindingIds = findingLinks.value
      .filter((link) => {
        const allIds = [
          ...(Array.isArray(link?.evidence_ids) ? link.evidence_ids : []),
          ...(Array.isArray(link?.chart_evidence_ids) ? link.chart_evidence_ids : [])
        ];
        return allIds.some((evidenceId) => stageEvidenceIds.includes(evidenceId));
      })
      .map((link) => link?.finding_id)
      .filter(Boolean);

    items.push({
      kind: 'stage',
      id: `stage-${stage}`,
      stage,
      kindLabel: '阶段',
      title: stageLabelMap[stage] || stage,
      timeLabel: '',
      summary: stageStatusMap.get(stage) || '',
      meta: [
        `证据 ${stageEvidenceIds.length}`,
        `判断 ${linkedFindingIds.length}`
      ],
      detailLines: [
        `阶段 ${stage}`,
        `状态 ${stageStatusMap.get(stage) || stageItem.status || '未知'}`,
        `证据ID ${stageEvidenceIds.slice(0, 8).join(', ') || '无'}`,
        `判断ID ${linkedFindingIds.slice(0, 8).join(', ') || '无'}`
      ],
      linkBadges: [],
      isActive: evidenceCatalogFilter.value.stage === stage,
      isExpanded: expandedTimelineItemIds.value.includes(`stage-${stage}`),
      isGovernanceRelevant: false
    });
  });

  toolLifecycleEntries.value.forEach((item) => {
    items.push({
      kind: 'tool',
      id: item.runId || `${item.tool}-${item.event}-${item.timeLabel}`,
      stage: item.stage,
      kindLabel: item.event === 'start' ? '工具开始' : '工具结束',
      title: item.tool,
      timeLabel: item.timeLabel,
      summary: item.preview || '',
      meta: [
        item.stageLabel,
        item.durationLabel || '耗时暂无'
      ].filter(Boolean),
      detailLines: [
        item.runId ? `运行ID ${item.runId}` : '运行ID 暂无',
        `阶段 ${item.stage || '暂无'}`,
        `事件 ${item.event || '暂无'}`,
        `证据ID ${item.evidenceIds.join(', ') || '无'}`,
        `判断ID ${item.findingIds.join(', ') || '无'}`
      ],
      linkBadges: [
        item.evidenceCount ? `证据 ${item.evidenceCount}` : '',
        item.findingCount ? `判断 ${item.findingCount}` : ''
      ].filter(Boolean),
      isActive: activeLifecycleRunId.value === item.runId,
      isExpanded: expandedTimelineItemIds.value.includes(item.runId || `${item.tool}-${item.event}-${item.timeLabel}`),
      lifecycleRef: item,
      isGovernanceRelevant: false
    });
  });

  normalizedEvidences.value.slice(0, 12).forEach((item) => {
    const linkedFindingId = findingLinks.value.find((link) => {
      const ids = [
        ...(Array.isArray(link?.evidence_ids) ? link.evidence_ids : []),
        ...(Array.isArray(link?.chart_evidence_ids) ? link.chart_evidence_ids : [])
      ];
      return item?.evidence_id && ids.includes(item.evidence_id);
    })?.finding_id || null;

    items.push({
      kind: 'evidence',
      id: item.evidence_id || `${item.kind}-${item.title}`,
      stage: item.stage,
      findingId: linkedFindingId,
      kindLabel: '证据',
      title: item.title || item.kind,
      timeLabel: '',
      summary: item.summary || '',
      meta: [
        item.family || 'generic',
        item.channel || 'generic',
        item.stage || 'n/a'
      ],
      detailLines: getEvidenceDetailLines(item),
      linkBadges: [
        item.tool_name ? `工具 ${item.tool_name}` : '',
        linkedFindingId ? `判断 ${linkedFindingId}` : ''
      ].filter(Boolean),
      isActive: isEvidenceHighlighted(item.evidence_id),
      isExpanded: expandedTimelineItemIds.value.includes(item.evidence_id || `${item.kind}-${item.title}`),
      isGovernanceRelevant: false
    });
  });

  evidenceFindings.value.forEach((finding) => {
    const link = getFindingLink(finding?.finding_id);
    const evidenceIds = Array.isArray(link?.evidence_ids) ? link.evidence_ids : [];
    const chartIds = Array.isArray(link?.chart_evidence_ids) ? link.chart_evidence_ids : [];
    const stage = evidenceIds
      .map((evidenceId) => evidenceById.value.get(evidenceId)?.stage)
      .find(Boolean) || 'analyze';

    items.push({
      kind: 'finding',
      id: finding?.finding_id || finding?.text,
      findingId: finding?.finding_id || null,
      stage,
      kindLabel: '判断点',
      title: finding?.text || '判断点',
      timeLabel: '',
      summary: '',
      meta: [
        finding?.confidence ? formatConfidenceText(finding.confidence) : '',
        finding?.severity ? formatSeverityText(finding.severity) : ''
      ].filter(Boolean),
      detailLines: [
        `判断ID ${finding?.finding_id || '暂无'}`,
        `阶段 ${stage}`,
        `证据ID ${evidenceIds.join(', ') || '无'}`,
        `图表证据ID ${chartIds.join(', ') || '无'}`,
        Array.isArray(link?.matched_keywords) && link?.matched_keywords.length
          ? `关键词 ${link.matched_keywords.join(', ')}`
          : '关键词 无'
      ],
      linkBadges: [
        evidenceIds.length ? `证据 ${evidenceIds.length}` : '',
        chartIds.length ? `图表 ${chartIds.length}` : ''
      ].filter(Boolean),
      isActive: activeFindingId.value === (finding?.finding_id || null),
      isExpanded: expandedTimelineItemIds.value.includes(finding?.finding_id || finding?.text),
      isGovernanceRelevant: false
    });
  });

  return items;
});
const getRiskLevelFromScore = (score) => {
  if (score >= 70) return 'high';
  if (score >= 40) return 'medium';
  if (score > 0) return 'low';
  return 'none';
};
const getRiskLabelFromLevel = (level, score = 0) => {
  if (level === 'high') return `高风险 ${score}`;
  if (level === 'medium') return `中风险 ${score}`;
  if (level === 'low') return `低风险 ${score}`;
  return '稳定';
};
const findingRiskProfiles = computed(() => {
  const profiles = new Map();
  badCaseReplay.value.items.forEach((item) => {
    const reasons = [];
    let score = 0;
    if (!item.evidenceIds.length) {
      score += 42;
      reasons.push('no bound evidence');
    }
    if (item.matchScore <= 1) {
      score += 18;
      reasons.push(`weak match score ${item.matchScore}`);
    }
    if (item.confidence === 'low') {
      score += 20;
      reasons.push('low confidence finding');
    }
    if (chartEvidenceRecords.value.length > 0 && !item.chartEvidenceIds.length) {
      score += 10;
      reasons.push('chart evidence not bound');
    }
    if (!item.evidenceTypes.includes('sql') && !item.evidenceTypes.includes('rag')) {
      score += 18;
      reasons.push('missing SQL/RAG primary evidence');
    }
    const normalizedScore = Math.min(score, 100);
    profiles.set(item.findingId, {
      score: normalizedScore,
      level: getRiskLevelFromScore(normalizedScore),
      label: getRiskLabelFromLevel(getRiskLevelFromScore(normalizedScore), normalizedScore),
      reasons
    });
  });
  return profiles;
});
const evidenceRiskProfiles = computed(() => {
  const profiles = new Map();
  badCaseReplay.value.items.forEach((item) => {
    const findingRisk = findingRiskProfiles.value.get(item.findingId);
    const propagatedScore = Math.max((findingRisk?.score || 0) - 12, 0);
    const propagatedLevel = getRiskLevelFromScore(propagatedScore);
    [...item.evidenceIds, ...item.chartEvidenceIds].forEach((evidenceId) => {
      const existing = profiles.get(evidenceId);
      if (!existing || propagatedScore > existing.score) {
        profiles.set(evidenceId, {
          score: propagatedScore,
          level: propagatedLevel,
          label: getRiskLabelFromLevel(propagatedLevel, propagatedScore),
          reasons: findingRisk?.reasons?.length
            ? [`linked to finding ${item.findingId}`, ...findingRisk.reasons]
            : [`linked to finding ${item.findingId}`]
        });
      }
    });
  });
  badCaseReplay.value.orphanChartEvidence.forEach((item) => {
    if (!item?.evidence_id) return;
    profiles.set(item.evidence_id, {
      score: 68,
      level: 'medium',
      label: getRiskLabelFromLevel('medium', 68),
      reasons: ['chart evidence generated but not cited by any finding']
    });
  });
  return profiles;
});
const stageRiskProfiles = computed(() => {
  const profiles = new Map();
  workflowStages.value.forEach((stageItem) => {
    const stage = stageItem.key;
    const relatedFindings = badCaseReplay.value.items.filter((item) => item.stage === stage);
    const scores = relatedFindings
      .map((item) => findingRiskProfiles.value.get(item.findingId)?.score || 0)
      .filter((score) => score > 0);
    const reasons = [];
    if (scores.length) {
      reasons.push(`${scores.length} risky finding(s) in this stage`);
    }
    const detail = workflowStageDetails.value.find((item) => item.stage === stage);
    const durationText = String(detail?.durationLabel || '');
    const durationValue = Number.parseFloat(durationText.replace(' ms', ''));
    if (!Number.isNaN(durationValue) && durationValue >= 1500) {
      reasons.push(`slow stage duration ${detail.durationLabel}`);
      scores.push(45);
    }
    const score = scores.length ? Math.min(Math.max(...scores), 100) : 0;
    const level = getRiskLevelFromScore(score);
    profiles.set(stage, {
      score,
      level,
      label: getRiskLabelFromLevel(level, score),
      reasons
    });
  });
  return profiles;
});
const toolRiskProfiles = computed(() => {
  const profiles = new Map();
  toolLifecycleEntries.value.forEach((item) => {
    const evidenceScores = item.evidenceIds
      .map((evidenceId) => evidenceRiskProfiles.value.get(evidenceId)?.score || 0)
      .filter((score) => score > 0);
    const findingScores = item.findingIds
      .map((findingId) => findingRiskProfiles.value.get(findingId)?.score || 0)
      .filter((score) => score > 0);
    const reasons = [];
    if (findingScores.length) {
      reasons.push(`touches ${findingScores.length} risky finding(s)`);
    }
    if (evidenceScores.length) {
      reasons.push(`touches ${evidenceScores.length} risky evidence item(s)`);
    }
    if (item.event === 'tool_end' && !item.evidenceCount && !item.findingCount) {
      reasons.push('tool completed without evidence or finding binding');
      evidenceScores.push(35);
    }
    const score = [...findingScores, ...evidenceScores].length
      ? Math.min(Math.max(...findingScores, ...evidenceScores), 100)
      : 0;
    const level = getRiskLevelFromScore(score);
    profiles.set(item.runId || `${item.tool}-${item.event}-${item.timeLabel}`, {
      score,
      level,
      label: getRiskLabelFromLevel(level, score),
      reasons
    });
  });
  return profiles;
});
const getTimelineRiskProfile = (item) => {
  if (!item || typeof item !== 'object') {
    return {
      riskScore: 0,
      riskLevel: 'none',
      riskLabel: 'Stable',
      riskReasons: []
    };
  }
  if (item.kind === 'finding') {
    const profile = findingRiskProfiles.value.get(item.findingId || item.id);
    return profile
      ? {
        riskScore: profile.score,
        riskLevel: profile.level,
        riskLabel: profile.label,
        riskReasons: profile.reasons
      }
      : { riskScore: 0, riskLevel: 'none', riskLabel: 'Stable', riskReasons: [] };
  }
  if (item.kind === 'evidence') {
    const profile = evidenceRiskProfiles.value.get(item.id);
    return profile
      ? {
        riskScore: profile.score,
        riskLevel: profile.level,
        riskLabel: profile.label,
        riskReasons: profile.reasons
      }
      : { riskScore: 0, riskLevel: 'none', riskLabel: 'Stable', riskReasons: [] };
  }
  if (item.kind === 'tool') {
    const profile = toolRiskProfiles.value.get(item.id);
    return profile
      ? {
        riskScore: profile.score,
        riskLevel: profile.level,
        riskLabel: profile.label,
        riskReasons: profile.reasons
      }
      : { riskScore: 0, riskLevel: 'none', riskLabel: 'Stable', riskReasons: [] };
  }
  if (item.kind === 'stage') {
    const profile = stageRiskProfiles.value.get(item.stage);
    return profile
      ? {
        riskScore: profile.score,
        riskLevel: profile.level,
        riskLabel: profile.label,
        riskReasons: profile.reasons
      }
      : { riskScore: 0, riskLevel: 'none', riskLabel: 'Stable', riskReasons: [] };
  }
  return { riskScore: 0, riskLevel: 'none', riskLabel: 'Stable', riskReasons: [] };
};
const filteredExecutionTimeline = computed(() => {
  const badCaseFindingIds = new Set(badCaseReplay.value.items.map((item) => item.findingId).filter(Boolean));
  return executionTimeline.value
    .map((item) => {
      let isGovernanceRelevant = false;
      if (activeGovernanceLayer.value === 'rag') {
        isGovernanceRelevant = item.kind === 'evidence'
          ? item.meta.includes('knowledge')
          : item.kind === 'finding'
            ? badCaseFindingIds.has(item.findingId)
            : item.stage === 'retrieve';
      } else if (activeGovernanceLayer.value === 'prompt') {
        isGovernanceRelevant = item.stage === 'analyze' || item.kind === 'finding';
      } else if (activeGovernanceLayer.value === 'tool') {
        isGovernanceRelevant = item.kind === 'tool' || item.kind === 'evidence';
      } else if (activeGovernanceLayer.value === 'workflow') {
        isGovernanceRelevant = item.kind === 'stage' || item.stage === 'report';
      }

      const isBadCaseRelevant = item.kind === 'finding'
        ? badCaseFindingIds.has(item.findingId)
        : item.linkBadges.some((badge) => badge.startsWith('finding '))
          || (item.detailLines || []).some((detail) => detail.includes('finding_ids') && !detail.endsWith('none'));
      const riskProfile = getTimelineRiskProfile(item);
      const meta = item.meta.slice();
      if (riskProfile.riskLevel !== 'none') {
        meta.push(`risk ${riskProfile.riskScore}`);
      }
      const detailLines = item.detailLines.slice();
      riskProfile.riskReasons.forEach((reason) => {
        detailLines.push(`risk_reason ${reason}`);
      });

      return {
        ...item,
        meta,
        detailLines,
        isGovernanceRelevant,
        isBadCaseRelevant,
        riskScore: riskProfile.riskScore,
        riskLevel: riskProfile.riskLevel,
        riskLabel: riskProfile.riskLabel,
        riskReasons: riskProfile.riskReasons
      };
    })
    .filter((item) => {
      if (badCaseTimelineOnly.value && !item.isBadCaseRelevant && !item.isGovernanceRelevant) {
        return false;
      }
      if (activeGovernanceLayer.value && !item.isGovernanceRelevant && !item.isActive) {
        return false;
      }
      return true;
    });
});
const areAllTimelineItemsExpanded = computed(() =>
  filteredExecutionTimeline.value.length > 0 &&
  filteredExecutionTimeline.value.every((item) => expandedTimelineItemIds.value.includes(item.id))
);
const toggleAllTimelineItems = () => {
  expandedTimelineItemIds.value = areAllTimelineItemsExpanded.value
    ? []
    : filteredExecutionTimeline.value.map((item) => item.id).filter(Boolean);
};
const executionTimelineMarkdown = computed(() => {
  const lines = ['# Execution Timeline'];
  filteredExecutionTimeline.value.forEach((item, index) => {
    lines.push(`${index + 1}. [${item.kindLabel}] ${item.title}`);
    if (item.summary) {
      lines.push(`   summary: ${item.summary}`);
    }
    if (item.timeLabel) {
      lines.push(`   time: ${item.timeLabel}`);
    }
    if (item.meta.length) {
      lines.push(`   meta: ${item.meta.join(' | ')}`);
    }
    if (item.linkBadges.length) {
      lines.push(`   links: ${item.linkBadges.join(' | ')}`);
    }
    if (item.detailLines.length) {
      item.detailLines.forEach((detail) => {
        lines.push(`   - ${detail}`);
      });
    }
  });
  return lines.join('\n').trim();
});
const badCaseReplay = computed(() => {
  const stageLabelMap = {
    collect: '收集',
    retrieve: '检索',
    analyze: '分析',
    report: '报告'
  };
  const confidenceLabelMap = {
    high: '高置信',
    medium: '中置信',
    low: '低置信'
  };
  const severityLabelMap = {
    high: '高严重度',
    medium: '中严重度',
    low: '低严重度'
  };

  const items = evidenceFindings.value.map((finding) => {
    const link = getFindingLink(finding?.finding_id);
    const evidenceIds = Array.isArray(link?.evidence_ids) ? link.evidence_ids : [];
    const chartEvidenceIds = Array.isArray(link?.chart_evidence_ids) ? link.chart_evidence_ids : [];
    const relatedStages = evidenceIds
      .map((evidenceId) => evidenceById.value.get(evidenceId)?.stage)
      .filter(Boolean);
    const stage = relatedStages[0] || 'analyze';
    const evidenceTypes = evidenceIds
      .map((evidenceId) => evidenceById.value.get(evidenceId)?.type)
      .filter(Boolean);
    return {
      findingId: finding?.finding_id || finding?.text,
      text: finding?.text || '',
      stage,
      stageLabel: stageLabelMap[stage] || stage,
      confidence: finding?.confidence || 'unknown',
      confidenceLabel: confidenceLabelMap[finding?.confidence] || '置信度未知',
      severity: finding?.severity || 'unknown',
      severityLabel: severityLabelMap[finding?.severity] || '严重度未知',
      matchScore: link?.match_score ?? 0,
      keywords: Array.isArray(link?.matched_keywords) ? link.matched_keywords : [],
      evidenceIds,
      chartEvidenceIds,
      evidenceTypes
    };
  });

  const risks = [];
  const linkedChartEvidenceIds = new Set(
    items.flatMap((item) => Array.isArray(item.chartEvidenceIds) ? item.chartEvidenceIds : [])
  );
  const orphanChartEvidence = chartEvidenceRecords.value.filter(
    (item) => item?.evidence_id && !linkedChartEvidenceIds.has(item.evidence_id)
  );
  items.forEach((item) => {
    if (!item.evidenceIds.length) {
      risks.push({
        type: 'no_evidence',
        text: `结论“${item.text}”缺少直接证据挂接`
      });
    } else if (item.matchScore <= 1) {
      risks.push({
        type: 'weak_match',
        text: `结论“${item.text}”证据匹配较弱，当前 score=${item.matchScore}`
      });
    }

    if (item.confidence === 'low') {
      risks.push({
        type: 'low_confidence',
        text: `结论“${item.text}”当前仍是低置信度`
      });
    }
  });

  items.forEach((item) => {
    if (chartEvidenceRecords.value.length > 0 && !item.chartEvidenceIds.length) {
      risks.push({
        type: 'missing_chart_binding',
        text: `Finding "${item.text}" has not linked any chart evidence yet.`
      });
    }

    if (!item.evidenceTypes.includes('sql') && !item.evidenceTypes.includes('rag')) {
      risks.push({
        type: 'missing_primary_evidence',
        text: `Finding "${item.text}" is missing primary SQL/RAG evidence and may rely on weak secondary signals.`
      });
    }
  });

  orphanChartEvidence.forEach((item) => {
    risks.push({
      type: 'orphan_chart_evidence',
      text: `Chart evidence "${item.title || item.evidence_id}" was generated but not referenced by any finding.`
    });
  });

  return {
    items,
    risks,
    orphanChartEvidence
  };
});
const badCaseGovernance = computed(() => {
  const layerLabelMap = {
    rag: '知识检索层',
    prompt: '提示词与推理层',
    tool: '工具执行层',
    workflow: '工作流编排层'
  };

  const buckets = new Map();

  const addBucket = (layer, reason, action) => {
    const existing = buckets.get(layer);
    if (existing) {
      existing.hitCount += 1;
      return;
    }
    buckets.set(layer, {
      layer,
      layerLabel: layerLabelMap[layer] || layer,
      reason,
      action,
      hitCount: 1,
      priority: 'P2'
    });
  };

  badCaseReplay.value.items.forEach((item) => {
    if (!item.evidenceIds.length) {
      addBucket(
        'rag',
        '结论没有直接证据挂接，说明召回或证据绑定可能不足。',
        '优先检查切片、召回、rerank 和 evidence 绑定规则，先补证据闭环。'
      );
    }

    if (item.matchScore <= 1) {
      addBucket(
        'rag',
        '证据匹配分数偏低，说明当前检索结果与结论语义贴合度不够。',
        '优先优化关键词召回、向量召回、rerank 维度，以及 claim-to-evidence 匹配逻辑。'
      );
    }

    if (item.confidence === 'low') {
      addBucket(
        'prompt',
        '低置信度结论仍被输出，说明分析层的约束和表达策略还不够稳。',
        '补强“低证据时降级表达 / 不足则明确说明”的 Prompt 约束，并增加保守输出策略。'
      );
    }

    if (item.stage === 'report' && item.confidence === 'low') {
      addBucket(
        'workflow',
        '低置信度问题已经进入报告阶段，说明流程里缺少报告前的质量门。',
        '在 report 前增加质量检查节点，低置信度时先回退到 retrieve 或 analyze。'
      );
    }
  });

  badCaseReplay.value.items.forEach((item) => {
    if (chartEvidenceRecords.value.length > 0 && !item.chartEvidenceIds.length) {
      addBucket(
        'tool',
        '这次会话里已经有图表证据，但有些判断点仍然没有引用图表。',
        '补充“图表到判断点”的绑定检查，并要求相关判断在有图表时必须引用图表依据。'
      );
    }

    if (!item.evidenceTypes.includes('sql') && !item.evidenceTypes.includes('rag')) {
      addBucket(
        'rag',
        '有些判断点缺少 SQL 或知识库这类主证据，更多依赖较弱的次级信号。',
        '补一条规则：关键判断至少要绑定一条 SQL 或知识库证据，否则不能保留高把握。'
      );
    }
  });

  if (badCaseReplay.value.orphanChartEvidence.length) {
    addBucket(
      'tool',
      '有些图表证据已经生成，但没有被任何判断点引用。',
      '自动识别“孤立图表证据”，并把它重新送回分析或治理检查。'
    );
  }

  if (evidenceCoverageScorecard.value.sqlCount === 0) {
    addBucket(
      'tool',
      '当前回答链路没有 SQL 数据证据，所以诊断还没有真正建立在结构化设备数据上。',
      '给关键判断补 SQL 证据绑定，并在有设备遥测或告警表时把结构化数据检索作为必经分支。'
    );
  }

  if (evidenceCoverageScorecard.value.ragCount === 0) {
    addBucket(
      'rag',
      '当前回答链路没有知识库证据覆盖，所以结论可能缺少手册、故障码或维护知识支撑。',
      '把手册和故障码检索变成标准证据分支，并在最终回答前把检索片段绑定到判断点。'
    );
  }

  if (
    evidenceCoverageScorecard.value.chartCount > 0 &&
    evidenceCoverageScorecard.value.chartCoverageRate < 60
  ) {
    addBucket(
      'tool',
      '虽然已经有图表证据，但图表和判断点之间的绑定率仍然偏低。',
      '补充图表引用规则，在分析阶段推荐应引用的图，并在关键图表未绑定时阻止正式报告输出。'
    );
  }

  if (evidenceCoverageScorecard.value.findingBindingRate < 70) {
    addBucket(
      'workflow',
      '大量判断点仍然缺少稳定证据绑定，说明推理链路还没有真正闭环。',
      'Insert a finding-binding verification step before the final answer and send low-binding findings back to retrieve / analyze for补证据.'
    );
  }

  workflowStageDetails.value.forEach((detail) => {
    const numericDuration = Number.parseFloat(String(detail.durationLabel).replace(' ms', ''));
    if (!Number.isNaN(numericDuration) && numericDuration >= 1500) {
      addBucket(
        'workflow',
        `阶段“${detail.label}”耗时偏高，说明当前流程拆分或执行顺序仍有优化空间。`,
        '检查该阶段是否存在重复工具调用、串行等待或不必要的回环。'
      );
    }

    if (detail.toolCount >= 3) {
      addBucket(
        'tool',
        `阶段“${detail.label}”工具调用较多，说明工具边界和聚合策略可能还不够紧凑。`,
        '考虑收敛重复工具、增加聚合工具，或提前做参数标准化，减少模型反复试探。'
      );
    }
  });

  const items = Array.from(buckets.values())
    .map((item) => ({
      ...item,
      priority: item.hitCount >= 3 ? 'P0' : item.hitCount >= 2 ? 'P1' : 'P2'
    }))
    .sort((a, b) => {
      const order = { P0: 0, P1: 1, P2: 2 };
      return (order[a.priority] ?? 9) - (order[b.priority] ?? 9);
    });

  return { items };
});
const resolveGovernanceStartNode = (layer) => {
  if (!layer) return null;
  if (layer === 'rag') {
    return executionTimeline.value.find((item) => item.kind === 'evidence' && item.meta.includes('knowledge'))
      || executionTimeline.value.find((item) => item.kind === 'stage' && item.stage === 'retrieve')
      || null;
  }
  if (layer === 'prompt') {
    return executionTimeline.value.find((item) => item.kind === 'finding' && item.stage === 'analyze')
      || executionTimeline.value.find((item) => item.kind === 'stage' && item.stage === 'analyze')
      || null;
  }
  if (layer === 'tool') {
    return executionTimeline.value.find((item) => item.kind === 'tool' && (toolRiskProfiles.value.get(item.id)?.score || 0) > 0)
      || executionTimeline.value.find((item) => item.kind === 'evidence' && item.meta.some((meta) => ['visualization', 'execution', 'database'].includes(meta)))
      || executionTimeline.value.find((item) => item.kind === 'tool')
      || null;
  }
  if (layer === 'workflow') {
    return executionTimeline.value.find((item) => item.kind === 'stage' && item.stage === 'report')
      || executionTimeline.value.find((item) => item.kind === 'stage' && item.stage === 'analyze')
      || null;
  }
  return null;
};
const getGovernanceDisplayCopy = (layer) => {
  if (layer === 'rag') {
    return {
      displayReason: '结论缺少直接证据，说明知识召回、重排或证据绑定还不够稳定。',
      displayAction: '优先检查切片、召回、重排和证据绑定规则，先把“结论-证据”这条链补完整。'
    };
  }
  if (layer === 'prompt') {
    return {
      displayReason: '低把握的判断仍然被输出，说明分析表达还不够保守。',
      displayAction: '补充“证据不足时降级表达 / 明确说明”的提示词约束，避免模型把话说得过满。'
    };
  }
  if (layer === 'tool') {
    return {
      displayReason: 'SQL、图表或其他工具结果没有稳定支撑关键判断，工具结果还没有真正回流到结论。',
      displayAction: '给关键判断补 SQL / 图表证据绑定，并检查工具结果是否真正参与了最终结论生成。'
    };
  }
  if (layer === 'workflow') {
    return {
      displayReason: '判断点和证据绑定还不稳定，最终回答前缺少一次闭环校验。',
      displayAction: '在最终回答前增加“判断点-证据绑定校验”，绑定不足就回退到检索或分析阶段补证据。'
    };
  }
  return {
    displayReason: '当前链路里还有稳定性问题，需要继续排查。',
    displayAction: '先定位问题最集中的链路层，再补约束、补证据或补执行校验。'
  };
};
const governanceSuggestions = computed(() => ({
  items: badCaseGovernance.value.items.map((item) => {
    const startNode = resolveGovernanceStartNode(item.layer);
    const displayCopy = getGovernanceDisplayCopy(item.layer);
    return {
      ...item,
      ...displayCopy,
      countText: `命中 ${item.hitCount} 次 · 优先级 ${item.priority}`,
      startNode: startNode
        ? {
          id: startNode.id,
          kind: startNode.kind,
          kindLabel: startNode.kindLabel,
          title: startNode.title
        }
        : null
    };
  })
}));
const governanceLedger = computed(() => {
  const ownerMap = {
    rag: 'Knowledge Owner',
    prompt: 'Prompt Owner',
    tool: 'Tool Owner',
    workflow: 'Workflow Owner'
  };
  const stageMap = {
    rag: 'retrieve / evidence binding',
    prompt: 'analyze / response shaping',
    tool: 'collect / execution',
    workflow: 'cross-stage orchestration'
  };

  const checklistMap = {
    rag: [
      '抽样检查 bad-case 的切片、召回和 rerank 结果',
      '补关键词召回与 evidence 绑定规则',
      '把低分匹配 case 纳入检索评测集'
    ],
    prompt: [
      '补强低证据场景下的保守表达约束',
      '区分“明确结论”和“待确认假设”两类输出',
      '增加 groundedness 和低置信度回归样例'
    ],
    tool: [
      '检查是否存在重复工具、重复参数试探或工具粒度过细',
      '优先聚合高频串行工具调用',
      '给高风险工具补统一输入规范和结果摘要'
    ],
    workflow: [
      '在 report 前增加质量门或回退节点',
      '检查是否存在不必要的串行等待和重复回环',
      '把长耗时阶段单独做性能观测与告警'
    ]
  };

  const items = badCaseGovernance.value.items.map((item) => ({
    layer: item.layer,
    title: `${item.layerLabel} 治理项`,
    priority: item.priority,
    owner: ownerMap[item.layer] || item.layer,
    stage: stageMap[item.layer] || 'general',
    triggerCount: item.hitCount,
    goal: item.action,
    checklist: checklistMap[item.layer] || ['补充该层的排查清单']
  }));

  const priorityCounts = items.reduce(
    (acc, item) => {
      acc[item.priority] = (acc[item.priority] || 0) + 1;
      return acc;
    },
    {}
  );

  return {
    items,
    summary: [
      { label: 'P0', value: priorityCounts.P0 || 0 },
      { label: 'P1', value: priorityCounts.P1 || 0 },
      { label: 'P2', value: priorityCounts.P2 || 0 }
    ]
  };
});
const governanceOptimizationCandidates = computed(() => {
  const candidates = [];
  const seen = new Set();

  badCaseGovernance.value.items.forEach((item) => {
    const key = `${item.layer}-governance`;
    if (seen.has(key)) return;
    seen.add(key);
    candidates.push({
      layer: item.layer,
      priority: item.priority,
      title: `${item.layerLabel} 优化候选项`,
      reason: item.reason,
      recommendation: item.action,
      source: '治理建议'
    });
  });

  governanceSaveState.value.ledgerItems
    .filter((item) => (item.status || 'open') === 'verified')
    .slice(0, 8)
    .forEach((item) => {
      const tags = Array.isArray(item.tags) ? item.tags : [];
      const mappedLayer = tags.find((tag) => ['prompt', 'rag', 'tool', 'workflow'].includes(tag)) || 'workflow';
      const key = `${mappedLayer}-${item.record_id}`;
      if (seen.has(key)) return;
      seen.add(key);
      candidates.push({
        layer: mappedLayer,
        priority: item.priority || 'P2',
        title: `已确认台账候选项：${item.record_id}`,
        reason: item.verified_result || item.next_action || '该治理记录已完成确认',
        recommendation: item.next_action || '建议把这项已确认改动纳入主诊断链路。',
        source: '已确认台账'
      });
    });

  return candidates.sort((a, b) => {
    const order = { P0: 0, P1: 1, P2: 2 };
    return (order[a.priority] ?? 9) - (order[b.priority] ?? 9);
  });
});
const governanceExportMarkdown = computed(() => {
  const lines = [
    '# 治理台账快照',
    '',
    '## 1. 概览',
    ...governanceLedger.value.summary.map((item) => `- ${item.label}: ${item.value}`),
    '',
    '## 2. 证据覆盖情况',
    `- 等级: ${evidenceCoverageScorecard.value.grade}`,
    ...evidenceCoverageScorecard.value.metrics.map((item) => `- ${item.label}: ${item.value}`),
    '',
    '## 3. 当前风险',
    ...(badCaseReplay.value.risks.length
      ? badCaseReplay.value.risks.map((item) => `- ${item.text}`)
      : ['- 当前未识别到明确风险。']),
    '',
    '## 4. 安全动作记录',
    ...(actionLedger.value.length
      ? actionLedger.value.map((item) => `- ${item.toolName}: ${item.publicationStatusLabel} / ${item.action} / 原始文件=${item.targetFilename} / 实际文件=${item.finalFilename}${item.statusText ? ` / ${item.statusText}` : ''}`)
      : ['- 当前没有记录到安全动作。']),
    '',
    '## 5. 治理项'
  ];

  governanceLedger.value.items.forEach((item) => {
    lines.push(`### ${item.title}`);
    lines.push(`- 优先级: ${item.priority}`);
    lines.push(`- 负责人: ${item.owner}`);
    lines.push(`- 所属阶段: ${item.stage}`);
    lines.push(`- 触发次数: ${item.triggerCount}`);
    lines.push(`- 目标: ${item.goal}`);
    lines.push('- 执行清单:');
    item.checklist.forEach((task, index) => {
      lines.push(`  ${index + 1}. ${task}`);
    });
    lines.push('');
  });

  if (governanceOptimizationCandidates.value.length) {
    lines.push('## 6. 优化候选项');
    governanceOptimizationCandidates.value.forEach((item) => {
      lines.push(`### ${item.title}`);
      lines.push(`- 层级: ${item.layer}`);
      lines.push(`- 优先级: ${item.priority}`);
      lines.push(`- 来源: ${item.source}`);
      lines.push(`- 原因: ${item.reason}`);
      lines.push(`- 建议动作: ${item.recommendation}`);
      lines.push('');
    });
  }

  return lines.join('\n').trim();
});
const governanceExportJson = computed(() =>
  JSON.stringify(
    {
      summary: governanceLedger.value.summary,
      evidence_scorecard: evidenceCoverageScorecard.value,
      risks: badCaseReplay.value.risks,
      safe_actions: actionLedger.value,
      items: governanceLedger.value.items
    },
    null,
    2
  )
);
const governanceExportPayload = computed(() => ({
  summary: governanceLedger.value.summary,
  evidence_scorecard: evidenceCoverageScorecard.value,
  risks: badCaseReplay.value.risks,
  safe_actions: actionLedger.value,
  items: governanceLedger.value.items,
  timeline: diagnosticTimeline.value,
  optimization_candidates: governanceOptimizationCandidates.value
}));
const governanceDocTemplate = computed(() => {
  const lines = [
    '# Bad-Case 治理文档',
    '',
    '## 1. 问题概览',
    ...governanceLedger.value.summary.map((item) => `- ${item.label}: ${item.value}`),
    '',
    '## 2. 风险总结',
    ...(badCaseReplay.value.risks.length
      ? badCaseReplay.value.risks.map((item) => `- ${item.text}`)
      : ['- 当前未检测到显式风险。']),
    '',
    '## 3. 分阶段观察',
    ...diagnosticTimeline.value.map((item) => `- ${item.label}: ${item.statusLabel}，${item.evidenceCount} 条证据，${item.findingCount} 条结论`),
    '',
    '## 4. 治理项',
  ];

  governanceLedger.value.items.forEach((item, index) => {
    lines.push(`### 4.${index + 1} ${item.title}`);
    lines.push(`- 优先级：${item.priority}`);
    lines.push(`- 责任人角色：${item.owner}`);
    lines.push(`- 责任层面：${item.stage}`);
    lines.push(`- 触发次数：${item.triggerCount}`);
    lines.push(`- 治理目标：${item.goal}`);
    lines.push('- 执行清单：');
    item.checklist.forEach((task, taskIndex) => {
      lines.push(`  ${taskIndex + 1}. ${task}`);
    });
    lines.push('- 预留结果：');
    lines.push('  1. 本周动作：');
    lines.push('  2. 验证方式：');
    lines.push('  3. 后续计划：');
    lines.push('');
  });

  lines.push('## 5. 结论');
  lines.push('- 当前 bad-case 更集中在哪一层：');
  lines.push('- 本周优先治理项：');
  lines.push('- 需要补充的数据或评测：');

  if (governanceOptimizationCandidates.value.length) {
    lines.push('');
    lines.push('## 6. Main Pipeline Optimization Candidates');
    governanceOptimizationCandidates.value.forEach((item, index) => {
      lines.push(`### 6.${index + 1} ${item.title}`);
      lines.push(`- Layer: ${item.layer}`);
      lines.push(`- Priority: ${item.priority}`);
      lines.push(`- Source: ${item.source}`);
      lines.push(`- Reason: ${item.reason}`);
      lines.push(`- Recommendation: ${item.recommendation}`);
      lines.push('');
    });
  }

  return lines.join('\n').trim();
});
const governanceWeeklyReport = computed(() => {
  const lines = [
    '# 治理周报',
    '',
    '## 1. 整体情况',
    `- 当前视图中的台账总数: ${governanceLedgerSummary.value.total}`,
    ...governanceLedgerSummary.value.statusItems.map((item) => `- ${getLedgerStatusLabel(item.key)}: ${item.value}`),
    ...governanceLedgerSummary.value.priorityItems.map((item) => `- 优先级 ${item.key}: ${item.value}`),
    '',
    '## 1.1 证据覆盖情况',
    `- 等级: ${evidenceCoverageScorecard.value.grade}`,
    ...evidenceCoverageScorecard.value.metrics.map((item) => `- ${item.label}: ${item.value}`),
    '',
    '## 2. 当前风险',
    ...(badCaseReplay.value.risks.length
      ? badCaseReplay.value.risks.slice(0, 5).map((item) => `- ${item.text}`)
      : ['- 当前视图下未识别到明确风险。']),
    '',
    '## 3. 安全动作',
    ...(actionLedger.value.length
      ? actionLedger.value.slice(0, 8).map((item) => `- ${item.toolName}: ${item.publicationStatusLabel} / ${item.action} / ${item.finalFilename}`)
      : ['- 本次会话未记录到安全动作。']),
    '',
    '## 4. 看板概览'
  ];

  governanceLedgerKanban.value.columns.forEach((column) => {
    lines.push(`### ${getLedgerStatusLabel(column.key)}（${column.items.length}）`);
    if (!column.items.length) {
      lines.push('- 当前列没有记录。');
    } else {
      column.items.forEach((item) => {
        lines.push(`- ${item.record_id}: ${item.owner || '未分配'} / ${item.priority || 'P2'} / ${item.due_date || '暂无截止日期'}`);
        lines.push(`  下一步: ${item.next_action || '暂无下一步动作'}`);
      });
    }
    lines.push('');
  });

  lines.push('## 5. 重点台账记录');
  if (!governanceSaveState.value.ledgerItems.length) {
    lines.push('- 当前没有可用台账记录。');
  } else {
    governanceSaveState.value.ledgerItems.slice(0, 6).forEach((item) => {
      lines.push(`### ${item.record_id}`);
      lines.push(`- 状态: ${getLedgerStatusLabel(item.status || 'open')}`);
      lines.push(`- 负责人: ${item.owner || '未分配'}`);
      lines.push(`- 优先级: ${item.priority || 'P2'}`);
      lines.push(`- 截止日期: ${item.due_date || '暂无'}`);
      lines.push(`- 标签: ${(item.tags || []).join(', ') || '暂无'}`);
      lines.push(`- 下一步动作: ${item.next_action || '暂无'}`);
      lines.push(`- 确认结果: ${item.verified_result || '暂无'}`);
      lines.push('');
    });
  }

  lines.push('## 6. 下周重点');
  const topOpen = governanceSaveState.value.ledgerItems
    .filter((item) => (item.status || 'open') !== 'verified')
    .slice(0, 3);
  if (!topOpen.length) {
    lines.push('- 持续跟踪已确认项，并继续收集新的 bad-case。');
  } else {
    topOpen.forEach((item) => {
      lines.push(`- ${item.record_id}: ${item.next_action || '补充下一步治理动作'}（${item.owner || '未分配'}）`);
    });
  }

  lines.push('');
  lines.push('## 7. 回流主链路');
  if (!governanceOptimizationCandidates.value.length) {
    lines.push('- 目前还没有提取出新的优化候选项。');
  } else {
    governanceOptimizationCandidates.value.slice(0, 6).forEach((item) => {
      lines.push(`- [${item.layer}] ${item.title}: ${item.recommendation}`);
    });
  }

  return lines.join('\n').trim();
});
const technicalUpgradeBacklog = computed(() => {
  const grouped = new Map();
  governanceOptimizationCandidates.value.forEach((item) => {
    if (!grouped.has(item.layer)) {
      grouped.set(item.layer, []);
    }
    grouped.get(item.layer).push(item);
  });

  const lines = [
    '# Technical Upgrade Backlog',
    '',
    '## Goal',
    '- Turn governance findings into actionable engineering tasks for the main diagnostic pipeline.',
    '',
    '## Evidence Coverage Baseline',
    `- Grade: ${evidenceCoverageScorecard.value.grade}`,
    `- Score: ${evidenceCoverageScorecard.value.score}`,
    ...evidenceCoverageScorecard.value.metrics.map((item) => `- ${item.label}: ${item.value}`),
  ];

  ['prompt', 'rag', 'tool', 'workflow'].forEach((layer) => {
    const items = grouped.get(layer) || [];
    lines.push('');
    lines.push(`## ${layer.toUpperCase()}`);
    if (!items.length) {
      lines.push('- No backlog items in this layer yet.');
      return;
    }
    items.forEach((item, index) => {
      lines.push(`### ${layer}.${index + 1} ${item.title}`);
      lines.push(`- Priority: ${item.priority}`);
      lines.push(`- Source: ${item.source}`);
      lines.push(`- Problem: ${item.reason}`);
      lines.push(`- Proposed Change: ${item.recommendation}`);
      lines.push(`- Acceptance: Observe more grounded outputs and fewer repeated bad-cases in this layer.`);
      lines.push('');
    });
  });

  lines.push('## Execution Order');
  governanceOptimizationCandidates.value.slice(0, 6).forEach((item, index) => {
    lines.push(`${index + 1}. [${item.layer}] ${item.title} (${item.priority})`);
  });

  return lines.join('\n').trim();
});
const toolDetailsExpanded = ref(false);
const hasRunningTool = computed(() => toolEvents.value[toolEvents.value.length - 1]?.type === 'tool_start');
const toolDetailsSummary = computed(() => {
  if (!toolEvents.value.length) return '暂无执行记录'
  const lastToolEvent = toolEvents.value[toolEvents.value.length - 1]
  if (lastToolEvent?.type === 'tool_start') {
    return `正在执行 ${lastToolEvent.tool || '工具'}，可展开查看原始明细`
  }
  return `已记录 ${toolEvents.value.length} 条工具事件，最近完成 ${lastToolEvent?.tool || '工具调用'}`
});

//处理llm内容
const processContent = (content) => {
  if (!content) return '';

  // 如果是流式传输且内容为空，显示打字效果
  if (props.isStream && content === '') {
    return '<div class="streaming-placeholder">正在输入<span class="dots"><span>.</span><span>.</span><span>.</span></span></div>';
  }

  // 配置 marked 选项
  marked.setOptions({
    breaks: true,
    gfm: true
  });

  // 统一图片处理：优先完整替换 Markdown 图片语法，随后处理裸露图片链接
  const normalizeImgUrl = (url) => {
    if (!url) return url;
    url = url.replace(/^['"]|['"]$/g, '').trim();
    if (url.startsWith('../images/')) url = url.replace('../images/', '/images/');
    if (url.startsWith('./images/')) url = url.replace('./images/', '/images/');
    if (url.startsWith('images/')) url = `/${url}`;
    // 若配置了后端基础地址，则为 /images/** 补全绝对地址，避免跨源时加载失败
    if (BACKEND_BASE && /^\/(public\/)?images\//i.test(url)) {
      const base = BACKEND_BASE.replace(/\/+$/, '');
      url = `${base}${url}`;
    }
    return url;
  };

  const markdownImageReg = /!\[([^\]]*)\]\(([^)]+)\)/g;
  content = content.replace(markdownImageReg, (_full, alt, url) => {
    const src = normalizeImgUrl(url);
    const altText = alt || '图片';
    const allowed = /^(https?:\/\/|\/images\/|\/public\/images\/|\.\.\/images\/)[^\s)]+$/i.test(src);
    if (!allowed) {
      // 非法/占位链接（如 “..图片”）不替换，保留原文，避免生成坏的 img
      return _full;
    }
    return `<img src="${src}" alt="${altText}" class="chat-image-thumb" data-preview="1" style="max-width:100%;height:auto;max-height:380px;margin:8px 0;border-radius:4px;cursor:pointer;" />`;
  });

  // 兼容：反引号包裹的完整图片路径，如 `/images/example.png`
  const backtickedImgPathReg = /`{1,3}\s*((?:\/images\/|\/public\/images\/|\.\.\/images\/|\.\/images\/)[^`\s]+\.(?:png|jpg|jpeg|gif|svg|webp))\s*`{1,3}/gi;
  content = content.replace(backtickedImgPathReg, (_full, url) => {
    const src = normalizeImgUrl(url);
    return `<img src="${src}" alt="图片" class="chat-image-thumb" data-preview="1" style="max-width:100%;height:auto;max-height:380px;margin:8px 0;border-radius:4px;cursor:pointer;" />`;
  });

  // 兼容：仅返回文件名（如 `soil_monitoring_fig.png`）的情况 → 映射为 /images/文件名
  const backtickedFilenameReg = /`{1,3}\s*([A-Za-z0-9._-]+\.(?:png|jpg|jpeg|gif|svg|webp))\s*`{1,3}/gi;
  content = content.replace(backtickedFilenameReg, (_full, filename) => {
    const src = normalizeImgUrl(`/images/${filename}`);
    return `<img src="${src}" alt="${filename}" class="chat-image-thumb" data-preview="1" style="max-width:100%;height:auto;max-height:380px;margin:8px 0;border-radius:4px;cursor:pointer;" />`;
  });

  // 兼容：仅给出 figure ID（如 `soil_temperature_trend_fig`）的情况，默认映射为 .png
  const backtickedFigureIdReg = /`{1,3}\s*([A-Za-z0-9_-]+_(?:fig|figure|chart|plot|img))\s*`{1,3}/gi;
  content = content.replace(backtickedFigureIdReg, (_full, figureId) => {
    const fallbackFilename = `${figureId}.png`;
    const src = normalizeImgUrl(`/images/${fallbackFilename}`);
    return `<img src="${src}" alt="${figureId}" class="chat-image-thumb" data-preview="1" style="max-width:100%;height:auto;max-height:380px;margin:8px 0;border-radius:4px;cursor:pointer;" />`;
  });
  // 兼容：独占一行的裸文件名
  const lineFilenameReg = /^(\s*)([A-Za-z0-9._-]+\.(?:png|jpg|jpeg|gif|svg|webp))(\s*)$/gim;
  content = content.replace(lineFilenameReg, (_full, pre, filename, post) => {
    const src = normalizeImgUrl(`/images/${filename}`);
    return `${pre}<img src="${src}" alt="${filename}" class="chat-image-thumb" data-preview="1" style="max-width:100%;height:auto;max-height:380px;margin:8px 0;border-radius:4px;cursor:pointer;" />${post}`;
  });

  const bareImgUrlReg = /(?:^|\s)(\/images\/[\w\-_.]+\.(?:png|jpg|jpeg|gif|svg|webp)|\/public\/images\/[\w\-_.]+\.(?:png|jpg|jpeg|gif|svg|webp)|https?:\/\/mdn\.alipayobjects\.com[^\s"]*|https?:\/\/[^\s")]+\.(?:png|jpg|jpeg|gif|svg|webp)(?:\?[^\s"]*)?)/gi;
  content = content.replace(bareImgUrlReg, (match, url) => {
    const src = normalizeImgUrl(url || match.trim());
    const leading = match.startsWith(' ') ? ' ' : '';
    return `${leading}<img src="${src}" alt="图片" class="chat-image-thumb" data-preview="1" style="max-width:100%;height:auto;max-height:380px;margin:8px 0;border-radius:4px;cursor:pointer;" />`;
  });

  content = stripReportMentions(content);

  let result = '';
  let isInThinkBlock = false;
  let currentBlock = '';

  // 处理think标签（假设使用```作为块标记，可根据实际标签调整）
  for (let i = 0; i < content.length; i++) {
    // 匹配块开始标记 ```
    if (content.slice(i, i + 3) === '```') {
      isInThinkBlock = true;
      if (currentBlock) {
        result += marked.parse(currentBlock);
      }
      currentBlock = '';
      i += 2; // 跳过已匹配的3个字符（i自增1 + 此处加2）
      continue;
    }

    // 匹配块结束标记 ```
    if (content.slice(i, i + 3) === '```' && isInThinkBlock) {
      isInThinkBlock = false;
      // 处理块内 Markdown 图片与裸链接
      let processedBlock = currentBlock.replace(markdownImageReg, (_full, alt, url) => {
        const src = normalizeImgUrl(url);
        const altText = alt || '图片';
        const allowed = /^(https?:\/\/|\/images\/|\/public\/images\/|\.\.\/images\/)[^\s)]+$/i.test(src);
        if (!allowed) return _full;
        return `<img src="${src}" alt="${altText}" class="chat-image-thumb" data-preview="1" style="max-width:100%;height:auto;max-height:380px;margin:8px 0;border-radius:4px;cursor:pointer;" />`;
      });
      processedBlock = processedBlock.replace(bareImgUrlReg, (match, url) => {
        const src = normalizeImgUrl(url || match.trim());
        const leading = match.startsWith(' ') ? ' ' : '';
        return `${leading}<img src="${src}" alt="图片" class="chat-image-thumb" data-preview="1" style="max-width:100%;height:auto;max-height:380px;margin:8px 0;border-radius:4px;cursor:pointer;" />`;
      });
      result += `<div class="think-block">${marked.parse(processedBlock)}</div>`;
      currentBlock = '';
      i += 2;
      continue;
    }

    currentBlock += content[i];
  }

  // 处理剩余内容（非块内的文本）
  if (currentBlock) {
    // 解析Markdown并拼接结果
    result += isInThinkBlock
        ? `<div class="think-block">${marked.parse(currentBlock)}</div>`
        : marked.parse(currentBlock);
  }

  // 净化HTML：允许img标签及必要属性
  const cleanHtml = DOMPurify.sanitize(result, {
    ADD_TAGS: ['think', 'code', 'pre', 'span', 'img'],
    ADD_ATTR: ['class', 'language', 'src', 'alt', 'style', 'data-preview', 'href', 'target', 'rel'],
    USE_PROFILES: { html: true }
  });

  // 处理代码块复制功能
  const tempDiv = document.createElement('div');
  tempDiv.innerHTML = cleanHtml;

  const preElements = tempDiv.querySelectorAll('pre');
  preElements.forEach(pre => {
    const code = pre.querySelector('code');
    if (code) {
      const wrapper = document.createElement('div');
      wrapper.className = 'code-block-wrapper';

      const copyBtn = document.createElement('button');
      copyBtn.className = 'code-copy-button';
      copyBtn.title = '复制代码';
      copyBtn.innerHTML = `
        <svg xmlns="http://www.w3.org/2000/svg" class="code-copy-icon" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
        </svg>
      `;

      // 复制功能逻辑
      copyBtn.addEventListener('click', () => {
        navigator.clipboard.writeText(code.textContent).then(() => {
          const successMsg = wrapper.querySelector('.copy-success-message');
          if (successMsg) {
            successMsg.style.opacity = '1';
            setTimeout(() => {
              successMsg.style.opacity = '0';
            }, 2000);
          }
        });
      });

      const successMsg = document.createElement('div');
      successMsg.className = 'copy-success-message';
      successMsg.textContent = '已复制!';
      successMsg.style.opacity = '0';
      successMsg.style.transition = 'opacity 0.3s ease';

      wrapper.appendChild(copyBtn);
      wrapper.appendChild(pre.cloneNode(true));
      wrapper.appendChild(successMsg);
      pre.parentNode.replaceChild(wrapper, pre);
    }
  });

  return tempDiv.innerHTML;
};

// 处理后的内容
const processedContent = computed(() => {
  if (!props.message.content) return '';
  return processContent(props.message.content);
});

// 从原始消息提取报告链接
const reportLinks = computed(() => {
  const links = new Set();
  const structuredUrl = props.message?.reportUrl || props.message?.report_url;
  const artifactUrl = props.message?.reportArtifact?.report_url || props.message?.report_artifact?.report_url;
  const artifactFilename = props.message?.reportArtifact?.report_filename || props.message?.report_artifact?.report_filename;
  const reportFilename = props.message?.reportFilename || props.message?.report_filename;

  if (structuredUrl) {
    links.add(structuredUrl);
  }
  if (artifactUrl) {
    links.add(artifactUrl);
  }
  if (artifactFilename) {
    links.add(`/reports/${artifactFilename}`);
  }
  if (reportFilename) {
    links.add(`/reports/${reportFilename}`);
  }
  extractReportLinks(props.message?.content || '').forEach((item) => links.add(item));
  phase4ReportLinks.value.forEach((item) => links.add(item));
  return [...links].filter(isSafeReportUrl);
});
const hasReportMentionButNoLinks = computed(() => {
  const raw = props.message?.content || '';
  const hasMention = /报告文件|报告已保存至|HTML 报告已保存至|报告已生成|诊断报告已生成|\/reports\//i.test(raw);
  return hasMention && reportLinks.value.length === 0;
});
const hasFinalAnswerContent = computed(() => (
  Boolean(processedContent.value) ||
  Boolean(showChart.value) ||
  Boolean(props.message?.imageUrl) ||
  reportLinks.value.length > 0 ||
  hasReportMentionButNoLinks.value
));
const hasAssistantProcessDetails = computed(() => (
  hasTaskSnapshot.value ||
  hasAssistantSummary.value ||
  evidenceSourceCards.value.length > 0 ||
  hasWorkflowContractPanel.value ||
  hasAssistantDetails.value
));
const shouldShowProcessDetails = computed(() => (
  !hasFinalAnswerContent.value || assistantDetailsExpanded.value
));
const shouldShowFinalProcessToggle = computed(() => (
  !isUser.value && hasFinalAnswerContent.value && hasAssistantProcessDetails.value
));
const shouldShowInlineAssistantDetailsToggle = computed(() => (
  hasAssistantDetails.value && !hasFinalAnswerContent.value
));
const finalProcessToggleHint = computed(() => {
  const sections = [];
  if (hasTaskSnapshot.value) sections.push('任务进度');
  if (hasAssistantSummary.value) sections.push('摘要');
  if (hasWorkflowContractPanel.value) sections.push('结构化流程');
  if (evidenceSourceCards.value.length || hasAssistantDetails.value) sections.push('证据与工具');
  return sections.length ? sections.join(' / ') : '诊断过程';
});

watch(hasFinalAnswerContent, (hasFinalAnswer) => {
  if (hasFinalAnswer) {
    assistantDetailsExpanded.value = false;
  }
});

// 处理温度数据并显示图表
const handleTemperatureData = (chartData) => {
  // 数据验证：无chartData或格式错误则隐藏图表
  if (!chartData || !chartData.data || !Array.isArray(chartData.data)) {
    showChart.value = false;
    if (chartInstance.value) {
      chartInstance.value.destroy();
      chartInstance.value = null;
    }
    return;
  }

  // 提取数据、样式、标题等信息
  const { data, style, title, type: chartType = 'line', width, height } = chartData;

  // 数据格式校验：确保每个数据项有time、value、group
  const isValidData = data.every(item =>
      item && typeof item === 'object' &&
      'time' in item && 'value' in item && 'group' in item
  );
  if (!isValidData) {
    console.error('无效的图表数据格式');
    showChart.value = false;
    return;
  }

  //🔥数据有效时，显示图表容器
  showChart.value = true;

  // 按group分组数据
  const groupedData = {};
  data.forEach(item => {
    const group = item.group;
    if (!groupedData[group]) groupedData[group] = [];
    groupedData[group].push({
      value: Number(item.value),
      time: item.time
    });
  });

  const groups = Object.keys(groupedData);
  if (groups.length === 0) {
    showChart.value = false;
    return;
  }

  // 准备图表数据集（默认隐藏，悬停显示）
  const datasets = groups.map(group => ({
    label: group,
    data: groupedData[group].map(item => item.value),
    borderColor: getRandomColor(),
    backgroundColor: 'transparent',
    tension: 0.4,
    fill: false,
    // 默认状态隐藏数据点
    pointRadius: 0,         // 正常状态下不显示数据点
    pointHoverRadius: 5,    // 鼠标悬停时显示数据点（大小为5）
    pointHoverBackgroundColor: getRandomColor(), // 悬停时数据点颜色（与线条一致）
    pointHoverBorderWidth: 2, // 悬停时数据点边框宽度
    pointHoverBorderColor: '#fff' // 悬停时数据点边框颜色（白色描边更明显）
  }));

  const labels = groupedData[groups[0]].map(item => item.time);

  // 等待DOM更新完成
  nextTick(() => {
    // 检查canvas元素是否存在且尺寸有效
    if (!chartRef.value) {
      console.error('Canvas元素不存在');
      showChart.value = false;
      return;
    }

    // 检查canvas尺寸是否有效
    if (!chartRef.value.width || !chartRef.value.height) {
      console.error('Canvas尺寸无效');
      showChart.value = false;
      return;
    }

    // 创建图表
    if (chartInstance.value) {
      chartInstance.value.destroy();
    }

    try {
      chartInstance.value = new Chart(chartRef.value, {
        type: chartType,
        data: { labels, datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: 'index', intersect: false },
          plugins: {
            legend: { position: 'top', labels: { usePointStyle: true, boxWidth: 6 } },
            title: { display: true, text: title || '设备数据趋势', font: { size: 16 } },
            tooltip: { padding: 10, cornerRadius: 6 }
          },
          scales: {
            y: {
              beginAtZero: false,
              title: { display: true, text: '数值' },
              ticks: { callback: v => `${v}` }
            },
            x: {
              title: { display: true, text: '时间' },
              ticks: { maxRotation: 45, minRotation: 45 }
            }
          },
          animation: { duration: 1000 },
          ...(style || {}) // 合并自定义样式
        }
      });
    } catch (error) {
      console.error('创建图表失败:', error);
      showChart.value = false;
    }
  });
};

// 生成随机颜色
const getRandomColor = () => {
  // 预定义一组适合图表的颜色，避免随机颜色过于相似
  const presetColors = [
    'rgb(75, 192, 192)',
    'rgb(255, 99, 132)',
    'rgb(54, 162, 235)',
    'rgb(255, 206, 86)',
    'rgb(153, 102, 255)',
    'rgb(255, 159, 64)',
    'rgb(231, 233, 237)',
    'rgb(108, 117, 125)'
  ];

  // 随机选择预定义颜色
  return presetColors[Math.floor(Math.random() * presetColors.length)];
};

// 获取图表图片
const getChartImage = async () => {
  if (!chartRef.value || !chartInstance.value) {
    return null;
  }

  await nextTick();
  return chartRef.value.toDataURL('image/png', 0.9);
};

// 代码块复制功能
const setupCodeBlockCopyButtons = () => {
  if (!contentRef.value) return;

  const codeBlocks = contentRef.value.querySelectorAll('.code-block-wrapper');
  codeBlocks.forEach(block => {
    const copyButton = block.querySelector('.code-copy-button');
    const codeElement = block.querySelector('code');
    const successMessage = block.querySelector('.copy-success-message');

    if (copyButton && codeElement) {
      const newCopyButton = copyButton.cloneNode(true);
      copyButton.parentNode.replaceChild(newCopyButton, copyButton);

      newCopyButton.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        try {
          await navigator.clipboard.writeText(codeElement.textContent || '');
          if (successMessage) {
            successMessage.classList.add('visible');
            setTimeout(() => successMessage.classList.remove('visible'), 2000);
          }
        } catch (err) {
          console.error('复制代码失败:', err);
        }
      });
    }
  });
};

// 代码高亮
const highlightCode = async () => {
  await nextTick();
  if (contentRef.value) {
    contentRef.value.querySelectorAll('pre code').forEach((block) => {
      hljs.highlightElement(block);
    });
    setupCodeBlockCopyButtons();
  }
};

// 复制内容
const startUserEdit = () => {
  if (!canEditUserMessage.value) return;
  editDraft.value = String(props.message.content || '');
  isEditingUserMessage.value = true;
};

const cancelUserEdit = () => {
  editDraft.value = '';
  isEditingUserMessage.value = false;
};

const submitUserEdit = () => {
  const nextContent = editDraft.value.trim();
  if (!nextContent || nextContent === String(props.message.content || '').trim()) return;
  emit('edit-user-message', {
    message: props.message,
    content: nextContent
  });
  isEditingUserMessage.value = false;
};

watch(
  () => props.message.content,
  (content) => {
    if (!isEditingUserMessage.value) {
      editDraft.value = String(content || '');
    }
  }
);

const copyContent = async () => {
  try {
    let textToCopy = props.message.content;

    if (!isUser.value && contentRef.value) {
      const tempDiv = document.createElement('div');
      tempDiv.innerHTML = processedContent.value;
      textToCopy = tempDiv.textContent || tempDiv.innerText || '';
    }

    await navigator.clipboard.writeText(textToCopy);
    copied.value = true;
    setTimeout(() => copied.value = false, 3000);
  } catch (err) {
    console.error('复制失败:', err);
  }
};

const copyGovernanceMarkdown = async () => {
  try {
    await navigator.clipboard.writeText(governanceExportMarkdown.value);
    ElMessage.success('治理 Markdown 已复制');
  } catch (error) {
    console.error('复制治理 Markdown 失败:', error);
    ElMessage.error('复制治理 Markdown 失败');
  }
};

const copyGovernanceJson = async () => {
  try {
    await navigator.clipboard.writeText(governanceExportJson.value);
    ElMessage.success('治理 JSON 已复制');
  } catch (error) {
    console.error('复制治理 JSON 失败:', error);
    ElMessage.error('复制治理 JSON 失败');
  }
};

const copyGovernanceDocTemplate = async () => {
  try {
    await navigator.clipboard.writeText(governanceDocTemplate.value);
    ElMessage.success('治理文档模板已复制');
  } catch (error) {
    console.error('复制治理文档模板失败:', error);
    ElMessage.error('复制治理文档模板失败');
  }
};

// 监听消息内容变化
const copyGovernanceWeeklyReport = async () => {
  try {
    await navigator.clipboard.writeText(governanceWeeklyReport.value);
    ElMessage.success('治理周报已复制');
  } catch (error) {
    console.error('复制治理周报失败:', error);
    ElMessage.error('复制治理周报失败');
  }
};

const copyTechnicalUpgradeBacklog = async () => {
  try {
    await navigator.clipboard.writeText(technicalUpgradeBacklog.value);
    ElMessage.success('Technical upgrade backlog copied');
  } catch (error) {
    console.error('复制技术待办失败:', error);
    ElMessage.error('复制技术待办失败');
  }
};
const copyExecutionTimeline = async () => {
  try {
    await navigator.clipboard.writeText(executionTimelineMarkdown.value);
    ElMessage.success('Execution Timeline copied');
  } catch (error) {
    console.error('复制执行时间线失败:', error);
    ElMessage.error('复制执行时间线失败');
  }
};

const resolveGovernanceThreadId = () =>
  props.message?.thread_id ||
  props.message?.threadId ||
  props.message?.chat_id ||
  props.message?.chatId ||
  null;

const loadGovernanceHistory = async () => {
  if (governanceSaveState.value.loadingHistory) {
    return;
  }

  governanceSaveState.value.loadingHistory = true;
  try {
    const response = await chatAPI.listGovernanceSnapshots(resolveGovernanceThreadId(), 6);
    governanceSaveState.value.historyItems = Array.isArray(response?.items) ? response.items : [];
  } catch (error) {
    console.error('Failed to load governance snapshots:', error);
    ElMessage.error('Failed to load governance snapshots');
  } finally {
    governanceSaveState.value.loadingHistory = false;
  }
};

const loadGovernanceLedger = async () => {
  if (governanceSaveState.value.loadingLedger) {
    return;
  }

  governanceSaveState.value.loadingLedger = true;
  try {
    const response = await chatAPI.listGovernanceLedger(
      resolveGovernanceThreadId(),
      12,
      governanceSaveState.value.ledgerFilters
    );
    const items = Array.isArray(response?.items) ? response.items : [];
    governanceSaveState.value.ledgerItems = items;
    governanceSaveState.value.ledgerSummary = response?.summary || {
      total: 0,
      status_counts: {},
      priority_counts: {}
    };
    const nextDrafts = { ...governanceSaveState.value.ledgerDrafts };
    items.forEach((item) => {
      nextDrafts[item.record_id] = {
        status: item.status || 'open',
        priority: item.priority || 'P2',
        owner: item.owner || 'unassigned',
        due_date: item.due_date || '',
        next_action: item.next_action || '',
        verified_result: item.verified_result || '',
        tags_text: Array.isArray(item.tags) ? item.tags.join(', ') : ''
      };
    });
    governanceSaveState.value.ledgerDrafts = nextDrafts;
  } catch (error) {
    console.error('Failed to load governance ledger:', error);
    ElMessage.error('Failed to load governance ledger');
  } finally {
    governanceSaveState.value.loadingLedger = false;
  }
};

const setLedgerFilter = (field, value) => {
  governanceSaveState.value.ledgerFilters = {
    ...governanceSaveState.value.ledgerFilters,
    [field]: value
  };
};

const getLedgerDraft = (item) =>
  governanceSaveState.value.ledgerDrafts[item.record_id] || {
    status: item.status || 'open',
    priority: item.priority || 'P2',
    owner: item.owner || 'unassigned',
    due_date: item.due_date || '',
    next_action: item.next_action || '',
    verified_result: item.verified_result || '',
    tags_text: Array.isArray(item.tags) ? item.tags.join(', ') : ''
  };

const setLedgerDraftField = (recordId, field, value) => {
  const existing = governanceSaveState.value.ledgerDrafts[recordId] || {
    status: 'open',
    priority: 'P2',
    owner: 'unassigned',
    due_date: '',
    next_action: '',
    verified_result: '',
    tags_text: ''
  };
  governanceSaveState.value.ledgerDrafts = {
    ...governanceSaveState.value.ledgerDrafts,
    [recordId]: {
      ...existing,
      [field]: value
    }
  };
};

const isLedgerUpdating = (recordId) =>
  governanceSaveState.value.updatingLedgerIds.includes(recordId);

const saveGovernanceSnapshot = async () => {
  if (governanceSaveState.value.saving) {
    return;
  }

  governanceSaveState.value.saving = true;
  try {
    const response = await chatAPI.saveGovernanceSnapshot({
      markdown: governanceExportMarkdown.value,
      json_content: governanceExportPayload.value,
      doc_template: governanceDocTemplate.value,
      report_markdown: governanceWeeklyReport.value,
      backlog_markdown: technicalUpgradeBacklog.value,
      thread_id: resolveGovernanceThreadId()
    });

    governanceSaveState.value.savedPaths = [
      { label: 'Markdown', path: response?.markdown_path },
      { label: 'JSON', path: response?.json_path },
      { label: 'Doc Template', path: response?.doc_template_path },
      { label: 'Weekly Report', path: response?.report_path }
      ,
      { label: 'Tech Backlog', path: response?.backlog_path }
    ].filter((item) => item.path);

    ElMessage.success('治理快照已保存到 reports/governance');
    await loadGovernanceHistory();
  } catch (error) {
    console.error('保存治理快照失败:', error);
    ElMessage.error('保存治理快照失败');
  } finally {
    governanceSaveState.value.saving = false;
  }
};

const createGovernanceLedgerRecord = async () => {
  if (governanceSaveState.value.creatingLedger) {
    return;
  }

  governanceSaveState.value.creatingLedger = true;
  try {
    const sourceSnapshotPaths = governanceSaveState.value.savedPaths.reduce((acc, item) => {
      if (item?.label && item?.path) {
        acc[item.label.toLowerCase().replace(/\s+/g, "_")] = item.path;
      }
      return acc;
    }, {});

    const response = await chatAPI.createGovernanceLedger({
      thread_id: resolveGovernanceThreadId(),
      summary: governanceLedger.value.summary,
      risks: badCaseReplay.value.risks,
      items: governanceLedger.value.items,
      timeline: diagnosticTimeline.value,
      priority: 'P1',
      tags: badCaseGovernance.value.items.map((item) => item.layer).filter(Boolean),
      source_snapshot_paths: sourceSnapshotPaths
    });

    ElMessage.success('治理台账记录已创建');
    await loadGovernanceLedger();
    if (response?.detail_path) {
      governanceSaveState.value.ledgerItems = [
        {
          record_id: response.record_id,
          thread_id: response.thread_id,
          thread_hint: response.thread_hint,
          created_at: response.created_at,
          risk_count: response.risk_count,
          item_count: response.item_count,
          priority_summary: response.priority_summary,
          detail_path: response.detail_path,
          status: response.status,
          owner: response.owner,
          next_action: response.next_action,
          verified_result: response.verified_result,
          due_date: response.due_date,
          priority: response.priority,
          tags: response.tags
        },
        ...governanceSaveState.value.ledgerItems.filter((item) => item.record_id !== response.record_id)
      ].slice(0, 6);
      governanceSaveState.value.ledgerDrafts = {
        ...governanceSaveState.value.ledgerDrafts,
        [response.record_id]: {
          status: response.status || 'open',
          priority: response.priority || 'P2',
          owner: response.owner || 'unassigned',
          due_date: response.due_date || '',
          next_action: response.next_action || '',
          verified_result: response.verified_result || '',
          tags_text: Array.isArray(response.tags) ? response.tags.join(', ') : ''
        }
      };
    }
  } catch (error) {
    console.error('创建治理台账记录失败:', error);
    ElMessage.error('创建治理台账记录失败');
  } finally {
    governanceSaveState.value.creatingLedger = false;
  }
};

const saveLedgerRecord = async (recordId) => {
  if (!recordId || isLedgerUpdating(recordId)) {
    return;
  }

  const draft = governanceSaveState.value.ledgerDrafts[recordId];
  if (!draft) {
    return;
  }

  governanceSaveState.value.updatingLedgerIds = [
    ...governanceSaveState.value.updatingLedgerIds,
    recordId
  ];
  try {
    const tags = String(draft.tags_text || '')
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean);
    const response = await chatAPI.updateGovernanceLedger({
      record_id: recordId,
      status: draft.status,
      priority: draft.priority,
      owner: draft.owner,
      due_date: draft.due_date,
      next_action: draft.next_action,
      verified_result: draft.verified_result,
      tags
    });

    governanceSaveState.value.ledgerItems = governanceSaveState.value.ledgerItems.map((item) =>
      item.record_id === recordId
        ? {
            ...item,
            status: response.status,
            priority: response.priority,
            owner: response.owner,
            due_date: response.due_date,
            next_action: response.next_action,
            verified_result: response.verified_result,
            tags: response.tags
          }
        : item
    );
    governanceSaveState.value.ledgerDrafts = {
      ...governanceSaveState.value.ledgerDrafts,
      [recordId]: {
        status: response.status || 'open',
        priority: response.priority || 'P2',
        owner: response.owner || 'unassigned',
        due_date: response.due_date || '',
        next_action: response.next_action || '',
        verified_result: response.verified_result || '',
        tags_text: Array.isArray(response.tags) ? response.tags.join(', ') : ''
      }
    };
    await loadGovernanceLedger();
    ElMessage.success('治理台账记录已更新');
  } catch (error) {
    console.error('更新治理台账记录失败:', error);
    ElMessage.error('更新治理台账记录失败');
  } finally {
    governanceSaveState.value.updatingLedgerIds =
      governanceSaveState.value.updatingLedgerIds.filter((id) => id !== recordId);
  }
};

watch(
    () => props.message.content,
    () => {
      if (!isUser.value) {
        highlightCode();
      }
    }
);

// 监听图表数据变化
watch(
    () => props.message.chartData,
    () => {
      if (!isUser.value) {
        handleTemperatureData(props.message.chartData)
      }
    },
    { deep: true } // 深度监听对象内部变化
);

watch(
  () => resolveGovernanceThreadId(),
  () => {
    governanceSaveState.value.historyItems = [];
    governanceSaveState.value.ledgerItems = [];
    governanceSaveState.value.ledgerDrafts = {};
    governanceSaveState.value.ledgerSummary = {
      total: 0,
      status_counts: {},
      priority_counts: {}
    };
    if (!isUser.value && governanceLedger.value.items.length) {
      loadGovernanceHistory();
      loadGovernanceLedger();
    }
  },
  { immediate: true }
);

watch(
  () => ({ ...governanceSaveState.value.ledgerFilters }),
  () => {
    if (!isUser.value && governanceLedger.value.items.length) {
      loadGovernanceLedger();
    }
  },
  { deep: true }
);

// 生命周期钩子
onMounted(() => {
  if (!isUser.value) {
    highlightCode();
    handleTemperatureData(props.message.chartData)
  }
  if (contentRef.value) {
    contentRef.value.addEventListener('click', onContentClick);
  }
});

// 在组件卸载时销毁图表实例
watch(() => showChart.value, (newVal) => {
  if (!newVal && chartInstance.value) {
    chartInstance.value.destroy()
    chartInstance.value = null
  }
})

// 在setup中增加方法
const openImageInNewTab = (url) => {
  window.open(url, '_blank');
};

const showImage = (url) => {
  imagePreview.value.url = url;
  imagePreview.value.visible = true;
  imagePreview.value.scale = 1;
  imagePreview.value.tx = 0;
  imagePreview.value.ty = 0;
  document.body.style.overflow = 'hidden';
};

const closeImage = () => {
  imagePreview.value.visible = false;
  imagePreview.value.url = '';
  imagePreview.value.scale = 1;
  imagePreview.value.tx = 0;
  imagePreview.value.ty = 0;
  document.body.style.overflow = '';
};

// 代理 markdown 内图片点击，使用遮罩预览
const onContentClick = (e) => {
  const target = e.target;
  if (target && target.tagName === 'IMG' && target.dataset && target.dataset.preview === '1') {
    e.preventDefault();
    e.stopPropagation();
    showImage(target.getAttribute('src'));
  }
};

onUnmounted(() => {
  if (contentRef.value) {
    contentRef.value.removeEventListener('click', onContentClick);
  }
});

// 预览层：滚轮缩放
const onWheel = (e) => {
  const delta = e.deltaY > 0 ? -0.1 : 0.1;
  const next = Math.min(5, Math.max(0.5, imagePreview.value.scale + delta));
  imagePreview.value.scale = Number(next.toFixed(2));
};

// 预览层：拖拽平移
const onDragStart = (e) => {
  imagePreview.value.dragging = true;
  imagePreview.value.lastX = e.clientX;
  imagePreview.value.lastY = e.clientY;
  e.preventDefault();
};
const onDragMove = (e) => {
  if (!imagePreview.value.dragging) return;
  const dx = e.clientX - imagePreview.value.lastX;
  const dy = e.clientY - imagePreview.value.lastY;
  imagePreview.value.tx += dx;
  imagePreview.value.ty += dy;
  imagePreview.value.lastX = e.clientX;
  imagePreview.value.lastY = e.clientY;
};
const onDragEnd = () => {
  imagePreview.value.dragging = false;
};
const resetPreviewTransform = () => {
  imagePreview.value.scale = 1;
  imagePreview.value.tx = 0;
  imagePreview.value.ty = 0;
};

// 报告查看逻辑
const drawerVisible = ref(false);
const reportUrl = ref('');
const drawerLoading = ref(false);
const drawerSize = ref('80%');
const selectedReport = ref('');
const isMarkdownReport = ref(false);
const markdownContent = ref('');
const normalizeReportUrl = (url) => {
  if (!url) return url;
  // 若配置了 BACKEND_BASE，则为 /reports/** 补全绝对地址，避免跨源加载失败
  if (BACKEND_BASE && url.startsWith('/reports/')) {
    const base = BACKEND_BASE.replace(/\/+$/, '');
    return `${base}${url}`;
  }
  return url;
};

// 监听reportUrl变化，当为空时自动关闭抽屉
watch(reportUrl, (newVal) => {
  if (!newVal && drawerVisible.value) {
    drawerVisible.value = false;
  }
});

// 监听drawerVisible变化，当打开但reportUrl为空时自动关闭
watch(drawerVisible, (newVal) => {
  if (newVal && !reportUrl.value) {
    drawerVisible.value = false;
  }
});
const openReport = (url) => {
  if (!isSafeReportUrl(url)) {
    ElMessage.warning('报告链接不合法，无法打开');
    return;
  }
  reportUrl.value = normalizeReportUrl(url);
  isMarkdownReport.value = url.endsWith('.md');
  drawerLoading.value = true;
  drawerVisible.value = true;
  
  if (isMarkdownReport.value) {
    fetchMarkdownContent(url);
  } else {
    window.setTimeout(() => { drawerLoading.value = false; }, 1500);
  }
};

const fetchMarkdownContent = async (url) => {
  try {
    const response = await fetch(normalizeReportUrl(url));
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }
    const text = await response.text();
    markdownContent.value = processContent(text);
    drawerLoading.value = false;
  } catch (error) {
    console.error('Failed to fetch markdown content:', error);
    ElMessage.error('报告加载失败');
    drawerLoading.value = false;
  }
};
const openReportInNewTab = (url) => {
  if (!url) return;
  if (!isSafeReportUrl(url)) {
    ElMessage.warning('报告链接不合法，无法打开');
    return;
  }
  window.open(normalizeReportUrl(url), '_blank');
};
const reloadReport = () => {
  if (!reportUrl.value) return;
  drawerLoading.value = true;
  const current = reportUrl.value;
  // 通过追加时间戳强制刷新
  const sep = current.includes('?') ? '&' : '?';
  reportUrl.value = current + sep + 't=' + Date.now();
  // 还原为干净地址，避免累积参数
  setTimeout(() => { reportUrl.value = current; }, 0);
  // 兜底超时
  window.setTimeout(() => { drawerLoading.value = false; }, 1500);
};
const onIframeLoad = () => {
  drawerLoading.value = false;
};
const onIframeError = () => {
  drawerLoading.value = false;
  ElMessage.error('报告加载失败');
};
const toggleDrawerSize = () => {
  drawerSize.value = drawerSize.value === '420px' ? '80%' : '420px';
};

</script>
