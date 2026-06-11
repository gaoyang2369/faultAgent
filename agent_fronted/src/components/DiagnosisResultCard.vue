<template>
  <section v-if="hasCard" class="diagnosis-card" :class="`diagnosis-card--${statusTone}`">
    <header class="diagnosis-card__header">
      <div class="diagnosis-card__status">
        <span class="diagnosis-card__status-dot"></span>
        <span>{{ statusLabel }}</span>
      </div>
      <div class="diagnosis-card__title-block">
        <h3 class="diagnosis-card__title">{{ deviceLabel }}</h3>
        <div class="diagnosis-card__meta">
          <span v-if="faultCodeLabel">{{ faultCodeLabel }}</span>
          <span v-if="confidenceLabel">置信度 {{ confidenceLabel }}</span>
          <span v-if="latestTimeLabel">最新时间 {{ latestTimeLabel }}</span>
        </div>
      </div>
    </header>

    <p v-if="conclusion" class="diagnosis-card__conclusion">{{ conclusion }}</p>

    <div v-if="metricCards.length" class="diagnosis-card__metrics">
      <div
        v-for="metric in metricCards"
        :key="metric.label"
        class="diagnosis-card__metric"
        :class="`diagnosis-card__metric--${metric.tone}`"
      >
        <div class="diagnosis-card__metric-label">{{ metric.label }}</div>
        <div class="diagnosis-card__metric-value">{{ metric.value }}</div>
        <div v-if="metric.meta" class="diagnosis-card__metric-meta">{{ metric.meta }}</div>
      </div>
    </div>

    <div v-if="actionItems.length" class="diagnosis-card__actions">
      <div class="diagnosis-card__section-title">建议处置</div>
      <ol class="diagnosis-card__action-list">
        <li v-for="item in actionItems" :key="item.key" class="diagnosis-card__action">
          <span class="diagnosis-card__action-label">{{ item.label }}</span>
          <span class="diagnosis-card__action-text">{{ item.text }}</span>
        </li>
      </ol>
    </div>

    <div v-if="workOrderVisible" class="diagnosis-card__workorder">
      <div class="diagnosis-card__section-title">工单建议</div>
      <div class="diagnosis-card__workorder-box" :class="{ 'is-muted': !workOrderNeed }">
        <div class="diagnosis-card__workorder-head">
          <div>
            <div class="diagnosis-card__workorder-status">
              系统判断：{{ workOrderNeed ? '建议生成维修工单' : '暂不建议自动建单' }}
            </div>
            <div v-if="workOrderReason" class="diagnosis-card__workorder-reason">{{ workOrderReason }}</div>
          </div>
          <button
            v-if="workOrderNeed"
            type="button"
            class="diagnosis-card__workorder-button"
            :disabled="isCreatingWorkOrder || Boolean(createdWorkOrder)"
            @click="createWorkOrder"
          >
            <WrenchScrewdriverIcon class="diagnosis-card__workorder-icon" />
            <span>{{ createdWorkOrder ? '已生成' : isCreatingWorkOrder ? '生成中' : '生成工单' }}</span>
          </button>
        </div>
        <div class="diagnosis-card__workorder-grid">
          <div>
            <span>工单类型</span>
            <strong>{{ workOrderType }}</strong>
          </div>
          <div>
            <span>优先级</span>
            <strong>{{ workOrderPriority }}</strong>
          </div>
          <div>
            <span>建议负责人</span>
            <strong>{{ workOrderAssignee }}</strong>
          </div>
          <div>
            <span>建议完成时间</span>
            <strong>{{ workOrderWindow }}</strong>
          </div>
        </div>
        <div v-if="workOrderEvidenceItems.length" class="diagnosis-card__workorder-evidence">
          <span>关键证据</span>
          <p>{{ workOrderEvidenceItems.join('；') }}</p>
        </div>
        <div v-if="createdWorkOrder" class="diagnosis-card__workorder-result">
          <div class="diagnosis-card__workorder-created">
            工单 {{ createdWorkOrder.work_order_id || createdWorkOrder.workOrderId }} 已生成
          </div>
          <div class="diagnosis-card__workorder-result-grid">
            <span>标题：{{ createdWorkOrder.title || workOrderTitle }}</span>
            <span>状态：{{ createdWorkOrder.status || '待派单' }}</span>
            <span>优先级：{{ createdWorkOrder.priority || workOrderPriority }}</span>
            <span>关联诊断记录：{{ traceIdLabel }}</span>
          </div>
        </div>
        <div v-if="workOrderError" class="diagnosis-card__workorder-error">{{ workOrderError }}</div>
      </div>
    </div>

    <div class="diagnosis-card__details">
      <details v-if="basisItems.length" class="diagnosis-card__details-group">
        <summary>
          <span>已确认事实</span>
          <span>{{ basisItems.length }} 项</span>
        </summary>
        <ul>
          <li v-for="item in basisItems" :key="item">{{ item }}</li>
        </ul>
      </details>

      <details v-if="causeItems.length" class="diagnosis-card__details-group">
        <summary>
          <span>可能原因</span>
          <span>{{ causeItems.length }} 项</span>
        </summary>
        <ul>
          <li v-for="item in causeItems" :key="item">{{ item }}</li>
        </ul>
      </details>

      <details v-if="verificationItems.length" class="diagnosis-card__details-group">
        <summary>
          <span>待验证信息</span>
          <span>{{ verificationItems.length }} 项</span>
        </summary>
        <ul>
          <li v-for="item in verificationItems" :key="item">{{ item }}</li>
        </ul>
      </details>

      <details v-if="knowledgeSourceItems.length" class="diagnosis-card__details-group">
        <summary>
          <span>RAG 来源</span>
          <span>{{ knowledgeSourceItems.length }} 条</span>
        </summary>
        <div class="diagnosis-card__source-list">
          <div v-for="item in knowledgeSourceItems" :key="item" class="diagnosis-card__source">
            {{ item }}
          </div>
        </div>
      </details>
    </div>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import { WrenchScrewdriverIcon } from '@heroicons/vue/24/outline'
import { chatAPI } from '@/services/api.js'

const props = defineProps({
  message: {
    type: Object,
    required: true
  }
})

const pickObject = (...candidates) => {
  for (const candidate of candidates) {
    if (candidate && typeof candidate === 'object' && !Array.isArray(candidate)) {
      return candidate
    }
  }
  return null
}

const toList = (value) => {
  if (!Array.isArray(value)) return []
  return [...new Set(value.map((item) => String(item || '').trim()).filter(Boolean))]
}

const cleanText = (value) => String(value || '').replace(/\s+/g, ' ').trim()

const analysisArtifact = computed(() => pickObject(
  props.message?.analysisArtifact,
  props.message?.analysis_artifact,
  props.message?.artifact?.payload?.analysis_artifact,
  props.message?.workflowResult?.payload?.analysis_artifact,
  props.message?.workflow_result?.payload?.analysis_artifact,
  props.message?.scenarioResult?.payload?.analysis_artifact,
  props.message?.scenario_result?.payload?.analysis_artifact
))

const sqlArtifact = computed(() => pickObject(
  props.message?.sqlArtifact,
  props.message?.sql_artifact,
  props.message?.artifact?.payload?.sql_artifact,
  props.message?.workflowResult?.payload?.sql_artifact,
  props.message?.workflow_result?.payload?.sql_artifact,
  props.message?.scenarioResult?.payload?.sql_artifact,
  props.message?.scenario_result?.payload?.sql_artifact
))

const knowledgeArtifact = computed(() => pickObject(
  props.message?.knowledgeArtifact,
  props.message?.knowledge_artifact,
  props.message?.artifact?.payload?.knowledge_artifact,
  props.message?.workflowResult?.payload?.knowledge_artifact,
  props.message?.workflow_result?.payload?.knowledge_artifact,
  props.message?.scenarioResult?.payload?.knowledge_artifact,
  props.message?.scenario_result?.payload?.knowledge_artifact
))

const workOrderDecision = computed(() => pickObject(
  props.message?.workorderDecision,
  props.message?.workorder_decision,
  props.message?.artifact?.payload?.workorder_decision,
  props.message?.workflowResult?.payload?.workorder_decision,
  props.message?.workflow_result?.payload?.workorder_decision,
  props.message?.scenarioResult?.payload?.workorder_decision,
  props.message?.scenario_result?.payload?.workorder_decision
))

const basisItems = computed(() => toList(analysisArtifact.value?.basis))
const causeItems = computed(() => toList(analysisArtifact.value?.probable_causes))
const verificationItems = computed(() => toList([
  ...toList(analysisArtifact.value?.verification_items),
  ...toList(analysisArtifact.value?.missing_information)
]))
const recommendations = computed(() => toList(analysisArtifact.value?.recommendations))
const conclusion = computed(() => cleanText(analysisArtifact.value?.conclusion || props.message?.content))
const createdWorkOrder = ref(null)
const isCreatingWorkOrder = ref(false)
const workOrderError = ref('')

const evidenceText = computed(() => [
  conclusion.value,
  ...basisItems.value,
  ...causeItems.value,
  cleanText(sqlArtifact.value?.result_preview),
  cleanText(sqlArtifact.value?.raw_output),
  cleanText(knowledgeArtifact.value?.raw_output)
].filter(Boolean).join(' '))

const hasCard = computed(() => Boolean(
  analysisArtifact.value &&
  (
    conclusion.value ||
    basisItems.value.length ||
    recommendations.value.length ||
    causeItems.value.length
  )
))

const faultCodes = computed(() => [...new Set((evidenceText.value.match(/\b[A-Z]\d{5}\b/g) || []))])
const faultCodeLabel = computed(() => faultCodes.value.length ? faultCodes.value.join(' / ') : '')
const confidenceLabel = computed(() => {
  const value = String(analysisArtifact.value?.confidence || '').trim()
  if (value === 'high') return 'high'
  if (value === 'medium') return 'medium'
  if (value === 'low') return 'low'
  return value
})
const deviceLabel = computed(() => {
  const matched = evidenceText.value.match(/[A-Za-z]*\d+电机\d+/)
  return matched?.[0] || 'DCMA 系统'
})
const latestTimeLabel = computed(() => {
  const matched = evidenceText.value.match(/\b20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\b/)
  return matched?.[0] || ''
})
const statusTone = computed(() => {
  if (faultCodes.value.length || /故障|异常|报警/.test(conclusion.value)) return 'danger'
  if (/关注|偏差|偏高|待验证/.test(conclusion.value)) return 'warning'
  return 'good'
})
const statusLabel = computed(() => {
  if (statusTone.value === 'danger') return '异常'
  if (statusTone.value === 'warning') return '关注'
  return '正常'
})

const firstMatch = (patterns) => {
  for (const pattern of patterns) {
    const matched = evidenceText.value.match(pattern)
    if (matched?.[1]) return matched[1]
  }
  return ''
}

const percentTone = (value, warning, danger) => {
  const numeric = Number.parseFloat(String(value || '').replace('%', ''))
  if (Number.isNaN(numeric)) return 'neutral'
  if (numeric >= danger) return 'danger'
  if (numeric >= warning) return 'warning'
  return 'good'
}

const metricCards = computed(() => {
  const cards = []
  const streak = firstMatch([/连续\s*(\d+)\s*条/, /最新连续异常\s*(\d+)\s*条/])
  if (faultCodeLabel.value) {
    cards.push({
      label: '故障码',
      value: faultCodeLabel.value,
      meta: streak ? `连续 ${streak} 条` : 'SQL 样本命中',
      tone: 'danger'
    })
  }

  const speedDeviation = firstMatch([/速度[^。；;]*?偏差(?:达|为)?\s*([0-9.]+%)/, /偏差(?:达|为)?\s*([0-9.]+%)/])
  if (speedDeviation) {
    cards.push({
      label: '速度偏差',
      value: speedDeviation,
      meta: '给定/反馈偏离',
      tone: percentTone(speedDeviation, 20, 40)
    })
  }

  const loadRate = firstMatch([/负载率最高\s*([0-9.]+%)/, /最高负载率\s*([0-9.]+%)/])
  if (loadRate) {
    cards.push({
      label: '负载率',
      value: loadRate,
      meta: '关注负载裕量',
      tone: percentTone(loadRate, 75, 90)
    })
  }

  const temperatureText = basisItems.value.find((item) => /温度/.test(item)) || ''
  if (temperatureText) {
    cards.push({
      label: '温度',
      value: /异常|偏高|关注/.test(temperatureText) ? '关注' : '正常',
      meta: temperatureText.replace(/^.*?(电机温度|温度)/, '$1').slice(0, 32),
      tone: /异常|偏高/.test(temperatureText) ? 'warning' : 'good'
    })
  }

  const voltageText = basisItems.value.find((item) => /母线电压|供电/.test(item)) || ''
  if (voltageText) {
    cards.push({
      label: '母线电压',
      value: /稳定|未见/.test(voltageText) ? '稳定' : '关注',
      meta: voltageText.slice(0, 36),
      tone: /稳定|未见/.test(voltageText) ? 'good' : 'warning'
    })
  }

  return cards.slice(0, 5)
})

const actionItems = computed(() => recommendations.value.slice(0, 5).map((item, index) => {
  const matched = item.match(/^([^：:]{2,10})[：:]\s*(.+)$/)
  return {
    key: `${index}-${item.slice(0, 12)}`,
    label: matched?.[1] || `步骤 ${index + 1}`,
    text: matched?.[2] || item
  }
}))

const workOrderVisible = computed(() => Boolean(workOrderDecision.value))
const workOrderNeed = computed(() => Boolean(workOrderDecision.value?.need_workorder ?? workOrderDecision.value?.needWorkorder))
const workOrderReason = computed(() => cleanText(workOrderDecision.value?.reason))
const workOrderType = computed(() => cleanText(workOrderDecision.value?.workorder_type || workOrderDecision.value?.workorderType || '运行异常排查'))
const workOrderPriority = computed(() => {
  const priority = cleanText(workOrderDecision.value?.priority || 'P2')
  const label = cleanText(workOrderDecision.value?.priority_label || workOrderDecision.value?.priorityLabel)
  return label ? `${priority} ${label}` : priority
})
const workOrderAssignee = computed(() => cleanText(workOrderDecision.value?.assignee_role || workOrderDecision.value?.assigneeRole || '电气维护人员'))
const workOrderWindow = computed(() => cleanText(workOrderDecision.value?.suggested_completion_window || workOrderDecision.value?.suggestedCompletionWindow || '24小时内'))
const workOrderTitle = computed(() => cleanText(workOrderDecision.value?.title || `${deviceLabel.value} ${faultCodeLabel.value || '运行异常'} 排查`))
const workOrderEquipment = computed(() => cleanText(workOrderDecision.value?.equipment_object || workOrderDecision.value?.equipmentObject || deviceLabel.value || 'DCMA 系统'))
const workOrderFaultCode = computed(() => cleanText(workOrderDecision.value?.fault_code || workOrderDecision.value?.faultCode || faultCodes.value[0] || ''))
const workOrderEvidenceItems = computed(() => toList(workOrderDecision.value?.key_evidence || workOrderDecision.value?.keyEvidence).slice(0, 5))
const workOrderStepItems = computed(() => toList(workOrderDecision.value?.processing_steps || workOrderDecision.value?.processingSteps).slice(0, 6))
const workOrderAcceptanceItems = computed(() => toList(workOrderDecision.value?.acceptance_criteria || workOrderDecision.value?.acceptanceCriteria).slice(0, 5))
const traceId = computed(() => cleanText(
  props.message?.traceId ||
  props.message?.trace_id ||
  props.message?.trace?.trace_id ||
  props.message?.artifact?.payload?.trace?.trace_id ||
  props.message?.workflowResult?.payload?.trace?.trace_id ||
  props.message?.workflow_result?.payload?.trace?.trace_id
))
const traceIdLabel = computed(() => traceId.value || '未绑定')
const requestId = computed(() => cleanText(
  props.message?.requestId ||
  props.message?.request_id ||
  props.message?.trace?.request_id ||
  props.message?.artifact?.payload?.trace?.request_id ||
  props.message?.workflowResult?.payload?.trace?.request_id ||
  props.message?.workflow_result?.payload?.trace?.request_id
))
const threadId = computed(() => cleanText(
  props.message?.threadId ||
  props.message?.thread_id ||
  props.message?.artifact?.thread_id ||
  props.message?.artifact?.payload?.trace?.thread_id ||
  props.message?.workflowResult?.metadata?.thread_id ||
  props.message?.workflow_result?.metadata?.thread_id ||
  props.message?.scenarioResult?.metadata?.thread_id ||
  props.message?.scenario_result?.metadata?.thread_id
))

const createWorkOrderPayload = () => ({
  title: workOrderTitle.value,
  equipment_object: workOrderEquipment.value,
  fault_code: workOrderFaultCode.value || null,
  workorder_type: workOrderType.value,
  priority: cleanText(workOrderDecision.value?.priority || 'P2'),
  risk_level: cleanText(workOrderDecision.value?.risk_level || workOrderDecision.value?.riskLevel || '低'),
  trigger_source: cleanText(workOrderDecision.value?.trigger_source || workOrderDecision.value?.triggerSource || '故障诊断 Agent'),
  diagnosis_conclusion: cleanText(workOrderDecision.value?.diagnosis_conclusion || workOrderDecision.value?.diagnosisConclusion || conclusion.value),
  key_evidence: workOrderEvidenceItems.value,
  processing_steps: workOrderStepItems.value,
  acceptance_criteria: workOrderAcceptanceItems.value,
  assignee_role: workOrderAssignee.value,
  suggested_completion_window: workOrderWindow.value,
  status: cleanText(workOrderDecision.value?.status || '待派单'),
  thread_id: threadId.value,
  trace_id: traceId.value,
  request_id: requestId.value || null,
  source: {
    trace_id: traceId.value,
    request_id: requestId.value || null,
    thread_id: threadId.value,
    report_url: props.message?.reportUrl || props.message?.report_url || null,
    analysis_artifact: analysisArtifact.value,
    sql_summary: sqlArtifact.value?.summary || ''
  }
})

const createWorkOrder = async () => {
  if (createdWorkOrder.value || isCreatingWorkOrder.value) return
  workOrderError.value = ''
  if (!threadId.value || !traceId.value) {
    workOrderError.value = '缺少 trace_id 或 thread_id，无法绑定诊断依据。'
    return
  }
  isCreatingWorkOrder.value = true
  try {
    const response = await chatAPI.createWorkOrder(createWorkOrderPayload())
    createdWorkOrder.value = response?.work_order || response?.workOrder || null
    if (!createdWorkOrder.value) {
      workOrderError.value = '工单已提交，但未返回工单详情。'
    }
  } catch (error) {
    workOrderError.value = error?.message || '工单生成失败，请稍后重试。'
  } finally {
    isCreatingWorkOrder.value = false
  }
}

const knowledgeSourceItems = computed(() => {
  const rawText = String(knowledgeArtifact.value?.raw_output || '')
  const raw = cleanText(rawText)
  if (!rawText.trim()) return []
  const sourceFile = firstMatchFrom(rawText, [/来源文件[:：]\s*([^\n\r]+)/, /文件[:：]\s*([^\n\r]+)/])
  const page = firstMatchFrom(rawText, [/来源页码[:：]\s*([^\n\r]+)/, /页码[:：]\s*([^\n\r]+)/])
  const method = firstMatchFrom(rawText, [/检索方式[:：]\s*([^\n\r]+)/])
  const code = faultCodeLabel.value
  const primary = [sourceFile, page ? `页 ${page}` : '', method].filter(Boolean).join(' · ')
  const items = []
  if (primary) items.push(code ? `${code} · ${primary}` : primary)
  const reason = firstMatchFrom(raw, [/原因[:：]\s*([^。；;]+)/])
  const action = firstMatchFrom(raw, [/处理[:：]\s*([^。；;]+)/])
  if (reason) items.push(`原因：${reason}`)
  if (action) items.push(`处理：${action}`)
  return items
})

function firstMatchFrom(text, patterns) {
  for (const pattern of patterns) {
    const matched = text.match(pattern)
    if (matched?.[1]) return matched[1].trim()
  }
  return ''
}
</script>

<style scoped>
.diagnosis-card {
  margin-top: 0.95rem;
  border: 1px solid #d7dee8;
  border-left-width: 4px;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 8px 22px rgba(15, 23, 42, 0.06);
  overflow: hidden;
}

.diagnosis-card--danger {
  border-left-color: #dc2626;
}

.diagnosis-card--warning {
  border-left-color: #d97706;
}

.diagnosis-card--good {
  border-left-color: #16a34a;
}

.diagnosis-card__header {
  display: flex;
  gap: 0.75rem;
  align-items: flex-start;
  padding: 0.95rem 1rem 0.7rem;
  border-bottom: 1px solid #e5eaf0;
}

.diagnosis-card__status {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  flex-shrink: 0;
  min-height: 1.5rem;
  padding: 0.18rem 0.5rem;
  border-radius: 999px;
  background: #f1f5f9;
  color: #334155;
  font-size: 0.75rem;
  font-weight: 700;
}

.diagnosis-card__status-dot {
  width: 0.45rem;
  height: 0.45rem;
  border-radius: 999px;
  background: #64748b;
}

.diagnosis-card--danger .diagnosis-card__status {
  background: #fef2f2;
  color: #991b1b;
}

.diagnosis-card--danger .diagnosis-card__status-dot {
  background: #dc2626;
}

.diagnosis-card--warning .diagnosis-card__status {
  background: #fffbeb;
  color: #92400e;
}

.diagnosis-card--warning .diagnosis-card__status-dot {
  background: #d97706;
}

.diagnosis-card--good .diagnosis-card__status {
  background: #f0fdf4;
  color: #166534;
}

.diagnosis-card--good .diagnosis-card__status-dot {
  background: #16a34a;
}

.diagnosis-card__title-block {
  min-width: 0;
}

.diagnosis-card__title {
  margin: 0;
  color: #0f172a;
  font-size: 1rem;
  line-height: 1.35;
}

.diagnosis-card__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem 0.7rem;
  margin-top: 0.24rem;
  color: #64748b;
  font-size: 0.76rem;
}

.diagnosis-card__conclusion {
  margin: 0;
  padding: 0.8rem 1rem 0;
  color: #1f2937;
  font-size: 0.86rem;
  line-height: 1.6;
}

.diagnosis-card__metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(112px, 1fr));
  gap: 0.6rem;
  padding: 0.9rem 1rem 0;
}

.diagnosis-card__metric {
  min-height: 5.7rem;
  padding: 0.68rem 0.72rem;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
}

.diagnosis-card__metric--danger {
  border-color: #fecaca;
  background: #fef2f2;
}

.diagnosis-card__metric--warning {
  border-color: #fde68a;
  background: #fffbeb;
}

.diagnosis-card__metric--good {
  border-color: #bbf7d0;
  background: #f0fdf4;
}

.diagnosis-card__metric-label {
  color: #64748b;
  font-size: 0.72rem;
}

.diagnosis-card__metric-value {
  margin-top: 0.16rem;
  color: #0f172a;
  font-size: 0.98rem;
  font-weight: 800;
  line-height: 1.25;
  overflow-wrap: anywhere;
}

.diagnosis-card__metric-meta {
  margin-top: 0.26rem;
  color: #64748b;
  font-size: 0.72rem;
  line-height: 1.35;
}

.diagnosis-card__actions {
  padding: 0.95rem 1rem 0;
}

.diagnosis-card__section-title {
  margin-bottom: 0.5rem;
  color: #334155;
  font-size: 0.8rem;
  font-weight: 800;
}

.diagnosis-card__action-list {
  display: grid;
  gap: 0.48rem;
  margin: 0;
  padding: 0;
  list-style: none;
}

.diagnosis-card__action {
  display: grid;
  grid-template-columns: 5.2rem minmax(0, 1fr);
  gap: 0.62rem;
  align-items: flex-start;
  padding: 0.62rem 0.7rem;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #fff;
}

.diagnosis-card__action-label {
  color: #165dff;
  font-size: 0.75rem;
  font-weight: 800;
}

.diagnosis-card__action-text {
  color: #111827;
  font-size: 0.8rem;
  line-height: 1.5;
}

.diagnosis-card__workorder {
  padding: 0.95rem 1rem 0;
}

.diagnosis-card__workorder-box {
  padding: 0.75rem;
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  background: #eff6ff;
}

.diagnosis-card__workorder-box.is-muted {
  border-color: #e2e8f0;
  background: #f8fafc;
}

.diagnosis-card__workorder-head {
  display: flex;
  gap: 0.75rem;
  align-items: flex-start;
  justify-content: space-between;
}

.diagnosis-card__workorder-status {
  color: #0f172a;
  font-size: 0.84rem;
  font-weight: 800;
  line-height: 1.45;
}

.diagnosis-card__workorder-reason {
  margin-top: 0.22rem;
  color: #475569;
  font-size: 0.76rem;
  line-height: 1.5;
}

.diagnosis-card__workorder-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.32rem;
  flex-shrink: 0;
  min-height: 2rem;
  padding: 0.38rem 0.62rem;
  border: 0;
  border-radius: 8px;
  background: #1d4ed8;
  color: #fff;
  font-size: 0.76rem;
  font-weight: 800;
  cursor: pointer;
}

.diagnosis-card__workorder-button:disabled {
  background: #94a3b8;
  cursor: not-allowed;
}

.diagnosis-card__workorder-icon {
  width: 0.92rem;
  height: 0.92rem;
}

.diagnosis-card__workorder-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 0.5rem;
  margin-top: 0.68rem;
}

.diagnosis-card__workorder-grid > div {
  min-height: 3.3rem;
  padding: 0.5rem 0.56rem;
  border: 1px solid rgba(148, 163, 184, 0.38);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.78);
}

.diagnosis-card__workorder-grid span,
.diagnosis-card__workorder-evidence span {
  display: block;
  color: #64748b;
  font-size: 0.7rem;
}

.diagnosis-card__workorder-grid strong {
  display: block;
  margin-top: 0.18rem;
  color: #0f172a;
  font-size: 0.82rem;
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.diagnosis-card__workorder-evidence {
  margin-top: 0.6rem;
  padding: 0.55rem 0.6rem;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.72);
}

.diagnosis-card__workorder-evidence p {
  margin: 0.18rem 0 0;
  color: #1e293b;
  font-size: 0.78rem;
  line-height: 1.5;
}

.diagnosis-card__workorder-result {
  margin-top: 0.68rem;
  padding: 0.62rem 0.68rem;
  border: 1px solid #bbf7d0;
  border-radius: 8px;
  background: #f0fdf4;
}

.diagnosis-card__workorder-created {
  color: #166534;
  font-size: 0.82rem;
  font-weight: 800;
}

.diagnosis-card__workorder-result-grid {
  display: grid;
  gap: 0.25rem;
  margin-top: 0.4rem;
  color: #14532d;
  font-size: 0.76rem;
  line-height: 1.45;
}

.diagnosis-card__workorder-error {
  margin-top: 0.58rem;
  color: #b91c1c;
  font-size: 0.76rem;
  line-height: 1.45;
}

.diagnosis-card__details {
  display: grid;
  gap: 0.5rem;
  padding: 0.95rem 1rem 1rem;
}

.diagnosis-card__details-group {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #f8fafc;
  overflow: hidden;
}

.diagnosis-card__details-group summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.62rem 0.72rem;
  color: #334155;
  font-size: 0.78rem;
  font-weight: 800;
  cursor: pointer;
  list-style: none;
}

.diagnosis-card__details-group summary::-webkit-details-marker {
  display: none;
}

.diagnosis-card__details-group ul {
  margin: 0;
  padding: 0 0.82rem 0.75rem 1.8rem;
  color: #1f2937;
  font-size: 0.78rem;
  line-height: 1.55;
}

.diagnosis-card__details-group li + li {
  margin-top: 0.28rem;
}

.diagnosis-card__source-list {
  display: grid;
  gap: 0.4rem;
  padding: 0 0.72rem 0.72rem;
}

.diagnosis-card__source {
  padding: 0.48rem 0.55rem;
  border-radius: 6px;
  background: #fff;
  color: #334155;
  font-size: 0.76rem;
  line-height: 1.45;
}

:global(.dark) .diagnosis-card {
  border-color: rgba(148, 163, 184, 0.24);
  background: #0f172a;
}

:global(.dark) .diagnosis-card__header,
:global(.dark) .diagnosis-card__metric,
:global(.dark) .diagnosis-card__action,
:global(.dark) .diagnosis-card__details-group {
  border-color: rgba(148, 163, 184, 0.18);
}

:global(.dark) .diagnosis-card__title,
:global(.dark) .diagnosis-card__metric-value,
:global(.dark) .diagnosis-card__action-text,
:global(.dark) .diagnosis-card__details-group ul,
:global(.dark) .diagnosis-card__source {
  color: #f8fafc;
}

:global(.dark) .diagnosis-card__conclusion,
:global(.dark) .diagnosis-card__section-title,
:global(.dark) .diagnosis-card__details-group summary {
  color: #e2e8f0;
}

:global(.dark) .diagnosis-card__meta,
:global(.dark) .diagnosis-card__metric-label,
:global(.dark) .diagnosis-card__metric-meta {
  color: #cbd5e1;
}

:global(.dark) .diagnosis-card__metric,
:global(.dark) .diagnosis-card__details-group {
  background: rgba(30, 41, 59, 0.78);
}

:global(.dark) .diagnosis-card__action,
:global(.dark) .diagnosis-card__source {
  background: rgba(15, 23, 42, 0.82);
}

:global(.dark) .diagnosis-card__workorder-box {
  border-color: rgba(96, 165, 250, 0.35);
  background: rgba(30, 41, 59, 0.82);
}

:global(.dark) .diagnosis-card__workorder-status,
:global(.dark) .diagnosis-card__workorder-grid strong,
:global(.dark) .diagnosis-card__workorder-evidence p {
  color: #f8fafc;
}

:global(.dark) .diagnosis-card__workorder-reason,
:global(.dark) .diagnosis-card__workorder-grid span,
:global(.dark) .diagnosis-card__workorder-evidence span {
  color: #cbd5e1;
}

:global(.dark) .diagnosis-card__workorder-grid > div,
:global(.dark) .diagnosis-card__workorder-evidence {
  background: rgba(15, 23, 42, 0.72);
}

@media (max-width: 640px) {
  .diagnosis-card__header,
  .diagnosis-card__action {
    grid-template-columns: 1fr;
  }

  .diagnosis-card__header {
    flex-direction: column;
  }

  .diagnosis-card__action {
    gap: 0.28rem;
  }

  .diagnosis-card__workorder-head {
    flex-direction: column;
  }

  .diagnosis-card__workorder-button {
    width: 100%;
  }
}
</style>
