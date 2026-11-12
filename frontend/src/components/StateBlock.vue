<script setup lang="ts">
// The three states every data area has besides its content: loading, failed, empty.
// Loading is a skeleton on the neutral fill; failed and empty are one quiet figure with
// a sentence, and a retry where there is something to retry.
defineProps<{
  loading?: boolean
  error?: string
  empty?: boolean
  emptyText?: string
  skeleton?: string
}>()
defineEmits<{ retry: [] }>()
</script>

<template>
  <v-skeleton-loader v-if="loading" :type="skeleton ?? 'paragraph'" aria-busy="true" aria-label="Loading" />
  <div v-else-if="error" class="state-block" role="alert">
    <v-icon icon="mdi-connection" class="state-block__icon" />
    <p class="state-block__text">{{ error }}</p>
    <v-btn size="small" variant="tonal" prepend-icon="mdi-refresh" @click="$emit('retry')">Try again</v-btn>
  </div>
  <div v-else-if="empty" class="state-block">
    <v-icon icon="mdi-tray" class="state-block__icon" />
    <p class="state-block__text">{{ emptyText ?? 'Nothing to show.' }}</p>
  </div>
  <slot v-else />
</template>

<style scoped>
.state-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 24px 16px;
  text-align: center;
}
.state-block__icon {
  font-size: 2.5rem !important;
  color: var(--ink-3);
}
.state-block__text {
  margin: 0;
  max-width: 44ch;
  font-size: var(--text-body);
  line-height: var(--text-body-lh);
  color: var(--ink-2);
}
</style>
