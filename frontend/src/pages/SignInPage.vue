<script setup lang="ts">
import DataCard from '../components/DataCard.vue'
import PageHeader from '../components/PageHeader.vue'
import { storeToRefs } from 'pinia'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '../stores/session'

const session = useSessionStore()
const router = useRouter()
const { users, loading, error } = storeToRefs(session)
const typed = ref('')

const byCompany = computed(() => {
  const groups = new Map<string, typeof users.value>()
  for (const user of users.value) groups.set(user.company, [...(groups.get(user.company) ?? []), user])
  return [...groups.entries()]
})

async function choose(email: string): Promise<void> {
  if (await session.signIn(email)) await router.replace('/dashboard')
}

onMounted(session.loadUsers)
</script>

<template>
  <div class="screen-backdrop">
    <div class="main-content sign-in">
      <PageHeader title="Choose a user" />
      <v-alert type="info" variant="tonal" density="comfortable" class="mb-4">
        Sign-in with a password is not built yet. In development the app runs as one of the
        demo users below. This page does not exist outside development.
      </v-alert>
      <v-alert v-if="error" type="error" variant="tonal" density="comfortable" class="mb-4">{{ error }}</v-alert>

      <DataCard v-for="[company, people] in byCompany" :key="company" :title="company" flush class="mb-4">
        <v-list density="compact">
          <v-list-item
            v-for="user in people"
            :key="user.email"
            :title="user.email"
            :subtitle="user.role"
            prepend-icon="mdi-account-circle-outline"
            :disabled="loading"
            @click="choose(user.email)"
          />
        </v-list>
      </DataCard>

      <v-form class="d-flex ga-2 align-center" @submit.prevent="choose(typed)">
        <v-text-field
          v-model="typed"
          label="Or type a demo user's email"
          type="email"
          hide-details
        />
        <v-btn type="submit" color="primary" :loading="loading" :disabled="!typed.trim()">Continue</v-btn>
      </v-form>
    </div>
  </div>
</template>

<style scoped>
.sign-in {
  max-width: 640px;
  padding-top: 32px;
}
</style>
