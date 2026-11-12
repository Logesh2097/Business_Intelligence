<script setup lang="ts">
import { computed } from 'vue'
import { barWidths } from '../lib/chart'

// Horizontal bars, drawn with plain elements: each row is a label, a bar and its value.
const props = defineProps<{
  points: { label: string; value: number }[]
  format: (value: number) => string
  title: string
}>()

const widths = computed(() => barWidths(props.points.map((point) => point.value)))
</script>

<template>
  <div role="img" :aria-label="title" class="bars">
    <div v-for="(point, index) in points" :key="index" class="bar-row">
      <div class="bar-label text-body-2" :title="point.label">{{ point.label }}</div>
      <div class="bar-track">
        <div class="bar-fill" :style="{ width: `${widths[index]}%` }" />
      </div>
      <div class="bar-value text-body-2">{{ format(point.value) }}</div>
    </div>
  </div>
</template>

<style scoped>
.bars {
  display: grid;
  gap: 6px;
}
.bar-row {
  display: grid;
  grid-template-columns: minmax(80px, 28%) 1fr minmax(64px, auto);
  align-items: center;
  gap: 8px;
}
.bar-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bar-track {
  height: 14px;
  border-radius: var(--radius-pill);
  background: var(--fill-2);
}
.bar-fill {
  height: 100%;
  min-width: 2px;
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-chart-1));
}
.bar-value {
  text-align: right;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
  color: var(--ink);
}
.bar-label {
  color: var(--ink-2);
}
</style>
