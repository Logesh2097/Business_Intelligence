<script setup lang="ts">
import type { Source } from '../api/types'

// The document passages an answer cites: [n], the file, the page, and the quoted lines.
defineProps<{ sources: Source[] }>()
</script>

<template>
  <div class="mt-3">
    <div class="t-micro ink-2 mb-1">Sources</div>
    <v-list density="compact" lines="three" class="pa-0">
      <v-list-item v-for="source in sources" :key="source.number" class="px-0">
        <template #prepend>
          <span class="source-number">{{ source.number }}</span>
        </template>
        <v-list-item-title class="text-body-2 font-weight-medium">
          {{ source.title }}
          <span class="text-medium-emphasis font-weight-regular">
            · {{ source.filename }}<template v-if="source.page"> · page {{ source.page }}</template>
          </span>
        </v-list-item-title>
        <div class="text-body-2 text-medium-emphasis quote">“{{ source.quote }}”</div>
      </v-list-item>
    </v-list>
  </div>
</template>

<style scoped>
.quote {
  white-space: normal;
  overflow-wrap: anywhere;
}
.source-number {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 24px;
  height: 24px;
  margin-right: var(--space-3);
  border-radius: var(--radius-xs);
  background: rgb(var(--v-theme-wash));
  color: var(--ink);
  font-size: var(--text-meta);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
</style>
