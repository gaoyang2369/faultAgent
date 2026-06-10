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

    <div class="task-panel__stats">
      <span v-for="stat in taskStats" :key="stat.label">{{ stat.label }} {{ stat.value }}</span>
    </div>

    <div v-if="isLoading && !hasTodos" class="task-panel__empty state-loading">
      <span class="task-panel__spinner">◐</span>
      正在规划任务清单...
    </div>
    <ul v-else-if="hasTodos" class="task-panel__list">
        <li
          v-for="todo in decoratedTodos"
          :key="todo.id"
          class="task-panel__item"
          :class="`task-panel__item--${todo.displayStatus}`"
        >
          <span class="task-panel__item-icon" :class="{ spinning: todo.displayStatus === 'in_progress' }">
            {{ todo.statusIcon }}
          </span>
        <div class="task-panel__item-body">
          <p class="task-panel__item-title">{{ todo.title }}</p>
          <p v-if="todo.description" class="task-panel__item-desc">{{ todo.description }}</p>
        </div>
        <span class="task-panel__item-status">
          {{ todo.statusText }}
        </span>
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
const taskStats = computed(() => (
  lifecycleState.value === 'interrupted'
    ? [
        { label: '未开始', value: summary.value.pending },
        { label: '已停止', value: summary.value.interrupted },
        { label: '已完成', value: summary.value.completed }
      ]
    : [
        { label: '未开始', value: summary.value.pending },
        { label: '进行中', value: summary.value.in_progress },
        { label: '已完成', value: summary.value.completed }
      ]
))

const panelClasses = computed(() => ({
  'task-panel--active': taskProgressState.value === 'active' || isLoading.value,
  'task-panel--done': taskProgressState.value === 'done',
  'task-panel--interrupted': taskProgressState.value === 'interrupted'
}))

const progressClass = computed(() => `task-panel__progress--${taskProgressState.value}`)
</script>
