<template>
  <label class="model-selector" :class="{ 'is-disabled': disabled }">
    <span class="model-selector__label">推理模型</span>
    <span class="model-selector__field">
      <span class="model-selector__pulse" aria-hidden="true"></span>
      <select
        :value="modelValue"
        :disabled="disabled || loading"
        aria-label="选择推理模型"
        @change="$emit('update:modelValue', ($event.target as HTMLSelectElement).value)"
      >
        <option v-if="loading" value="">加载模型中...</option>
        <option v-for="model in models" :key="model.id" :value="model.id">
          {{ model.id }} · {{ model.description }}
        </option>
      </select>
      <ChevronDownIcon class="model-selector__chevron" />
    </span>
  </label>
</template>

<script setup lang="ts">
import { ChevronDownIcon } from '@heroicons/vue/24/outline'

defineProps<{
  modelValue: string
  models: Array<{ id: string; label?: string; description?: string }>
  loading?: boolean
  disabled?: boolean
}>()

defineEmits<{
  'update:modelValue': [value: string]
}>()
</script>
