import { defineStore } from 'pinia'
import { reactive, ref } from 'vue'
import { runTool } from '../api/bi'
import { messageOf } from '../api/client'
import type { ToolResult } from '../api/types'

export interface Panel {
  loading: boolean
  error: string
  result: ToolResult | null
}

/**
 * The data panels of the Dashboard and Analytics pages. Each panel is one tool call
 * (docs/api.md §9b); a panel that fails says so and the others still show.
 */
export const usePanelsStore = defineStore('panels', () => {
  const period = ref('last_month')
  const panels = reactive<Record<string, Panel>>({})

  function panel(key: string): Panel {
    if (!panels[key]) panels[key] = { loading: false, error: '', result: null }
    return panels[key]
  }

  async function load(key: string, tool: string, args: Record<string, unknown> = {}): Promise<void> {
    const target = panel(key)
    target.loading = true
    target.error = ''
    try {
      target.result = await runTool(tool, args)
    } catch (problem) {
      target.result = null
      target.error = messageOf(problem)
    } finally {
      target.loading = false
    }
  }

  async function loadAll(requests: { key: string; tool: string; args?: Record<string, unknown> }[]): Promise<void> {
    await Promise.all(requests.map((request) => load(request.key, request.tool, request.args ?? {})))
  }

  function reset(): void {
    for (const key of Object.keys(panels)) delete panels[key]
  }

  return { period, panels, panel, load, loadAll, reset }
})
