<script setup lang="ts">
import { useRoute } from 'vue-router'
import type { NavItem } from '../lib/nav'

// The phone's bottom bar: four screens and "More", which opens the full list.
defineProps<{ tabs: NavItem[]; moreOpen: boolean }>()
defineEmits<{ more: [] }>()

const route = useRoute()
</script>

<template>
  <nav class="mnav" aria-label="Primary">
    <router-link
      v-for="tab in tabs"
      :key="tab.to"
      :to="tab.to"
      class="mnav__tab"
      :class="{ 'mnav__tab--active': route.path.startsWith(tab.to) }"
      :aria-current="route.path.startsWith(tab.to) ? 'page' : undefined"
    >
      <span class="mnav__icon"><v-icon :icon="tab.icon" size="22" /></span>
      <span class="mnav__label">{{ tab.short }}</span>
    </router-link>
    <button type="button" class="mnav__tab" :aria-expanded="moreOpen" aria-label="More screens" @click="$emit('more')">
      <span class="mnav__icon"><v-icon icon="mdi-dots-horizontal" size="22" /></span>
      <span class="mnav__label">More</span>
    </button>
  </nav>
</template>

<style scoped>
.mnav {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 1011;
  display: flex;
  align-items: stretch;
  height: calc(var(--mobile-nav-h) + env(safe-area-inset-bottom));
  padding-bottom: env(safe-area-inset-bottom);
  background: rgb(var(--v-theme-surface));
  border-top: 1px solid var(--hairline);
}
.mnav__tab {
  flex: 1 1 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  padding: 6px 2px;
  border: 0;
  background: none;
  text-decoration: none;
  color: var(--ink-2);
  cursor: pointer;
}
.mnav__tab--active {
  color: rgb(var(--v-theme-primary));
}
.mnav__tab--active .mnav__label {
  color: var(--text-brand);
}
.mnav__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-pill);
  padding: 2px 16px;
  margin-bottom: 2px;
  transition: background-color var(--dur-fast) var(--ease-out);
}
.mnav__tab--active .mnav__icon {
  background: rgba(var(--v-theme-primary), 0.14);
}
.mnav__tab:active .mnav__icon {
  background: var(--fill-2);
}
.mnav__label {
  font-size: var(--text-micro);
  font-weight: 500;
  line-height: 1.1;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
