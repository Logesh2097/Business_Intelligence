<script setup lang="ts">
import { computed } from 'vue'
import { linePath } from '../lib/chart'

// A line over time from zero, with the first and last label and the largest value named.
const props = defineProps<{
  points: { label: string; value: number }[]
  format: (value: number) => string
  formatLabel: (label: string) => string
  title: string
}>()

const WIDTH = 600
const HEIGHT = 160
const values = computed(() => props.points.map((point) => point.value))
const path = computed(() => linePath(values.value, WIDTH, HEIGHT))
const largest = computed(() => Math.max(...values.value, 0))
</script>

<template>
  <div>
    <div class="text-caption text-medium-emphasis">Highest: {{ format(largest) }}</div>
    <svg :viewBox="`0 0 ${WIDTH} ${HEIGHT}`" role="img" :aria-label="title" class="line" preserveAspectRatio="none">
      <line x1="0" :y1="HEIGHT - 4" :x2="WIDTH" :y2="HEIGHT - 4" class="axis" />
      <path :d="path" class="stroke" />
    </svg>
    <div class="d-flex justify-space-between text-caption text-medium-emphasis">
      <span>{{ formatLabel(points[0]?.label ?? '') }}</span>
      <span>{{ formatLabel(points[points.length - 1]?.label ?? '') }}</span>
    </div>
  </div>
</template>

<style scoped>
.line {
  width: 100%;
  height: 160px;
  display: block;
}
.axis {
  stroke: var(--hairline-strong);
  stroke-width: 1;
}
.stroke {
  fill: none;
  stroke: rgb(var(--v-theme-chart-1));
  stroke-width: 2;
  vector-effect: non-scaling-stroke;
}
</style>
