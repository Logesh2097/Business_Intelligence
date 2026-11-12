import { defineStore } from 'pinia'
import { ref } from 'vue'
import { askAgent, fetchAgents } from '../api/bi'
import { messageOf } from '../api/client'
import type { AgentAnswer, AgentInfo } from '../api/types'

export interface Insight {
  id: number
  agent: string
  question: string
  state: 'waiting' | 'done' | 'failed'
  answer: AgentAnswer | null
  error: string
}

/**
 * The Insights page: a question put to one specialist agent, answered with a summary,
 * observations, a table and (when its shape allows) a chart. Kept for this visit only:
 * saving a report is not built in the API yet.
 */
export const useInsightsStore = defineStore('insights', () => {
  const agents = ref<AgentInfo[]>([])
  const loading = ref(false)
  const error = ref('')
  const insights = ref<Insight[]>([])
  let next = 1

  async function loadAgents(): Promise<void> {
    loading.value = true
    error.value = ''
    try {
      agents.value = await fetchAgents()
    } catch (problem) {
      error.value = messageOf(problem)
    } finally {
      loading.value = false
    }
  }

  async function run(agent: string, question: string): Promise<void> {
    const text = question.trim()
    if (text.length < 3 || insights.value.some((insight) => insight.state === 'waiting')) return
    insights.value.unshift({ id: next++, agent, question: text, state: 'waiting', answer: null, error: '' })
    const insight = insights.value[0]
    try {
      insight.answer = await askAgent(agent, text)
      insight.state = 'done'
    } catch (problem) {
      insight.error = messageOf(problem)
      insight.state = 'failed'
    }
  }

  function dismiss(id: number): void {
    insights.value = insights.value.filter((insight) => insight.id !== id)
  }

  return { agents, loading, error, insights, loadAgents, run, dismiss }
})
