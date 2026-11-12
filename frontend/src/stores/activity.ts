import { defineStore } from 'pinia'
import { ref } from 'vue'
import { fetchActivity, fetchTools } from '../api/bi'
import { messageOf } from '../api/client'
import type { Activity, ToolInfo } from '../api/types'

export const useActivityStore = defineStore('activity', () => {
  const tools = ref<ToolInfo[]>([])
  const transport = ref('')
  const toolsError = ref('')
  const activity = ref<Activity | null>(null)
  const activityError = ref('')
  const loading = ref(false)

  /** `withLog` is false for roles that may not see queries: the log is then not asked for. */
  async function refresh(withLog: boolean): Promise<void> {
    loading.value = true
    toolsError.value = ''
    activityError.value = ''
    const [listed, log] = await Promise.allSettled([
      fetchTools(),
      withLog ? fetchActivity(100) : Promise.resolve(null),
    ])
    if (listed.status === 'fulfilled') {
      tools.value = listed.value.tools
      transport.value = listed.value.transport
    } else {
      toolsError.value = messageOf(listed.reason)
    }
    if (log.status === 'fulfilled') activity.value = log.value
    else activityError.value = messageOf(log.reason)
    loading.value = false
  }

  return { tools, transport, toolsError, activity, activityError, loading, refresh }
})
