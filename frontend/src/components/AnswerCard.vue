<script setup lang="ts">
import { computed } from 'vue'
import type { AssistantAnswer } from '../api/types'
import { routeTitle, statusLook } from '../lib/answer'
import AnswerPart from './AnswerPart.vue'

// The assistant's answer to one question: the sentence, how it was routed, and each part.
const props = defineProps<{ answer: AssistantAnswer; currency: string; seconds: number | null }>()

const look = computed(() => statusLook(props.answer.status))
const many = computed(() => props.answer.results.length > 1)
</script>

<template>
  <v-card variant="flat" border class="answer-card">
    <div class="answer-card__head head-fill">
      <v-chip size="small" class="status-chip" :color="look.color" variant="tonal" :prepend-icon="look.icon">{{ look.label }}</v-chip>
      <span class="text-caption text-medium-emphasis">
        {{ routeTitle(answer.route) }}<template v-if="seconds !== null"> · {{ seconds }} s</template>
      </span>
    </div>
    <div class="answer-card__body">
      <p class="text-body-1 answer-text">{{ answer.summary }}</p>
      <p v-if="answer.reason" class="text-caption text-medium-emphasis mb-2">Why this route: {{ answer.reason }}</p>

      <template v-for="(part, index) in answer.results" :key="index">
        <v-divider class="my-4" />
        <AnswerPart
          :agent="part.agent"
          :question="many ? part.question : undefined"
          :status="part.status"
          :summary="part.summary"
          :insights="part.insights"
          :sql="part.sql"
          :columns="part.columns"
          :rows="part.data"
          :truncated="part.truncated"
          :assumptions="part.assumptions"
          :sources="part.sources"
          :currency="currency"
          :show-summary="many"
        />
      </template>

      <p v-if="answer.scope_note && !answer.summary.includes(answer.scope_note)" class="text-caption text-medium-emphasis mt-3 mb-0">
        {{ answer.scope_note }}
      </p>
    </div>
  </v-card>
</template>

<style scoped>
.answer-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.answer-card__head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px var(--card-pad);
}
.answer-card__body {
  padding: var(--card-pad);
}
</style>
