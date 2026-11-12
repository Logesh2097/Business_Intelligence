<script setup lang="ts">
import { computed } from 'vue'
import { clampPage, pageCount, PAGE_SIZE, rangeText } from '../lib/paging'

// The strip under a table: which rows are shown, and the way to the other pages.
// Hidden when everything fits on one page.
const props = withDefaults(defineProps<{ modelValue: number; total: number; size?: number }>(), { size: PAGE_SIZE })
const emit = defineEmits<{ 'update:modelValue': [page: number] }>()

const pages = computed(() => pageCount(props.total, props.size))
const page = computed(() => clampPage(props.modelValue, props.total, props.size))

function go(to: number): void {
  emit('update:modelValue', clampPage(to, props.total, props.size))
}
</script>

<template>
  <div v-if="pages > 1" class="pager">
    <span class="pager__range">{{ rangeText(total, page, size) }}</span>
    <v-btn icon variant="text" size="x-small" aria-label="First page" :disabled="page === 1" @click="go(1)">
      <v-icon icon="mdi-page-first" size="18" />
    </v-btn>
    <v-btn icon variant="text" size="x-small" aria-label="Previous page" :disabled="page === 1" @click="go(page - 1)">
      <v-icon icon="mdi-chevron-left" size="18" />
    </v-btn>
    <span class="pager__page" aria-live="polite">Page {{ page }} of {{ pages }}</span>
    <v-btn icon variant="text" size="x-small" aria-label="Next page" :disabled="page === pages" @click="go(page + 1)">
      <v-icon icon="mdi-chevron-right" size="18" />
    </v-btn>
    <v-btn icon variant="text" size="x-small" aria-label="Last page" :disabled="page === pages" @click="go(pages)">
      <v-icon icon="mdi-page-last" size="18" />
    </v-btn>
  </div>
</template>

<style scoped>
.pager {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 2px;
  padding: 6px 8px;
  border-top: 1px solid var(--hairline-soft);
  font-size: var(--text-meta);
  color: var(--ink-2);
  font-variant-numeric: tabular-nums;
}
.pager__range {
  margin-right: auto;
  padding-left: 4px;
}
.pager__page {
  padding: 0 6px;
  white-space: nowrap;
}
</style>
