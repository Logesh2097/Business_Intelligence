<script setup lang="ts">
// A card with a head: the title on the brand wash, the content on the card ground.
// One construction for every titled card in the app. Flat, with a hairline.
defineProps<{ title?: string; subtitle?: string; sub?: boolean; flush?: boolean }>()
</script>

<template>
  <v-card variant="flat" border class="data-card">
    <div v-if="title || subtitle || $slots.header" class="data-card__head" :class="sub ? 'head-sub' : 'head-fill'">
      <div class="data-card__heading">
        <h2 v-if="title" class="data-card__title">{{ title }}</h2>
        <div v-if="subtitle" class="data-card__subtitle">{{ subtitle }}</div>
      </div>
      <slot name="header" />
    </div>
    <div :class="flush ? 'data-card__body--flush' : 'data-card__body'">
      <slot />
    </div>
  </v-card>
</template>

<style scoped>
.data-card {
  height: 100%;
}
.data-card__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  padding: 12px var(--card-pad);
}
.data-card__heading {
  flex: 1 1 160px;
  min-width: 0;
}
.data-card__title {
  font-family: var(--font-heading);
  font-weight: 600;
  font-size: var(--text-body);
  line-height: var(--text-body-lh);
  letter-spacing: -0.01em;
  margin: 0;
  color: var(--ink);
}
.data-card__subtitle {
  font-size: var(--text-meta);
  color: var(--ink-2);
  margin-top: 2px;
}
.data-card__body {
  padding: var(--card-pad);
}
@media (max-width: 600px) {
  .data-card__body {
    padding: var(--space-3);
  }
}
</style>
