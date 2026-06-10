<template>
  <el-dialog
    :model-value="modelValue"
    @update:model-value="emit('update:modelValue', $event)"
    width="min(92vw, 460px)"
    top="12vh"
    class="admin-auth-dialog"
    append-to-body
  >
    <template #header>
      <div class="dialog-title">
        <span>管理员登录</span>
        <small>用于测试 PDF 上传与管理链路</small>
      </div>
    </template>

    <div class="auth-panel">
      <div v-if="isAdmin" class="identity-card success">
        <strong>{{ displayName }}</strong>
        <p>当前已获得管理员身份，可直接进入 PDF 上传登记。</p>
      </div>

      <template v-else>
        <div class="identity-card">
          <strong>当前为访客模式</strong>
          <p>请输入管理员账号和密码。声纹认证入口保留，待后续真实识别链路接回。</p>
        </div>

        <label class="field-block">
          <span>用户名</span>
          <input
            v-model.trim="username"
            type="text"
            autocomplete="username"
            placeholder="请输入管理员用户名"
            @keydown.enter.prevent="handleLogin"
          />
        </label>

        <label class="field-block">
          <span>密码</span>
          <input
            v-model="password"
            type="password"
            autocomplete="current-password"
            placeholder="请输入管理员密码"
            @keydown.enter.prevent="handleLogin"
          />
        </label>
      </template>

      <div class="dialog-actions">
        <button
          v-if="isAdmin"
          type="button"
          class="primary-btn"
          @click="emit('open-upload')"
        >
          打开 PDF 上传
        </button>
        <button
          v-else
          type="button"
          class="primary-btn"
          :disabled="isSubmitting || !username || !password"
          @click="handleLogin"
        >
          {{ isSubmitting ? '登录中...' : '登录管理员' }}
        </button>

        <button
          v-if="isAdmin"
          type="button"
          class="secondary-btn"
          :disabled="isSubmitting"
          @click="handleLogout"
        >
          退出登录
        </button>
        <button
          v-else
          type="button"
          class="secondary-btn"
          :disabled="isSubmitting"
          @click="showVoicePendingMessage"
        >
          声纹认证待接入
        </button>
      </div>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import { adminAuthAPI } from '@/services/api'

type IdentityPayload = {
  user_id: string
  user_role: string
  is_admin: boolean
  auth_method?: string | null
}

const props = defineProps<{
  modelValue: boolean
  isAdmin: boolean
  displayName: string
}>()

const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean): void
  (event: 'authenticated', identity: IdentityPayload): void
  (event: 'logged-out', identity: IdentityPayload): void
  (event: 'open-upload'): void
}>()

const username = ref('')
const password = ref('')
const isSubmitting = ref(false)

const resetForm = () => {
  username.value = ''
  password.value = ''
}

const extractErrorMessage = (error: unknown, fallback: string) => {
  if (error instanceof Error && error.message.trim()) {
    return error.message
  }
  return fallback
}

const handleLogin = async () => {
  if (!username.value || !password.value || isSubmitting.value) return
  isSubmitting.value = true
  try {
    const identity = await adminAuthAPI.login(username.value, password.value)
    ElMessage.success('管理员登录成功。')
    emit('authenticated', identity)
    emit('update:modelValue', false)
    resetForm()
  } catch (error) {
    ElMessage.error(extractErrorMessage(error, '管理员登录失败，请稍后重试。'))
  } finally {
    isSubmitting.value = false
  }
}

const handleLogout = async () => {
  if (isSubmitting.value) return
  isSubmitting.value = true
  try {
    const identity = await adminAuthAPI.logout()
    ElMessage.success('已退出管理员登录。')
    emit('logged-out', identity)
    emit('update:modelValue', false)
    resetForm()
  } catch (error) {
    ElMessage.error(extractErrorMessage(error, '退出登录失败，请稍后重试。'))
  } finally {
    isSubmitting.value = false
  }
}

const showVoicePendingMessage = () => {
  ElMessage.info('声纹认证入口已保留，当前版本优先使用用户名和密码进行管理员测试。')
}

watch(
  () => props.modelValue,
  (visible) => {
    if (!visible) {
      resetForm()
      isSubmitting.value = false
    }
  }
)
</script>

<style scoped lang="scss">
.dialog-title {
  display: flex;
  flex-direction: column;
  gap: 4px;
  span {
    font-size: 18px;
    font-weight: 800;
    color: #111827;
  }
  small {
    color: #64748b;
    font-size: 12px;
  }
}

.auth-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.identity-card {
  padding: 16px;
  border-radius: 10px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;

  &.success {
    background: rgba(82, 196, 26, 0.08);
    border-color: rgba(82, 196, 26, 0.24);
  }

  strong {
    display: block;
    color: #111827;
    font-size: 15px;
  }

  p {
    margin: 8px 0 0;
    color: #64748b;
    line-height: 1.6;
    font-size: 13px;
  }
}

.field-block {
  display: flex;
  flex-direction: column;
  gap: 8px;

  span {
    color: #475569;
    font-size: 13px;
    font-weight: 700;
  }

  input {
    width: 100%;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 11px 12px;
    outline: none;
    font-size: 14px;
    transition: border-color 0.2s, box-shadow 0.2s;

    &:focus {
      border-color: #2563eb;
      box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12);
    }
  }
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  flex-wrap: wrap;
}

.primary-btn,
.secondary-btn {
  border: 0;
  border-radius: 8px;
  padding: 10px 16px;
  font-weight: 700;
  cursor: pointer;
}

.primary-btn {
  background: #2563eb;
  color: #fff;
}

.secondary-btn {
  background: #e2e8f0;
  color: #334155;
}

button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
