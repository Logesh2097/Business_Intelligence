import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { fetchDemoUsers, fetchMe } from '../api/bi'
import { devUser, messageOf, setDevUser } from '../api/client'
import type { DemoUser, Me } from '../api/types'

/** Who is using the app. Sign-in is not built: the user is a demo user chosen in development. */
export const useSessionStore = defineStore('session', () => {
  const email = ref(devUser())
  const me = ref<Me | null>(null)
  const users = ref<DemoUser[]>([])
  const loading = ref(false)
  const error = ref('')

  const signedIn = computed(() => me.value !== null)
  const currency = computed(() => me.value?.currency ?? 'INR')

  function can(permission: string): boolean {
    return me.value?.permissions.includes(permission) ?? false
  }

  async function loadUsers(): Promise<void> {
    try {
      users.value = await fetchDemoUsers()
    } catch (problem) {
      users.value = []
      error.value = messageOf(problem)
    }
  }

  /** Read who the API says we are. False when there is no user or the API refuses. */
  async function restore(): Promise<boolean> {
    if (!email.value) return false
    loading.value = true
    error.value = ''
    try {
      me.value = await fetchMe()
      return true
    } catch (problem) {
      me.value = null
      error.value = messageOf(problem)
      return false
    } finally {
      loading.value = false
    }
  }

  async function signIn(chosen: string): Promise<boolean> {
    email.value = chosen.trim().toLowerCase()
    setDevUser(email.value)
    return restore()
  }

  function signOut(): void {
    email.value = ''
    me.value = null
    setDevUser('')
  }

  return { email, me, users, loading, error, signedIn, currency, can, loadUsers, restore, signIn, signOut }
})
