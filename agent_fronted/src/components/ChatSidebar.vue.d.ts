// ChatSidebar 组件类型声明文件
import { DefineComponent } from 'vue'

export interface ChatSidebarProps {
  chatHistory: Array<{ id: string; title?: string }>
  currentChatId?: string | number | null
}

export interface ChatSidebarEmits {
  (e: 'select-chat', id: string): void
  (e: 'new-chat'): void
}

const ChatSidebar: DefineComponent<ChatSidebarProps>

export default ChatSidebar
