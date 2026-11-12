<script setup lang="ts">
// One headline figure, already written out by the caller. A neutral card; the only
// colour is the icon, in the brand as words.
defineProps<{ label: string; value: string; icon?: string }>()
</script>

<template>
  <div class="rtile">
    <div class="rtile__head">
      <v-icon v-if="icon" :icon="icon" size="18" class="rtile__icon" />
      <span class="rtile__label">{{ label }}</span>
    </div>
    <div class="rtile__value">{{ value }}</div>
  </div>
</template>

<style scoped>
.rtile {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-height: var(--tile-h);
  height: 100%;
  padding: 12px var(--card-pad);
  min-width: 0;
  border-radius: var(--radius-md);
  border: 1px solid var(--hairline);
  background: rgb(var(--v-theme-surface));
  color: var(--ink);
}
.rtile__head {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.rtile__icon {
  color: var(--text-brand);
  flex-shrink: 0;
}
.rtile__label {
  font-size: var(--text-meta);
  font-weight: 500;
  color: var(--ink-2);
  min-width: 0;
  overflow: hidden;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  line-clamp: 2;
}
.rtile__value {
  margin-top: auto;
  font-family: var(--font-heading);
  font-size: var(--text-title);
  font-weight: 600;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em;
  overflow-wrap: anywhere;
}
@media (max-width: 600px) {
  .rtile {
    padding: 8px 12px;
  }
  .rtile__value {
    font-size: var(--text-section);
  }
}
</style>
