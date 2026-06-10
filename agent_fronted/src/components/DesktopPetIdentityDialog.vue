<script setup lang="ts">
import { computed } from 'vue'
import { CircleCheckFilled, CircleCloseFilled, Loading, UserFilled } from '@element-plus/icons-vue'

type IdentityState = 'identifying' | 'success' | 'failed'

const props = defineProps<{
  modelValue: boolean
  state: IdentityState
  userId?: string | null
  userRole?: string | null
  permissionHint?: string | null
  message?: string | null
}>()

const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean): void
}>()

const dialogTitle = computed(() => {
  if (props.state === 'success') return '身份识别成功'
  if (props.state === 'failed') return '身份识别失败'
  return '正在识别身份'
})

const stateText = computed(() => {
  if (props.state === 'success') return props.message || '已完成身份识别'
  if (props.state === 'failed') return props.message || '未能识别当前用户身份'
  return props.message || '小飞正在进行声纹识别，请稍候...'
})

const normalizedUserId = computed(() => props.userId || '未确认')
const normalizedUserRole = computed(() => props.userRole || '未确认')

const closeDialog = () => {
  emit('update:modelValue', false)
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    :title="dialogTitle"
    width="380px"
    center
    append-to-body
    custom-class="desktop-pet-identity-dialog"
    @update:model-value="closeDialog"
  >
    <div class="identity-result" :class="`identity-result--${state}`">
      <div class="identity-result__icon">
        <el-icon v-if="state === 'identifying'" class="is-loading"><Loading /></el-icon>
        <el-icon v-else-if="state === 'success'"><CircleCheckFilled /></el-icon>
        <el-icon v-else><CircleCloseFilled /></el-icon>
      </div>

      <div class="identity-result__message">{{ stateText }}</div>

      <div v-if="state === 'success'" class="identity-card">
        <div class="identity-card__avatar">
          <el-icon><UserFilled /></el-icon>
        </div>
        <div class="identity-card__content">
          <div class="identity-card__row">
            <span class="identity-card__label">用户编号</span>
            <span class="identity-card__value">{{ normalizedUserId }}</span>
          </div>
          <div class="identity-card__row">
            <span class="identity-card__label">当前身份</span>
            <span class="identity-card__value">{{ normalizedUserRole }}</span>
          </div>
          <div v-if="permissionHint" class="identity-card__permission">
            {{ permissionHint }}
          </div>
        </div>
      </div>
    </div>
  </el-dialog>
</template>

<style scoped lang="scss">
.identity-result {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 18px;
  padding: 8px 0 4px;

  &__icon {
    width: 72px;
    height: 72px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 42px;
    background: rgba(22, 93, 255, 0.1);
    color: #165dff;
  }

  &__message {
    font-size: 15px;
    color: #303133;
    line-height: 1.6;
    text-align: center;
  }

  &--success &__icon {
    background: rgba(82, 196, 26, 0.14);
    color: #52c41a;
  }

  &--failed &__icon {
    background: rgba(255, 77, 79, 0.12);
    color: #ff4d4f;
  }
}

.identity-card {
  width: 100%;
  display: flex;
  gap: 14px;
  padding: 14px;
  border-radius: 8px;
  border: 1px solid rgba(22, 93, 255, 0.14);
  background: rgba(22, 93, 255, 0.04);

  &__avatar {
    width: 42px;
    height: 42px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    flex: 0 0 auto;
    background: #165dff;
    color: #ffffff;
    font-size: 22px;
  }

  &__content {
    flex: 1;
    min-width: 0;
  }

  &__row {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    font-size: 13px;
    line-height: 1.8;
  }

  &__label {
    color: #909399;
    white-space: nowrap;
  }

  &__value {
    color: #303133;
    font-weight: 700;
    text-align: right;
    overflow-wrap: anywhere;
  }

  &__permission {
    margin-top: 8px;
    padding-top: 8px;
    border-top: 1px solid rgba(22, 93, 255, 0.12);
    color: #606266;
    font-size: 13px;
    line-height: 1.6;
  }
}

:deep(.desktop-pet-identity-dialog) {
  border-radius: 8px;
}
</style>
