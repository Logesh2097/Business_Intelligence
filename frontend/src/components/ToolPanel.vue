<script setup lang="ts">
import { computed } from 'vue'
import type { Row } from '../api/types'
import { formatDate } from '../lib/format'
import type { Panel } from '../stores/panels'
import DataCard from './DataCard.vue'
import ResultView from './ResultView.vue'
import StateBlock from './StateBlock.vue'

// One data panel: a title, the period it covers, a chart and table, and its notes.
const props = withDefaults(
  defineProps<{
    title: string
    panel: Panel
    currency: string
    chart?: boolean
    emptyText?: string
    filter?: (row: Row) => boolean
  }>(),
  { chart: true, emptyText: 'No data for this period.', filter: undefined },
)
defineEmits<{ retry: [] }>()

const rows = computed(() => {
  const all = props.panel.result?.rows ?? []
  return props.filter ? all.filter(props.filter) : all
})
const period = computed(() => {
  const covered = props.panel.result?.period
  return covered ? `${formatDate(covered.start)} to ${formatDate(covered.end)}` : ''
})
</script>

<template>
  <DataCard :title="title" :subtitle="period || undefined">
    <StateBlock
      :loading="panel.loading"
      :error="panel.error"
      :empty="!panel.loading && !panel.error && rows.length === 0"
      :empty-text="emptyText"
      skeleton="list-item-two-line@3"
      @retry="$emit('retry')"
    >
      <ResultView
        v-if="panel.result"
        :columns="panel.result.columns"
        :rows="rows"
        :currency="currency"
        :truncated="panel.result.truncated"
        :chart="chart"
      />
      <p v-for="(note, index) in panel.result?.notes ?? []" :key="index" class="text-caption text-medium-emphasis mt-2 mb-0">
        {{ note }}
      </p>
    </StateBlock>
  </DataCard>
</template>
