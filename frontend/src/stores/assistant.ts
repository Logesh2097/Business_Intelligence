import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { askAssistant } from '../api/bi'
import { messageOf } from '../api/client'
import type { AssistantAnswer } from '../api/types'

export interface Exchange {
  id: number
  question: string
  state: 'waiting' | 'done' | 'failed'
  answer: AssistantAnswer | null
  error: string
  seconds: number | null
}

export const MIN_QUESTION = 3
export const MAX_QUESTION = 2000

/**
 * The conversation on the Assistant page. It lives in memory for this visit only:
 * conversations are not stored by the API yet, and each question is answered on its own.
 */
export const useAssistantStore = defineStore('assistant', () => {
  const exchanges = ref<Exchange[]>([])
  const busy = computed(() => exchanges.value.some((exchange) => exchange.state === 'waiting'))
  let next = 1

  function problemWith(question: string): string {
    const text = question.trim()
    if (text.length < MIN_QUESTION) return 'Type a question first.'
    if (text.length > MAX_QUESTION) return `A question can be at most ${MAX_QUESTION} characters.`
    return ''
  }

  async function ask(question: string): Promise<void> {
    const text = question.trim()
    if (busy.value || problemWith(text)) return
    exchanges.value.push({ id: next++, question: text, state: 'waiting', answer: null, error: '', seconds: null })
    const exchange = exchanges.value[exchanges.value.length - 1]
    const started = Date.now()
    try {
      exchange.answer = await askAssistant(text)
      exchange.state = 'done'
    } catch (problem) {
      exchange.error = messageOf(problem)
      exchange.state = 'failed'
    }
    exchange.seconds = Math.round((Date.now() - started) / 100) / 10
  }

  function clear(): void {
    if (!busy.value) exchanges.value = []
  }

  return { exchanges, busy, problemWith, ask, clear }
})
