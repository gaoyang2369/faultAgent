<template>
  <div class="sidebar">
    <div class="history-header">
      <h2>咨询记录</h2>
      <button class="new-chat" @click="$emit('new-chat')">
        <PlusIcon class="icon" />
        新咨询
      </button>
    </div>

    <div class="history-tools">
      <input
        class="history-search"
        type="search"
        :value="historySearchQuery"
        placeholder="搜索咨询记录"
        @input="handleSearchInput"
      />
    </div>

    <div v-if="historyError" class="history-state error">{{ historyError }}</div>
    <div v-else-if="!historyLoading && !visibleChatHistory.length" class="history-state">
      {{ historySearchQuery ? '没有匹配的咨询记录' : '暂无咨询记录' }}
    </div>

    <div v-else class="history-list">
      <div
        v-for="chat in visibleChatHistory"
        :key="chat.id"
        class="history-item"
        :class="{ 'active': currentChatId === chat.id }"
        @click="$emit('select-chat', chat.id)"
      >
        <ChatBubbleLeftRightIcon class="icon" />
        <span class="title">{{ chat.title || '新咨询' }}</span>
        <span v-if="chat.source === 'local-cache'" class="history-badge">本地缓存</span>
        <div class="history-actions" @click.stop>
          <button
            class="history-more"
            :title="`${chat.title || '新咨询'} 的更多操作`"
            @click="toggleMenu(chat.id)"
          >
            ...
          </button>
          <div v-if="openMenuId === chat.id" class="history-menu">
            <button @click="openRenameDialog(chat)">重命名</button>
            <button class="danger" @click="confirmDelete(chat)">删除</button>
          </div>
        </div>
      </div>
    </div>

    <button
      v-if="historyHasMore || historyLoading"
      class="history-load-more"
      type="button"
      :disabled="historyLoading"
      @click="$emit('load-more-history')"
    >
      {{ historyLoading ? '加载中...' : '加载更多' }}
    </button>

    <el-dialog
      v-model="renameDialogVisible"
      title="重命名咨询记录"
      width="360px"
      append-to-body
      @closed="resetRenameDialog"
    >
      <el-input
        v-model="renameTitle"
        maxlength="60"
        show-word-limit
        placeholder="请输入新的咨询记录名称"
        @keyup.enter="submitRename"
      />
      <template #footer>
        <el-button @click="renameDialogVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!canSubmitRename" @click="submitRename">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ChatBubbleLeftRightIcon,
  PlusIcon
} from '@heroicons/vue/24/outline'

type ChatHistoryItem = { id: string; title?: string; source?: 'server' | 'local-cache' }

const props = defineProps({
  chatHistory: {
    type: Array as () => ChatHistoryItem[],
    default: () => []
  },
  currentChatId: {
    type: [String, Number, null] as any,
    default: null
  },
  historyLoading: {
    type: Boolean,
    default: false
  },
  historyError: {
    type: String,
    default: ''
  },
  historyHasMore: {
    type: Boolean,
    default: false
  },
  historySearchQuery: {
    type: String,
    default: ''
  }
})

const emit = defineEmits<{
  (e: 'select-chat', id: string): void
  (e: 'new-chat'): void
  (e: 'rename-chat', payload: { id: string; title: string }): void
  (e: 'delete-chat', id: string): void
  (e: 'load-more-history'): void
  (e: 'update-history-search', keyword: string): void
}>()

const openMenuId = ref<string | null>(null)
const renameDialogVisible = ref(false)
const renameTarget = ref<ChatHistoryItem | null>(null)
const renameTitle = ref('')

const canSubmitRename = computed(() => {
  const title = renameTitle.value.trim()
  return Boolean(renameTarget.value?.id && title && title !== (renameTarget.value.title || '').trim())
})

const visibleChatHistory = computed(() => props.chatHistory)

const closeMenu = () => {
  openMenuId.value = null
}

const toggleMenu = (chatId: string) => {
  openMenuId.value = openMenuId.value === chatId ? null : chatId
}

const handleSearchInput = (event: Event) => {
  const target = event.target as HTMLInputElement | null
  emit('update-history-search', target?.value || '')
}

const handleDocumentClick = () => {
  closeMenu()
}

document.addEventListener('click', handleDocumentClick)

onBeforeUnmount(() => {
  document.removeEventListener('click', handleDocumentClick)
})

const openRenameDialog = (chat: ChatHistoryItem) => {
  closeMenu()
  renameTarget.value = chat
  renameTitle.value = chat.title || '新咨询'
  renameDialogVisible.value = true
}

const resetRenameDialog = () => {
  renameTarget.value = null
  renameTitle.value = ''
}

const submitRename = () => {
  if (!canSubmitRename.value || !renameTarget.value) return
  emit('rename-chat', {
    id: renameTarget.value.id,
    title: renameTitle.value.trim().slice(0, 60)
  })
  renameDialogVisible.value = false
}

const confirmDelete = async (chat: ChatHistoryItem) => {
  closeMenu()
  try {
    await ElMessageBox.confirm(
      `确认删除咨询记录「${chat.title || '新咨询'}」吗？删除后将无法从当前列表恢复。`,
      '删除咨询记录',
      {
        confirmButtonText: '确认删除',
        cancelButtonText: '取消',
        type: 'warning',
        confirmButtonClass: 'el-button--danger'
      }
    )
    emit('delete-chat', chat.id)
  } catch {
    ElMessage.info('已取消删除。')
  }
}
</script>

<style scoped>
.history-tools {
  padding: 8px 12px;
}

.history-search {
  width: 100%;
  min-height: 34px;
  border: 1px solid rgba(148, 163, 184, 0.45);
  border-radius: 8px;
  padding: 0 10px;
  font-size: 13px;
  outline: none;
}

.history-search:focus {
  border-color: #2563eb;
  box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.12);
}

.history-state {
  padding: 12px;
  color: #64748b;
  font-size: 13px;
}

.history-state.error {
  color: #b91c1c;
}

.history-load-more {
  margin: 8px 12px 12px;
  min-height: 34px;
  border: 1px solid rgba(148, 163, 184, 0.45);
  border-radius: 8px;
  background: #fff;
  color: #334155;
  cursor: pointer;
}

.history-load-more:disabled {
  cursor: wait;
  opacity: 0.65;
}
</style>
