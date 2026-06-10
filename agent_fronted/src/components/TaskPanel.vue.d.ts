import { DefineComponent } from 'vue'
import type { TaskSnapshot } from '@/utils/taskState'

export interface TaskPanelProps {
  taskSnapshot?: Partial<TaskSnapshot> | null
}

const TaskPanel: DefineComponent<TaskPanelProps>

export default TaskPanel
