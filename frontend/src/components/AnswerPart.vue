<script setup lang="ts">
import type { Column, Row, Source } from '../api/types'
import { agentTitle, statusLook } from '../lib/answer'
import ResultView from './ResultView.vue'
import SourceList from './SourceList.vue'
import SqlBlock from './SqlBlock.vue'

// One agent's part of an answer: what it was asked, what it said, and what it stands on.
// `showSummary` defaults to true on purpose: Vue reads an absent boolean prop as false.
withDefaults(
  defineProps<{
    agent: string
    question?: string
    status: string
    summary: string
    insights: string[]
    sql: string | null
    columns: Column[]
    rows: Row[]
    truncated: boolean
    assumptions: string[]
    sources: Source[]
    currency: string
    showSummary?: boolean
  }>(),
  { showSummary: true, question: undefined },
)
</script>

<template>
  <div>
    <div class="d-flex align-center flex-wrap ga-2 mb-2">
      <v-chip size="small" variant="tonal" prepend-icon="mdi-robot-outline">
        {{ agentTitle(agent) }}
      </v-chip>
      <v-chip size="small" class="status-chip" :color="statusLook(status).color" variant="tonal" :prepend-icon="statusLook(status).icon">
        {{ statusLook(status).label }}
      </v-chip>
    </div>
    <p v-if="question" class="text-caption text-medium-emphasis mb-1">Asked: {{ question }}</p>
    <p v-if="showSummary" class="text-body-1 answer-text">{{ summary }}</p>

    <ul v-if="insights.length" class="pl-5 mb-3">
      <li v-for="(insight, index) in insights" :key="index" class="text-body-2">{{ insight }}</li>
    </ul>

    <ResultView
      v-if="rows.length"
      :columns="columns"
      :rows="rows"
      :currency="currency"
      :truncated="truncated"
    />
    <SourceList v-if="sources.length" :sources="sources" />

    <div v-if="assumptions.length" class="mt-3">
      <div class="t-micro ink-2 mb-1">How this was read</div>
      <ul class="pl-5 mb-0">
        <li v-for="(assumption, index) in assumptions" :key="index" class="text-body-2 text-medium-emphasis">
          {{ assumption }}
        </li>
      </ul>
    </div>
    <SqlBlock v-if="sql" :sql="sql" />
  </div>
</template>

<style scoped>
.answer-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
