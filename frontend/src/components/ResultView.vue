<script setup lang="ts">
import { computed } from 'vue'
import type { Column, Row } from '../api/types'
import { chartFor } from '../lib/chart'
import { formatDate, formatValue, titleOf } from '../lib/format'
import BarChart from './BarChart.vue'
import DataTable from './DataTable.vue'
import LineChart from './LineChart.vue'

// A result as a chart (only when its shape allows one) and always as a table.
// `chart` defaults to true on purpose: Vue reads an absent boolean prop as false.
const props = withDefaults(
  defineProps<{
    columns: Column[]
    rows: Row[]
    currency: string
    truncated?: boolean
    chart?: boolean
  }>(),
  { chart: true, truncated: false },
)

const spec = computed(() => (props.chart ? chartFor(props.columns, props.rows) : null))
const title = computed(() =>
  spec.value ? `${titleOf(spec.value.value.name)} by ${titleOf(spec.value.label.name).toLowerCase()}` : '',
)

function written(value: number): string {
  return spec.value ? formatValue(value, spec.value.value.type, props.currency) : String(value)
}
</script>

<template>
  <div>
    <div v-if="spec" class="mb-4">
      <div class="text-caption text-medium-emphasis mb-1">{{ title }}</div>
      <LineChart
        v-if="spec.kind === 'line'"
        :points="spec.points"
        :format="written"
        :format-label="formatDate"
        :title="title"
      />
      <BarChart v-else :points="spec.points" :format="written" :title="title" />
    </div>
    <DataTable :columns="columns" :rows="rows" :currency="currency" :truncated="truncated" />
  </div>
</template>
