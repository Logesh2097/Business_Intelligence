<script setup lang="ts">
// One line of the Status page: a name, what it is for, and whether it is running.
defineProps<{
  name: string
  detail: string
  ok: boolean
}>()
</script>

<template>
  <v-list-item :title="name" :subtitle="detail">
    <template #prepend>
      <v-icon
        :icon="ok ? 'mdi-check-circle' : 'mdi-alert-circle'"
        :color="ok ? 'success' : 'error'"
      />
    </template>
    <template #append>
      <span class="text-body-2">{{ ok ? 'Running' : 'Down' }}</span>
    </template>
  </v-list-item>
</template>
