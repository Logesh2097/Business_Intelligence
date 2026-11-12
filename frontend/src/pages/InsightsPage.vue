<script setup lang="ts">
import DataCard from '../components/DataCard.vue'
import PageHeader from '../components/PageHeader.vue'
import { storeToRefs } from 'pinia'
import { computed, onMounted, ref } from 'vue'
import AnswerPart from '../components/AnswerPart.vue'
import StateBlock from '../components/StateBlock.vue'
import { useInsightsStore } from '../stores/insights'
import { useSessionStore } from '../stores/session'

const store = useInsightsStore()
const session = useSessionStore()
const { agents, loading, error, insights } = storeToRefs(store)
const { currency } = storeToRefs(session)

const chosen = ref('')
const question = ref('')
const agent = computed(() => agents.value.find((item) => item.agent === chosen.value) ?? agents.value[0])
const busy = computed(() => insights.value.some((insight) => insight.state === 'waiting'))

async function run(text: string): Promise<void> {
  if (!agent.value) return
  await store.run(agent.value.agent, text)
  question.value = ''
}

onMounted(async () => {
  if (!agents.value.length) await store.loadAgents()
  chosen.value = agents.value[0]?.agent ?? ''
})
</script>

<template>
  <div class="main-content main-content--narrow">
    <PageHeader
      title="Reports and insights"
      subtitle="Put a question to one specialist agent and get a summary, a few observations, a table and, where the result's shape allows, a chart. Results stay on this page for this visit: saving and sharing a report is not built yet."
    />

    <StateBlock :loading="loading" :error="error" :empty="!agents.length" empty-text="No agents are available." @retry="store.loadAgents">
      <DataCard title="Ask an agent" class="mb-6">
        <v-chip-group v-model="chosen" mandatory selected-class="chip-picked" class="mb-2">
          <v-chip v-for="item in agents" :key="item.agent" :value="item.agent" variant="outlined" filter>
            {{ item.title }}
          </v-chip>
        </v-chip-group>
        <template v-if="agent">
          <ul class="pl-5 mb-3">
            <li v-for="(duty, index) in agent.responsibilities" :key="index" class="text-body-2 text-medium-emphasis">
              {{ duty }}
            </li>
          </ul>
          <div class="d-flex flex-wrap ga-2 mb-4">
            <v-chip
              v-for="example in agent.example_questions"
              :key="example"
              size="small"
              variant="tonal"
              :disabled="busy"
              @click="run(example)"
            >
              {{ example }}
            </v-chip>
          </div>
          <v-form class="d-flex ga-2 align-center" @submit.prevent="run(question)">
            <v-text-field
              v-model="question"
              :label="`Ask the ${agent.title.toLowerCase()}`"
              hide-details
              :disabled="busy"
            />
            <v-btn type="submit" color="primary" :loading="busy" :disabled="question.trim().length < 3">Ask</v-btn>
          </v-form>
        </template>
      </DataCard>
    </StateBlock>

    <DataCard v-for="insight in insights" :key="insight.id" :title="insight.question" class="mb-4">
      <template #header>
        <v-btn icon="mdi-close" size="small" variant="text" aria-label="Remove this result" :disabled="insight.state === 'waiting'" @click="store.dismiss(insight.id)" />
      </template>
      <div v-if="insight.state === 'waiting'" class="d-flex align-center ga-3">
        <v-progress-circular indeterminate size="20" width="2" color="primary" />
        <span class="text-body-2">Writing and checking a query, then reading the result…</span>
      </div>
      <v-alert v-else-if="insight.state === 'failed'" type="error" variant="tonal" density="comfortable">
        {{ insight.error }}
      </v-alert>
      <template v-else-if="insight.answer">
        <AnswerPart
          :agent="insight.answer.agent"
          :status="insight.answer.status"
          :summary="insight.answer.summary"
          :insights="insight.answer.insights"
          :sql="insight.answer.sql"
          :columns="insight.answer.columns"
          :rows="insight.answer.data"
          :truncated="insight.answer.truncated"
          :assumptions="insight.answer.assumptions"
          :sources="[]"
          :currency="currency"
        />
        <p v-if="insight.answer.scope_note && !insight.answer.summary.includes(insight.answer.scope_note)" class="text-caption text-medium-emphasis mt-3 mb-0">
          {{ insight.answer.scope_note }}
        </p>
      </template>
    </DataCard>
  </div>
</template>

<style scoped>
/* a chosen chip is green, not brand: a choice reads the same on every screen */
:deep(.chip-picked) {
  color: var(--text-success) !important;
  border-color: var(--text-success) !important;
  font-weight: 600;
}
</style>
