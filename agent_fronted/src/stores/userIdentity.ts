import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

type ConnectionStatus = 'idle' | 'connecting' | 'connected' | 'disconnected' | 'error'

const permissionLabelKeywords = [
  '用户权限',
  '全局管理',
  '访客权限',
  '数据查看和录入',
  '数据查看、录入和分析',
  '数据管理和系统维护',
  '高级技术支持和权限管理'
]

const normalizeIdentityText = (value: string | null | undefined) => {
  return typeof value === 'string' ? value.trim() : ''
}

const isPermissionLabel = (value: string) => {
  const normalized = value.replace(/\s/g, '').toLowerCase()
  return permissionLabelKeywords.some(keyword =>
    normalized.includes(keyword.replace(/\s/g, '').toLowerCase())
  )
}

const resolveDisplayIdentityLabel = (nextUserId: string | null, nextUserRole: string | null) => {
  const roleText = normalizeIdentityText(nextUserRole)
  const userIdText = normalizeIdentityText(nextUserId)
  const normalizedUserId = userIdText.toLowerCase()

  if (roleText && !isPermissionLabel(roleText)) {
    return roleText
  }

  if (['guest', 'visitor'].includes(normalizedUserId) || ['游客', '访客'].includes(userIdText)) {
    return '访客'
  }

  return userIdText
}

export const useUserIdentityStore = defineStore('userIdentity', () => {
  const userId = ref<string | null>('admin')
  const userRole = ref<string | null>('管理员')
  const status = ref<ConnectionStatus>('connected')

  const hasIdentity = computed(() => Boolean(userId.value))

  const displayName = computed(() => {
    if (!userId.value) {
      return '等待身份识别'
    }

    const identityLabel = resolveDisplayIdentityLabel(userId.value, userRole.value)
    return `${identityLabel}身份识别已完成`
  })

  const setUserInfo = (payload: { userId?: string | null; userRole?: string | null }) => {
    userId.value = payload.userId ?? null
    userRole.value = payload.userRole ?? null
  }

  const setStatus = (nextStatus: ConnectionStatus) => {
    status.value = nextStatus
  }

  const resetUserInfo = () => {
    userId.value = null
    userRole.value = null
  }

  return {
    userId,
    userRole,
    status,
    hasIdentity,
    displayName,
    setUserInfo,
    setStatus,
    resetUserInfo,
  }
})

