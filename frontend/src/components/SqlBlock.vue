<script setup lang="ts">
// The query behind an answer. Rendered only when the API sent one, which it does only
// for roles that may see SQL.
defineProps<{ sql: string }>()
</script>

<template>
  <details class="sql-fold">
    <summary class="sql-fold__summary">
      <v-icon icon="mdi-code-braces" size="16" class="mr-1" />Query
    </summary>
    <pre class="code-block">{{ sql }}</pre>
  </details>
</template>

<style scoped>
.sql-fold {
  margin-top: var(--space-3);
}
.sql-fold__summary {
  display: inline-flex;
  align-items: center;
  min-height: 32px;
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  font-size: var(--text-meta);
  font-weight: 500;
  color: var(--ink-2);
  cursor: pointer;
  list-style: none;
}
.sql-fold__summary::-webkit-details-marker {
  display: none;
}
.sql-fold__summary:hover {
  background: var(--fill-1);
  color: var(--ink);
}
.sql-fold[open] .sql-fold__summary {
  color: var(--ink);
  margin-bottom: var(--space-15);
}
</style>
