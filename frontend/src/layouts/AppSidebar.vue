<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import type { NavGroup } from '../lib/nav'

// The rail: text on the card ground, no slab. The active row wears the brand wash and
// its icon the brand as words; the bar above already says the brand once.
const props = defineProps<{
  modelValue: boolean | null
  groups: NavGroup[]
  mini: boolean
  desktop: boolean
}>()
defineEmits<{ 'update:modelValue': [value: boolean] }>()

const route = useRoute()
const rail = computed(() => props.desktop && props.mini)

function isActive(to: string): boolean {
  return route.path === to || route.path.startsWith(`${to}/`)
}
</script>

<template>
  <v-navigation-drawer
    :model-value="modelValue"
    :rail="rail"
    :expand-on-hover="rail"
    :permanent="desktop"
    :temporary="!desktop"
    :location="desktop ? 'left' : 'right'"
    class="sidebar-nav"
    width="240"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <v-list density="compact" nav class="sidebar-list px-2 mt-1">
      <template v-for="(group, index) in groups" :key="group.title">
        <div v-if="index" class="sidebar-divider" />
        <div class="sidebar-subheader">{{ group.title }}</div>
        <v-list-item
          v-for="item in group.items"
          :key="item.to"
          :to="item.to"
          :title="item.title"
          :active="isActive(item.to)"
          rounded="md"
          class="sidebar-item"
        >
          <template #prepend>
            <v-icon :icon="item.icon" size="20" class="sidebar-icon" />
          </template>
        </v-list-item>
      </template>
    </v-list>
    <template #append>
      <div class="sidebar-footer">DataPulse · business intelligence</div>
    </template>
  </v-navigation-drawer>
</template>

<style scoped>
.sidebar-nav {
  background-color: rgb(var(--v-theme-surface)) !important;
  border-right: 1px solid var(--hairline) !important;
}
.sidebar-nav.v-navigation-drawer--right {
  border-right: 0 !important;
  border-left: 1px solid var(--hairline) !important;
  padding-bottom: calc(var(--mobile-nav-h) + env(safe-area-inset-bottom));
  z-index: 1010 !important;
}
.sidebar-divider {
  height: 1px;
  margin: 6px 4px;
  background: var(--hairline);
}
.sidebar-subheader {
  font-size: var(--text-micro);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--ink-2);
  padding: 2px 12px 4px;
  white-space: nowrap;
  overflow: hidden;
}
.sidebar-item {
  color: var(--ink-2) !important;
  min-height: 34px;
  margin-bottom: 2px;
}
.sidebar-item :deep(.v-list-item-title) {
  font-size: var(--text-body);
  font-weight: 500;
  line-height: 1.3;
}
.sidebar-item :deep(.v-list-item__prepend .v-icon) {
  color: var(--ink-3) !important;
  opacity: 1 !important;
}
.sidebar-item :deep(.v-list-item__spacer) {
  width: 12px;
}
.sidebar-item:hover {
  background: var(--fill-1) !important;
  color: var(--ink) !important;
}
.sidebar-item:hover :deep(.v-list-item__prepend .v-icon) {
  color: var(--ink-2) !important;
}
.sidebar-item.v-list-item--active {
  background: rgb(var(--v-theme-wash)) !important;
  color: var(--ink) !important;
}
.sidebar-item.v-list-item--active :deep(.v-list-item-title) {
  font-weight: 600;
}
.sidebar-item.v-list-item--active :deep(.v-list-item__prepend .v-icon) {
  color: var(--text-brand) !important;
}
.sidebar-item :deep(.v-list-item__overlay) {
  opacity: 0 !important;
}
/* folded: icons only, until the pointer rests on the rail */
.v-navigation-drawer--rail:not(.v-navigation-drawer--is-hovering) .sidebar-subheader,
.v-navigation-drawer--rail:not(.v-navigation-drawer--is-hovering) .sidebar-footer {
  visibility: hidden;
  height: 0;
  padding-block: 0;
}
.v-navigation-drawer--rail:not(.v-navigation-drawer--is-hovering) .sidebar-item {
  justify-content: center;
  padding-inline: 8px !important;
}
.v-navigation-drawer--rail:not(.v-navigation-drawer--is-hovering) .sidebar-item :deep(.v-list-item__spacer),
.v-navigation-drawer--rail:not(.v-navigation-drawer--is-hovering) .sidebar-item :deep(.v-list-item__content) {
  display: none;
}
.sidebar-nav.v-navigation-drawer--rail.v-navigation-drawer--is-hovering {
  box-shadow: var(--overlay-shadow) !important;
}
.sidebar-footer {
  padding: 8px 16px;
  color: var(--ink-2);
  font-size: var(--text-micro);
  text-align: center;
  white-space: nowrap;
  overflow: hidden;
}
@media (pointer: coarse) {
  .sidebar-item {
    min-height: var(--control-h);
  }
}
</style>
