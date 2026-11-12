import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { fetchHealth, fetchReady, type Health, type Ready } from '../api/health'
import { storeRows, summary } from '../lib/status'

/** What the API says about itself and its stores. Pages read this; they do not call the API. */
export const useStatusStore = defineStore('status', () => {
  const loading = ref(false)
  const health = ref<Health | null>(null)
  const ready = ref<Ready | null>(null)
  const checkedAt = ref<Date | null>(null)

  const rows = computed(() => storeRows(ready.value))
  const overall = computed(() => summary(health.value !== null, rows.value))

  async function refresh(): Promise<void> {
    loading.value = true
    // Two separate calls: the API can be up while a store is down.
    const [h, r] = await Promise.allSettled([fetchHealth(), fetchReady()])
    health.value = h.status === 'fulfilled' ? h.value : null
    ready.value = r.status === 'fulfilled' ? r.value : null
    checkedAt.value = new Date()
    loading.value = false
  }

  return { loading, health, ready, checkedAt, rows, overall, refresh }
})
