<script setup lang="ts">
// The top of every page: the title on the page ground, an optional second line, and
// the page's controls on the right (the one primary action lives here, not in the bar).
defineProps<{ title: string; subtitle?: string }>()
</script>

<template>
  <div class="page-header">
    <div class="page-header__left">
      <h1 class="page-header__title">{{ title }}</h1>
      <p v-if="subtitle" class="page-header__sub">{{ subtitle }}</p>
    </div>
    <div v-if="$slots.actions" class="page-header__actions">
      <slot name="actions" />
    </div>
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 16px;
}
.page-header__left {
  min-width: 0;
}
.page-header__title {
  font-family: var(--font-heading) !important;
  font-size: var(--text-title);
  line-height: var(--text-title-lh);
  font-weight: 700;
  letter-spacing: -0.018em;
  margin: 0;
  color: var(--ink);
}
.page-header__sub {
  font-size: var(--text-meta);
  line-height: var(--text-meta-lh);
  color: var(--ink-2);
  margin: 2px 0 0;
  max-width: 78ch;
}
.page-header__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.page-header__actions :deep(.v-input) {
  flex-shrink: 0;
}
.page-header__actions :deep(.v-field) {
  font-size: var(--text-meta);
}
.page-header__actions :deep(.v-field__input) {
  min-height: 36px !important;
  padding-top: 4px !important;
  padding-bottom: 4px !important;
}
@media (max-width: 600px) {
  .page-header {
    flex-direction: column;
    align-items: stretch;
    gap: 8px;
    margin-bottom: 12px;
  }
  .page-header__title {
    font-size: var(--text-section);
    line-height: var(--text-section-lh);
  }
  .page-header__actions {
    width: 100%;
    align-items: stretch;
  }
  .page-header__actions :deep(> .v-btn) {
    flex: 1 1 132px;
    min-width: 0;
  }
  .page-header__actions :deep(> *:not(.v-btn)) {
    flex: 1 1 100%;
    max-width: none !important;
  }
}
</style>
