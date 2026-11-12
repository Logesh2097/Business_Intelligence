<script setup lang="ts">
import PageHeader from '../components/PageHeader.vue'
import { storeToRefs } from 'pinia'
import { computed, onMounted } from 'vue'
import StatusRow from '../components/StatusRow.vue'
import { useStatusStore } from '../stores/status'

const status = useStatusStore()
const { loading, health, rows, overall, checkedAt } = storeToRefs(status)

const apiDetail = computed(() =>
  health.value
    ? `Version ${health.value.version}, environment ${health.value.env}`
    : 'Not reachable. Start it with scripts/dev.sh',
)

onMounted(status.refresh)
</script>

<template>
  <div class="main-content status-page">
    <PageHeader title="Status">
      <template #actions>
        <v-btn variant="tonal" size="small" prepend-icon="mdi-refresh" :loading="loading" @click="status.refresh">
          Check again
        </v-btn>
      </template>
    </PageHeader>

    <v-skeleton-loader v-if="!checkedAt" type="list-item-two-line@4" />

    <template v-else>
      <v-alert
        :type="overall.ok ? 'success' : 'warning'"
        variant="tonal"
        class="mb-4"
        :text="overall.text"
      />

      <v-card variant="flat" border>
        <v-list lines="three">
          <StatusRow name="API" :detail="apiDetail" :ok="health !== null" />
          <template v-for="row in rows" :key="row.key">
            <v-divider />
            <StatusRow :name="row.name" :detail="`${row.purpose}. ${row.detail}`" :ok="row.ok" />
          </template>
        </v-list>
      </v-card>

      <p class="text-caption text-medium-emphasis mt-3">
        Checked at {{ checkedAt.toLocaleTimeString() }}
      </p>
    </template>
  </div>
</template>

<style scoped>
.status-page {
  max-width: 760px;
}
</style>
