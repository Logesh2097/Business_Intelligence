<script setup lang="ts">
import { computed } from 'vue'
import type { Me } from '../api/types'
import { initials } from '../lib/nav'

// The avatar in the bar and what opens under it: who is using the app, the theme, and
// switching user.
const props = defineProps<{ me: Me; email: string; dark: boolean }>()
defineEmits<{ theme: [dark: boolean]; switch: [] }>()

const letters = computed(() => initials(props.email))
</script>

<template>
  <v-menu location="bottom end" :close-on-content-click="false">
    <template #activator="{ props: menu }">
      <button v-bind="menu" type="button" class="user-trigger" :aria-label="`Account: ${email}`">
        <span class="user-avatar">{{ letters }}</span>
      </button>
    </template>
    <v-card min-width="280" max-width="320">
      <div class="user-dropdown-header head-fill">
        <div class="user-dropdown-name">{{ email }}</div>
        <div class="user-dropdown-meta">{{ me.role }} · {{ me.company }}</div>
        <div v-if="me.scope_note" class="user-dropdown-meta">{{ me.scope_note }}</div>
      </div>
      <div class="user-dropdown-theme">
        <span class="user-dropdown-theme__label">Theme</span>
        <v-btn-toggle
          :model-value="dark ? 'dark' : 'light'"
          mandatory
          density="compact"
          variant="outlined"
          divided
          @update:model-value="$emit('theme', $event === 'dark')"
        >
          <v-btn value="light" size="small" aria-label="Light theme"><v-icon icon="mdi-white-balance-sunny" size="18" /></v-btn>
          <v-btn value="dark" size="small" aria-label="Dark theme"><v-icon icon="mdi-weather-night" size="18" /></v-btn>
        </v-btn-toggle>
      </div>
      <v-divider />
      <v-list density="compact" class="py-1">
        <v-list-item title="Switch user" prepend-icon="mdi-account-switch-outline" @click="$emit('switch')" />
      </v-list>
    </v-card>
  </v-menu>
</template>

<style scoped>
.user-trigger {
  border: 0;
  background: none;
  padding: 4px;
  margin-right: 4px;
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.user-trigger:hover {
  background: var(--fill-2);
}
.user-trigger:focus-visible {
  outline: 2px solid rgb(var(--v-theme-primary));
  outline-offset: 2px;
}
.user-avatar {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-wash));
  color: var(--ink);
  font-size: var(--text-meta);
  font-weight: 600;
  letter-spacing: 0.02em;
}
.user-dropdown-header {
  padding: 16px;
}
.user-dropdown-name {
  font-size: var(--text-body);
  font-weight: 600;
  word-break: break-all;
}
.user-dropdown-meta {
  font-size: var(--text-meta);
  color: var(--ink-2);
  margin-top: 2px;
}
.user-dropdown-theme {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 16px;
}
.user-dropdown-theme__label {
  font-size: var(--text-meta);
  font-weight: 500;
}
</style>
