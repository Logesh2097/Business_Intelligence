<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Column, Row } from '../api/types'
import { formatValue, isNumeric, titleOf } from '../lib/format'
import { pageOf } from '../lib/paging'
import TablePager from './TablePager.vue'

// A result as a table, five rows a page.
const props = defineProps<{
  columns: Column[]
  rows: Row[]
  currency: string
  truncated?: boolean
}>()

const page = ref(1)
const shown = computed(() => pageOf(props.rows, page.value))

// new rows (another period, a filter) start again at the first page
watch(
  () => props.rows,
  () => (page.value = 1),
)
</script>

<template>
  <div>
    <v-table density="comfortable" class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column.name" :class="{ 'text-right': isNumeric(column) }">
            {{ titleOf(column.name) }}
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, index) in shown" :key="index">
          <td v-for="column in columns" :key="column.name" :class="{ 'text-right': isNumeric(column) }">
            {{ formatValue(row[column.name], column.type, currency) }}
          </td>
        </tr>
      </tbody>
    </v-table>
    <TablePager v-model="page" :total="rows.length" />
    <p v-if="truncated" class="text-caption text-medium-emphasis mt-2 mb-0">
      The result was cut at {{ rows.length }} rows; there are more.
    </p>
  </div>
</template>

<style scoped>
.data-table {
  overflow-x: auto;
}
.data-table td,
.data-table th {
  white-space: nowrap;
}
</style>
