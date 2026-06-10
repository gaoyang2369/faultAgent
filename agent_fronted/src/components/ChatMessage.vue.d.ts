// ChatMessage 组件类型声明文件
import { DefineComponent } from 'vue'

export interface ChatMessageProps {
  message: Record<string, any>
  isStream?: boolean
}

const ChatMessage: DefineComponent<ChatMessageProps>

export default ChatMessage
