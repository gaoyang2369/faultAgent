<template>
  <section
    v-if="visible"
    class="task-panel"
    :class="panelClasses"
  >
    <div class="task-panel__header">
      <div>
        <p class="task-panel__subtitle">任务进度</p>
        <h3 class="task-panel__title">{{ currentExecutingStepText }}</h3>
      </div>
      <span class="task-panel__status-pill" :class="`status-${taskProgressState}`">
        {{ taskPanelStatusText }}
      </span>
    </div>

    <div class="task-panel__progress" :class="progressClass">
      <div class="task-panel__progress-bar" :style="{ width: taskProgressPercent + '%' }"></div>
    </div>

    <div class="task-panel__progress-copy">
      <span>{{ completedStepText }}</span>
      <strong>{{ taskProgressPercent }}%</strong>
    </div>

    <div v-if="isLoading && !hasTodos" class="task-panel__empty state-loading">
      <span class="task-panel__spinner" aria-hidden="true"></span>
      <span>正在拆解诊断路径...</span>
    </div>
    <ul v-else-if="hasTodos" class="task-panel__list">
      <li
        v-for="(todo, index) in decoratedTodos"
        :key="todo.id"
        class="task-panel__item"
        :class="`task-panel__item--${todo.displayStatus}`"
        :aria-current="todo.displayStatus === 'in_progress' ? 'step' : undefined"
      >
        <span class="task-panel__connector" aria-hidden="true"></span>
        <span class="task-panel__item-icon" aria-hidden="true">
          <span v-if="todo.displayStatus === 'completed'">✓</span>
          <span v-else-if="todo.displayStatus === 'interrupted'">!</span>
          <span v-else>{{ index + 1 }}</span>
        </span>
        <div class="task-panel__item-body">
          <div class="task-panel__item-heading">
            <p class="task-panel__item-title">{{ todo.title }}</p>
            <span class="task-panel__item-status">{{ todo.statusText }}</span>
          </div>
          <p v-if="todo.description" class="task-panel__item-desc">{{ todo.description }}</p>
        </div>
      </li>
    </ul>
  </section>
</template>

<script setup lang="ts">
import { computed, type PropType } from 'vue'
import type { TaskSnapshot } from '@/utils/taskState'
import {
  decorateTodos,
  getTaskHeadline,
  getTaskProgressPercent,
  getTaskProgressState,
  getTaskStatusText,
  hasVisibleTaskSnapshot,
  interruptTaskSnapshot,
  normalizeTaskSummary,
  normalizeTodos
} from '@/utils/taskState'

const props = defineProps({
  taskSnapshot: {
    type: Object as PropType<Partial<TaskSnapshot> | null>,
    default: null
  }
})

const normalizedSnapshot = computed(() => {
  if (props.taskSnapshot?.lifecycleState === 'interrupted') {
    return interruptTaskSnapshot(props.taskSnapshot, props.taskSnapshot?.statusHint || '已停止生成') || props.taskSnapshot
  }
  return props.taskSnapshot
})

const normalizedTodos = computed(() => normalizeTodos(normalizedSnapshot.value?.todos || []))
const summary = computed(() => normalizeTaskSummary(normalizedSnapshot.value?.summary, normalizedTodos.value))
const lifecycleState = computed(() => normalizedSnapshot.value?.lifecycleState || '')
const decoratedTodos = computed(() => decorateTodos(normalizedTodos.value))
const isLoading = computed(() => Boolean(normalizedSnapshot.value?.isLoading))
const hasTodos = computed(() => normalizedTodos.value.length > 0)
const visible = computed(() => hasVisibleTaskSnapshot({
  todos: normalizedTodos.value,
  summary: summary.value,
  isLoading: isLoading.value,
  lifecycleState: lifecycleState.value
}))
const taskProgressState = computed(() => (
  isLoading.value && !hasTodos.value
    ? 'active'
    : getTaskProgressState(summary.value, lifecycleState.value)
))
const taskProgressPercent = computed(() => (isLoading.value && !hasTodos.value ? 18 : getTaskProgressPercent(summary.value)))
const taskPanelStatusText = computed(() => getTaskStatusText(
  summary.value,
  isLoading.value,
  normalizedSnapshot.value?.statusHint || '',
  lifecycleState.value
))
const currentExecutingStepText = computed(() => getTaskHeadline(normalizedTodos.value, isLoading.value, lifecycleState.value))
const completedStepText = computed(() => {
  if (lifecycleState.value === 'interrupted') return `已完成 ${summary.value.completed} 个阶段，任务已停止`
  if (!summary.value.total) return '正在生成诊断路径'
  return `${summary.value.completed} / ${summary.value.total} 个阶段已完成`
})

const panelClasses = computed(() => ({
  'task-panel--active': taskProgressState.value === 'active' || isLoading.value,
  'task-panel--done': taskProgressState.value === 'done',
  'task-panel--interrupted': taskProgressState.value === 'interrupted'
}))

const progressClass = computed(() => `task-panel__progress--${taskProgressState.value}`)
</script>
