<script setup lang="ts">
import { PERIODS } from '../lib/answer'

// The period a page covers. Periods are named ranges the server turns into dates;
// a free date range is not offered because the API does not take one.
defineProps<{ modelValue: string; disabled?: boolean }>()
defineEmits<{ 'update:modelValue': [value: string] }>()
</script>

<template>
  <v-select
    :model-value="modelValue"
    :items="PERIODS"
    item-title="title"
    item-value="value"
    label="Period"
    density="compact"
    hide-details
    :disabled="disabled"
    class="filter-input"
    @update:model-value="$emit('update:modelValue', $event)"
  />
</template>
