<script setup lang="ts">
import PageHeader from '../components/PageHeader.vue'
import { storeToRefs } from 'pinia'
import { nextTick, ref, watch } from 'vue'
import AnswerCard from '../components/AnswerCard.vue'
import { MAX_QUESTION, useAssistantStore } from '../stores/assistant'
import { useSessionStore } from '../stores/session'

const assistant = useAssistantStore()
const session = useSessionStore()
const { exchanges, busy } = storeToRefs(assistant)
const { currency } = storeToRefs(session)

const question = ref('')
const problem = ref('')
const end = ref<HTMLElement | null>(null)

const SUGGESTIONS = [
  'What was our total revenue last month?',
  'Which products are low on stock?',
  'How many customers visited last month?',
  'What was our revenue last month and which products are low on stock?',
  'What does our refund policy say?',
]

async function send(text = question.value): Promise<void> {
  problem.value = assistant.problemWith(text)
  if (problem.value || busy.value) return
  question.value = ''
  await assistant.ask(text)
}

function onEnter(event: KeyboardEvent): void {
  if (event.shiftKey) return // Shift+Enter is a new line
  event.preventDefault()
  void send()
}

watch(
  () => exchanges.value.length + (busy.value ? 1 : 0),
  async () => {
    await nextTick()
    end.value?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  },
)
</script>

<template>
  <div class="main-content main-content--narrow d-flex flex-column">
    <PageHeader
      title="Assistant"
      subtitle="Ask about sales, stock, customers, purchasing, expenses, or what is written in your uploaded documents."
    >
      <template v-if="exchanges.length" #actions>
        <v-btn variant="text" size="small" prepend-icon="mdi-broom" :disabled="busy" @click="assistant.clear">Clear</v-btn>
      </template>
    </PageHeader>

    <v-card v-if="!exchanges.length" variant="flat" border class="pa-4 mb-6">
      <p class="t-micro ink-2 mb-2">Try one of these</p>
      <div class="d-flex flex-wrap ga-2">
        <v-chip v-for="suggestion in SUGGESTIONS" :key="suggestion" variant="outlined" @click="send(suggestion)">
          {{ suggestion }}
        </v-chip>
      </div>
      <p class="text-caption text-medium-emphasis mt-3 mb-0">
        Each question is answered on its own; the conversation is not saved yet.
      </p>
    </v-card>

    <div v-for="exchange in exchanges" :key="exchange.id" class="mb-6">
      <div class="d-flex justify-end mb-2">
        <div class="question">{{ exchange.question }}</div>
      </div>
      <v-card v-if="exchange.state === 'waiting'" variant="flat" border class="pa-4">
        <div class="d-flex align-center ga-3">
          <v-progress-circular indeterminate size="20" width="2" color="primary" />
          <span class="text-body-2">Working on it: choosing who answers, then reading the data…</span>
        </div>
      </v-card>
      <v-alert v-else-if="exchange.state === 'failed'" type="error" variant="tonal">
        {{ exchange.error }}
        <template #append>
          <v-btn size="small" variant="text" :disabled="busy" @click="send(exchange.question)">Ask again</v-btn>
        </template>
      </v-alert>
      <AnswerCard v-else-if="exchange.answer" :answer="exchange.answer" :currency="currency" :seconds="exchange.seconds" />
    </div>
    <div ref="end" />

    <v-form class="question-box" @submit.prevent="send()">
      <v-textarea
        v-model="question"
        label="Ask a question"
        rows="2"
        auto-grow
        max-rows="6"
        :counter="MAX_QUESTION"
        :error-messages="problem"
        :disabled="busy"
        hint="Enter to send, Shift+Enter for a new line"
        @keydown.enter="onEnter"
      >
        <template #append-inner>
          <v-btn icon="mdi-send" color="primary" variant="text" type="submit" :loading="busy" aria-label="Send the question" />
        </template>
      </v-textarea>
    </v-form>
  </div>
</template>

<style scoped>
/* the user's words sit on the brand wash: the fill is kept for the one primary action */
.question {
  max-width: 85%;
  padding: 8px 14px;
  border-radius: var(--radius-md) var(--radius-md) var(--radius-xs) var(--radius-md);
  background: rgb(var(--v-theme-wash));
  color: var(--ink);
  font-size: var(--text-body);
  line-height: var(--text-body-lh);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.question-box {
  position: sticky;
  bottom: 0;
  padding-top: 8px;
  background: rgb(var(--v-theme-background));
}
@media (max-width: 959.98px) {
  .question-box {
    bottom: calc(var(--mobile-nav-h) + env(safe-area-inset-bottom));
  }
}
</style>
