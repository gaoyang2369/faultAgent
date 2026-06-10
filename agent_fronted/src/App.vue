<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useDark, useToggle } from '@vueuse/core'
import { SunIcon, MoonIcon, CloudArrowUpIcon } from '@heroicons/vue/24/outline'
import { WrenchScrewdriverIcon } from '@heroicons/vue/24/outline'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import {
  queueDesktopPetVoiceAutoStart,
  useDesktopPetBridge,
  type DesktopPetPayload
} from '@/composables/useDesktopPetBridge'
import { useUserIdentityStore } from '@/stores/userIdentity'
import DesktopPetIdentityDialog from '@/components/DesktopPetIdentityDialog.vue'
import FileUploadDialog from '@/views/FileUpload.vue'
import VoiceAuthDialog from '@/components/VoiceAuthDialog.vue'
import AdminAuthDialog from '@/components/AdminAuthDialog.vue'

type IdentityDialogState = 'identifying' | 'success' | 'failed'
type VoiceAuthSuccessPayload = {
  role: string
  userId: string
  userRole: string
  voiceprintScore?: number | null
  transcript?: string
}

const isDark = useDark()
const toggleDark = useToggle(isDark)
const showUploadDialog = ref(false)
const showVoiceAuthDialog = ref(false)
const showAdminAuthDialog = ref(false)
const showDesktopPetIdentityDialog = ref(false)
const desktopPetIdentityState = ref<IdentityDialogState>('identifying')
const desktopPetDialogMessage = ref('小飞正在进行声纹识别，请稍候...')
const desktopPetUserId = ref<string | null>(null)
const desktopPetUserRole = ref<string | null>(null)
const desktopPetPermissionHint = ref<string | null>(null)
const shouldAutoDismissDesktopPetIdentityDialog = ref(false)
const route = useRoute()
const router = useRouter()

let desktopPetIdentityCloseTimer: ReturnType<typeof window.setTimeout> | null = null
let desktopPetVoiceQueueReplayTimer: ReturnType<typeof window.setTimeout> | null = null

const userIdentityStore = useUserIdentityStore()
const { displayName, status, hasIdentity, userId, userRole } = storeToRefs(userIdentityStore)

const voiceAuthLaunchValues = ['1', 'true', 'yes', 'open']
const desktopPetSources = ['desktop-pet', 'desktoppet', 'xiaofei', '小飞']
const DESKTOP_PET_USER_INFO_AUTOSTART_MAX_AGE_MS = 30000
const desktopPetRoleRules = [
  {
    keywords: ['数据录入员', 'l1', '数据查看和录入'],
    role: '数据录入员',
    permissionHint: '用户权限：数据查看和录入'
  },
  {
    keywords: ['数据分析师', 'l2', '数据查看、录入和分析', '数据查看录入和分析'],
    role: '数据分析师',
    permissionHint: '用户权限：数据查看、录入和分析'
  },
  {
    keywords: ['系统工程师', 'l3', '数据管理和系统维护'],
    role: '系统工程师',
    permissionHint: '用户权限：数据管理和系统维护'
  },
  {
    keywords: ['技术专家', 'l4', '高级技术支持和权限管理'],
    role: '技术专家',
    permissionHint: '用户权限：高级技术支持和权限管理'
  },
  {
    keywords: ['总监', 'l5', '全局管理'],
    role: '总监',
    permissionHint: '用户权限：全局管理'
  }
]

const normalizeQueryValue = (value: unknown) => {
  const firstValue = Array.isArray(value) ? value[0] : value
  return typeof firstValue === 'string' ? firstValue.trim().toLowerCase() : ''
}

const normalizeQueryText = (value: unknown) => {
  const firstValue = Array.isArray(value) ? value[0] : value
  return typeof firstValue === 'string' ? firstValue.trim() : ''
}

const shouldOpenVoiceAuthDialogFromRoute = () => {
  const voiceAuth = normalizeQueryValue(route.query.voiceAuth)
  return voiceAuthLaunchValues.includes(voiceAuth)
}

const shouldAutoStartVoiceFromRoute = () => {
  const voiceAutoStart = normalizeQueryValue(route.query.voiceAutoStart)
  return voiceAuthLaunchValues.includes(voiceAutoStart)
}

const isDesktopPetLaunchRoute = () => {
  const source = normalizeQueryValue(route.query.source)
  const from = normalizeQueryValue(route.query.from)

  return desktopPetSources.includes(source) ||
    desktopPetSources.includes(from) ||
    shouldAutoStartVoiceFromRoute()
}

const isDesktopPetVoiceAutoStartPayload = (payload: DesktopPetPayload) => {
  const source = normalizeQueryValue(payload.source)
  const from = normalizeQueryValue(payload.from)
  const voiceAutoStart = normalizeQueryValue(
    payload.voiceAutoStart || payload.voice_auto_start || payload.autoVoiceStart || payload.auto_voice_start
  )

  return desktopPetSources.includes(source) ||
    desktopPetSources.includes(from) ||
    voiceAuthLaunchValues.includes(voiceAutoStart)
}

const isRecentDesktopPetUserInfo = (payload: DesktopPetPayload) => {
  const rawTimestamp = Number(payload.timestamp || payload.received_at || 0)
  if (!rawTimestamp) return true

  const timestampMs = rawTimestamp < 1000000000000 ? rawTimestamp * 1000 : rawTimestamp
  return Date.now() - timestampMs <= DESKTOP_PET_USER_INFO_AUTOSTART_MAX_AGE_MS
}

const normalizeRoleText = (role: unknown) => {
  if (Array.isArray(role)) return role.map(item => String(item).trim()).filter(Boolean).join('、')
  return typeof role === 'string' ? role.trim() : ''
}

const resolveDesktopPetRawRole = (payload: DesktopPetPayload) => {
  return normalizeRoleText(
    payload.user_role || payload.role || payload.clean_role || payload.permission_hint
  )
}

const resolveDesktopPetRole = (role: unknown) => {
  const rawRole = normalizeRoleText(role)
  const cleanRole = rawRole.toLowerCase()
  const matchedRule = desktopPetRoleRules.find(rule =>
    rule.keywords.some(keyword => cleanRole.includes(keyword.toLowerCase()))
  )

  if (matchedRule) {
    return {
      role: matchedRule.role,
      permissionHint: matchedRule.permissionHint
    }
  }

  if (!rawRole || ['访客', '游客', 'guest', 'visitor'].some(keyword => cleanRole.includes(keyword))) {
    return {
      role: '访客',
      permissionHint: '用户权限：访客权限'
    }
  }

  return {
    role: rawRole,
    permissionHint: '用户权限：访客权限'
  }
}

const openDesktopPetIdentityDialog = (message = '小飞正在进行声纹识别，请稍候...') => {
  if (desktopPetIdentityCloseTimer !== null) {
    window.clearTimeout(desktopPetIdentityCloseTimer)
    desktopPetIdentityCloseTimer = null
  }
  desktopPetIdentityState.value = 'identifying'
  desktopPetDialogMessage.value = message
  desktopPetUserId.value = null
  desktopPetUserRole.value = null
  desktopPetPermissionHint.value = null
  showDesktopPetIdentityDialog.value = true
  userIdentityStore.resetUserInfo()
  userIdentityStore.setStatus('connecting')
}

const scheduleDesktopPetIdentityDialogClose = () => {
  if (!shouldAutoDismissDesktopPetIdentityDialog.value) return
  if (desktopPetIdentityCloseTimer !== null) {
    window.clearTimeout(desktopPetIdentityCloseTimer)
  }

  desktopPetIdentityCloseTimer = window.setTimeout(() => {
    desktopPetIdentityCloseTimer = null
    if (desktopPetIdentityState.value === 'success') {
      showDesktopPetIdentityDialog.value = false
      shouldAutoDismissDesktopPetIdentityDialog.value = false
    }
  }, 700)
}

const queueDesktopPetLaunchVoice = (reason = 'desktop_pet_launch') => {
  shouldAutoDismissDesktopPetIdentityDialog.value = true
  queueDesktopPetVoiceAutoStart(reason)

  if (desktopPetVoiceQueueReplayTimer !== null) {
    window.clearTimeout(desktopPetVoiceQueueReplayTimer)
  }

  desktopPetVoiceQueueReplayTimer = window.setTimeout(() => {
    desktopPetVoiceQueueReplayTimer = null
    queueDesktopPetVoiceAutoStart(`${reason}_route_settled`)
  }, 600)
}

const applyDesktopPetUserInfo = (payload: DesktopPetPayload) => {
  const rawUserId = typeof payload.user_id === 'string' ? payload.user_id.trim() : ''
  const rawRole = resolveDesktopPetRawRole(payload)

  if (!rawUserId && !rawRole) {
    showDesktopPetAuthFailed(payload.message || '未收到有效的用户身份信息。')
    return
  }

  const resolvedRole = resolveDesktopPetRole(rawRole)
  const nextUserId = rawUserId || 'desktop-pet-user'

  userIdentityStore.setUserInfo({
    userId: resolvedRole.role === '访客' ? 'guest' : nextUserId,
    userRole: resolvedRole.role
  })
  userIdentityStore.setStatus('connected')

  desktopPetIdentityState.value = 'success'
  desktopPetDialogMessage.value = payload.message || '小飞已完成身份识别。'
  desktopPetUserId.value = nextUserId
  desktopPetUserRole.value = resolvedRole.role
  desktopPetPermissionHint.value = resolvedRole.permissionHint
  showDesktopPetIdentityDialog.value = true
  scheduleDesktopPetIdentityDialogClose()
}

const showDesktopPetAuthFailed = (message?: string | null) => {
  userIdentityStore.resetUserInfo()
  userIdentityStore.setStatus('error')
  desktopPetIdentityState.value = 'failed'
  desktopPetDialogMessage.value = message || '小飞未能识别当前用户身份，请重试。'
  desktopPetUserId.value = null
  desktopPetUserRole.value = null
  desktopPetPermissionHint.value = null
  showDesktopPetIdentityDialog.value = true
}

const resolveDesktopPetPayloadFromRoute = (): DesktopPetPayload | null => {
  const userId = normalizeQueryText(route.query.user_id || route.query.userId)
  const userRole = normalizeQueryText(
    route.query.user_role || route.query.userRole || route.query.role || route.query.clean_role || route.query.cleanRole
  )

  if (!userId && !userRole) return null

  return {
    type: 'user_info',
    user_id: userId,
    user_role: userRole,
    message: normalizeQueryText(route.query.message)
  }
}

const clearDesktopPetLaunchQuery = () => {
  const nextQuery = { ...route.query }
  delete nextQuery.voiceAuth
  delete nextQuery.voiceAutoStart
  delete nextQuery.user_id
  delete nextQuery.userId
  delete nextQuery.user_role
  delete nextQuery.userRole
  delete nextQuery.role
  delete nextQuery.clean_role
  delete nextQuery.cleanRole
  delete nextQuery.message

  if (desktopPetSources.includes(normalizeQueryValue(nextQuery.source))) {
    delete nextQuery.source
  }

  if (desktopPetSources.includes(normalizeQueryValue(nextQuery.from))) {
    delete nextQuery.from
  }

  const isChatRoute = route.name === 'chat'
  router.replace(isChatRoute ? { path: route.path, query: nextQuery } : { name: 'chat', query: nextQuery }).catch(() => {})
}

watch(
  () => route.fullPath,
  () => {
    if (shouldOpenVoiceAuthDialogFromRoute() && !isDesktopPetLaunchRoute()) {
      openVoiceAuthDialog()
      clearDesktopPetLaunchQuery()
      return
    }

    if (!isDesktopPetLaunchRoute()) return

    queueDesktopPetLaunchVoice('desktop_pet_launch')

    const routePayload = resolveDesktopPetPayloadFromRoute()
    if (routePayload) {
      applyDesktopPetUserInfo(routePayload)
    } else {
      openDesktopPetIdentityDialog('小飞已唤醒，正在等待身份识别结果...')
    }
    clearDesktopPetLaunchQuery()
  },
  { immediate: true }
)

const shouldAutoOpenVoiceAuthOnLoad = () => {
  const configured = normalizeQueryValue(import.meta.env.VITE_VOICE_AUTH_ON_LOAD)
  if (['0', 'false', 'no', 'off'].includes(configured)) return false
  if (voiceAuthLaunchValues.includes(configured)) return true
  return false
}

onMounted(() => {
  if (showVoiceAuthDialog.value) return
  if (shouldAutoOpenVoiceAuthOnLoad()) {
    openVoiceAuthDialog()
  }
})

useDesktopPetBridge({
  onOpen: () => {
    if (showDesktopPetIdentityDialog.value && desktopPetIdentityState.value !== 'identifying') return
    openDesktopPetIdentityDialog()
  },
  onUserInfo: payload => {
    applyDesktopPetUserInfo(payload)
    if (isDesktopPetVoiceAutoStartPayload(payload) || isRecentDesktopPetUserInfo(payload)) {
      queueDesktopPetLaunchVoice('desktop_pet_user_info')
    }
  },
  onAuthFailed: payload => showDesktopPetAuthFailed(payload.message || payload.error || null)
})

const openUploadDialog = () => {
  if (!canUploadFile.value) return
  showUploadDialog.value = true
}

const openVoiceAuthDialog = () => {
  showVoiceAuthDialog.value = true
}

const onVoiceAuthSuccess = async (payload: VoiceAuthSuccessPayload | string) => {
  const normalizedPayload = typeof payload === 'string'
    ? {
        role: payload,
        userId: payload === 'admin' ? 'admin' : 'guest',
        userRole: payload === 'admin' ? '管理员' : '访客'
      }
    : payload
  const isAdmin = normalizedPayload.role === 'admin'
  userIdentityStore.setUserInfo({
    userId: normalizedPayload.userId || (isAdmin ? 'admin' : 'guest'),
    userRole: normalizedPayload.userRole || (isAdmin ? '管理员' : '访客'),
  })
  userIdentityStore.setStatus('connected')

  if (!isAdmin) return

  try {
    await ElMessageBox.confirm(
      '识别为管理员身份，是否需要上传文件进行文档智能识别？',
      '管理员权限已确认',
      { confirmButtonText: '前往上传', cancelButtonText: '暂不需要', type: 'success' }
    )
    showUploadDialog.value = true
  } catch {
    ElMessage.info('可随时通过导航栏「上传」按钮再次打开。')
  }
}

const onAdminAuthenticated = (identity: { user_id: string; user_role: string; is_admin: boolean }) => {
  userIdentityStore.setUserInfo({
    userId: identity.user_id || 'admin',
    userRole: identity.user_role || '管理员',
  })
  userIdentityStore.setStatus('connected')
}

const onAdminLoggedOut = () => {
  userIdentityStore.resetUserInfo()
  userIdentityStore.setStatus('idle')
}

const statusLabel = computed(() => {
  if (hasIdentity.value) return '识别已完成'

  switch (status.value) {
    case 'connecting':
      return '连接中'
    case 'connected':
      return '已连接'
    case 'error':
      return '连接异常'
    case 'disconnected':
      return '未连接'
    default:
      return '等待连接'
  }
})

const statusClass = computed(() => {
  if (hasIdentity.value) return 'connected'

  switch (status.value) {
    case 'connecting':
      return 'connecting'
    case 'connected':
      return 'connected no-identity'
    case 'error':
      return 'error'
    case 'disconnected':
      return 'disconnected'
    default:
      return 'idle'
  }
})

const isVisitorIdentity = computed(() => {
  const roleText = String(userRole.value || '').trim().toLowerCase()
  const userIdText = String(userId.value || '').trim().toLowerCase()
  const visitorKeywords = ['guest', 'visitor', '访客', '游客']

  if (roleText) {
    return visitorKeywords.some(keyword => roleText.includes(keyword.toLowerCase()))
  }

  return visitorKeywords.some(keyword =>
    userIdText.includes(keyword.toLowerCase())
  )
})

const canUploadFile = computed(() => {
  return hasIdentity.value && !isVisitorIdentity.value
})
</script>

<template>
  <div class="app" :class="{ 'dark': isDark }">
    <nav class="navbar">
      <div class="logo">
        <WrenchScrewdriverIcon class="brand-icon" />
        <div class="brand-text">
          <div class="brand-title">DCMA-工业运行诊断系统</div>
          <div class="brand-subtitle">基于Agent的智能分析</div>
        </div>
      </div>
      <div class="navbar-actions">
        <button
          v-if="canUploadFile"
          class="nav-action-btn upload-nav-btn"
          title="PDF 上传登记"
          @click="openUploadDialog"
        >
          <CloudArrowUpIcon class="nav-button-icon" />
          <span class="nav-label">上传</span>
        </button>
        <div
          class="identity-indicator clickable"
          :class="statusClass"
          title="点击进行身份认证"
          @click="showAdminAuthDialog = true"
        >
          <div class="status-dot"></div>
<!--          &lt;!&ndash; 单个头像 &ndash;&gt;
          <img v-if="avatarUrls.length === 1" :src="avatarUrls[0]" class="identity-icon" />
          &lt;!&ndash; 多个头像折叠显示 &ndash;&gt;
          <div v-else class="avatars-container">
            <img 
              v-for="(avatar, index) in avatarUrls.slice(0, 3)" 
              :key="index"
              :src="avatar" 
              class="identity-icon small"
            />
            <div v-if="avatarUrls.length > 3" class="extra-count">
              +{{ avatarUrls.length - 3 }}
            </div>
          </div>-->
          <div class="identity-content">
            <span class="identity-name">{{ displayName }}</span>
            <span class="identity-status">{{ statusLabel }}</span>
          </div>
        </div>
        <button @click="toggleDark()" class="theme-toggle">
          <SunIcon v-if="isDark" class="icon" />
          <MoonIcon v-else class="icon" />
        </button>
      </div>
    </nav>
    <router-view v-slot="{ Component }">
      <transition name="fade" mode="out-in">
        <component :is="Component" />
      </transition>
    </router-view>
    <VoiceAuthDialog
      v-model="showVoiceAuthDialog"
      @auth-success="onVoiceAuthSuccess"
    />
    <AdminAuthDialog
      v-model="showAdminAuthDialog"
      :is-admin="!isVisitorIdentity && hasIdentity"
      :display-name="displayName"
      @authenticated="onAdminAuthenticated"
      @logged-out="onAdminLoggedOut"
      @open-upload="showUploadDialog = true"
    />
    <DesktopPetIdentityDialog
      v-model="showDesktopPetIdentityDialog"
      :state="desktopPetIdentityState"
      :user-id="desktopPetUserId"
      :user-role="desktopPetUserRole"
      :permission-hint="desktopPetPermissionHint"
      :message="desktopPetDialogMessage"
    />
    <FileUploadDialog v-model="showUploadDialog" />
  </div>
</template>

<style lang="scss">
:root {
  --bg-color: #f5f5f5;
  --text-color: #333;
}

.dark {
  --bg-color: #1a1a1a;
  --text-color: #fff;
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

html, body {
  height: 100%;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen,
    Ubuntu, Cantarell, 'Open Sans', 'Helvetica Neue', sans-serif;
  color: var(--text-color);
  background: var(--bg-color);
  min-height: 100vh;
}

.app {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.navbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem 2rem;
  background: rgba(255, 255, 255, 0.1);
  backdrop-filter: blur(10px);
  position: sticky;
  top: 0;
  z-index: 100;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);

  .logo {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    .brand-icon {
      width: 28px;
      height: 28px;
      color: #165DFF;
      flex: 0 0 auto;
    }
    .brand-text {
      display: flex;
      flex-direction: column;
      line-height: 1.2;
    }
    .brand-title {
      font-size: 1.1rem;
      font-weight: 700;
      background: linear-gradient(45deg, #165DFF, #00DFD8);
      background-clip: text;
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      white-space: nowrap;
    }
    .brand-subtitle {
      font-size: 0.8rem;
      color: var(--text-color);
      opacity: 0.75;
      white-space: nowrap;
    }
  }

  .navbar-actions {
    display: flex;
    align-items: center;
    gap: 0.6rem;
  }

  .nav-action-btn {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.4rem 0.8rem;
    border-radius: 999px;
    border: 1px solid rgba(22, 93, 255, 0.2);
    background: rgba(22, 93, 255, 0.06);
    color: #165dff;
    cursor: pointer;
    font-size: 0.8rem;
    font-weight: 600;
    transition: background-color 0.25s ease, border-color 0.25s ease, box-shadow 0.25s ease, transform 0.25s ease;
    white-space: nowrap;

    &:hover {
      border-color: rgba(22, 93, 255, 0.45);
      background: rgba(22, 93, 255, 0.12);
      box-shadow: 0 2px 8px rgba(22, 93, 255, 0.15);
      transform: translateY(-1px);
    }

    &:active {
      transform: translateY(0);
    }

    .nav-button-icon {
      width: 18px;
      height: 18px;
      flex: 0 0 auto;
    }
  }

  .upload-nav-btn {
    border-color: rgba(0, 180, 160, 0.25);
    background: rgba(0, 180, 160, 0.07);
    color: #008f82;

    &:hover {
      border-color: rgba(0, 180, 160, 0.5);
      background: rgba(0, 180, 160, 0.14);
      box-shadow: 0 2px 8px rgba(0, 180, 160, 0.15);
    }
  }

  .identity-indicator {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.4rem 0.75rem;
    border-radius: 999px;
    background: rgba(22, 93, 255, 0.08);
    border: 1px solid rgba(22, 93, 255, 0.15);
    color: var(--text-color);
    transition: background-color 0.3s ease, border-color 0.3s ease, color 0.3s ease;

    &.clickable {
      cursor: pointer;

      &:hover {
        box-shadow: 0 2px 8px rgba(22, 93, 255, 0.12);
      }
    }

    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #d9d9d9;
      transition: background-color 0.3s ease;
    }

    .identity-icon {
      width: 32px;
      height: 32px;
      opacity: 0.85;
      color: var(--text-color);
    }

    .avatars-container {
      display: flex;
      align-items: center;
      
      .identity-icon.small {
        width: 26px;
        height: 26px;
        margin-right: -4px;
        border: 2px solid var(--bg-color);
        border-radius: 50%;
        &:first-child {
          margin-left: 0;
        }
        &:last-child {
          margin-right: 0;
        }
      }
      
      .extra-count {
        width: 18px;
        height: 18px;
        border-radius: 50%;
        background: rgba(0, 0, 0, 0.2);
        color: white;
        font-size: 0.6rem;
        display: flex;
        align-items: center;
        justify-content: center;
        margin-left: 4px;
      }
    }

    .identity-content {
      display: flex;
      flex-direction: column;
      line-height: 1.1;
    }

    .identity-name {
      font-size: 0.85rem;
      font-weight: 600;
      white-space: nowrap;
    }

    .identity-status {
      font-size: 0.7rem;
      opacity: 0.7;
      white-space: nowrap;
    }

    &.connected {
      background: rgba(82, 196, 26, 0.18);
      border-color: rgba(82, 196, 26, 0.4);

      .status-dot {
        background: #52c41a;
      }
    }

    &.connected.no-identity {
      background: rgba(22, 93, 255, 0.12);
      border-color: rgba(22, 93, 255, 0.25);

      .status-dot {
        background: #165dff;
      }
    }

    &.connecting {
      background: rgba(250, 173, 20, 0.15);
      border-color: rgba(250, 173, 20, 0.35);

      .status-dot {
        background: #faad14;
      }
    }

    &.error {
      background: rgba(255, 77, 79, 0.15);
      border-color: rgba(255, 77, 79, 0.35);

      .status-dot {
        background: #ff4d4f;
      }
    }

    &.disconnected,
    &.idle {
      background: rgba(0, 0, 0, 0.05);
      border-color: rgba(0, 0, 0, 0.08);

      .status-dot {
        background: #bfbfbf;
      }
    }
  }

  .theme-toggle {
    background: none;
    border: none;
    cursor: pointer;
    padding: 0.5rem;
    border-radius: 50%;
    transition: background-color 0.3s;

    &:hover {
      background: rgba(255, 255, 255, 0.1);
    }

    .icon {
      width: 24px;
      height: 24px;
      color: var(--text-color);
    }
  }

  .dark & {
    background: rgba(0, 0, 0, 0.2);
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  }
}

.dark .navbar {
  .identity-indicator.disconnected,
  .identity-indicator.idle {
    background: rgba(255, 255, 255, 0.08);
    border-color: rgba(255, 255, 255, 0.15);
  }

  .upload-nav-btn {
    border-color: rgba(0, 210, 190, 0.25);
    background: rgba(0, 210, 190, 0.08);
    color: #00d2be;
  }
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

@media (max-width: 768px) {
  .navbar {
    padding: 1rem;
    .brand-title {
      font-size: 1rem;
    }
    .brand-subtitle {
      display: none;
    }
    .navbar-actions {
      gap: 0.35rem;
    }
    .nav-action-btn {
      padding: 0.4rem 0.55rem;
      .nav-label {
        display: none;
      }
    }
    .identity-indicator {
      padding: 0.35rem 0.6rem;
      .identity-status {
        display: none;
      }
    }
  }
}
</style>
