<script setup lang="ts">
import DataCard from '../components/DataCard.vue'
import PageHeader from '../components/PageHeader.vue'
import { storeToRefs } from 'pinia'
import { computed, onMounted, ref } from 'vue'
import StateBlock from '../components/StateBlock.vue'
import TablePager from '../components/TablePager.vue'
import { pageOf } from '../lib/paging'
import { statusLook } from '../lib/answer'
import { formatDate, formatNumber } from '../lib/format'
import { useActivityStore } from '../stores/activity'
import { useSessionStore } from '../stores/session'

const store = useActivityStore()
const session = useSessionStore()
const { tools, transport, toolsError, activity, activityError, loading } = storeToRefs(store)

const maySeeLog = computed(() => session.can('sql:view'))
const open = ref('') // the id of the entry whose query is shown
const page = ref(1)
const entries = computed(() => activity.value?.entries ?? [])
const shownEntries = computed(() => pageOf(entries.value, page.value))

const GROUPS: Record<string, string> = {
  business: 'Business figures',
  catalog: 'What the data means',
  query: 'Queries',
  documents: 'Documents',
}
const grouped = computed(() =>
  Object.entries(GROUPS)
    .map(([key, title]) => ({ title, tools: tools.value.filter((tool) => tool.group === key) }))
    .filter((group) => group.tools.length),
)
const counts = computed(() => Object.entries(activity.value?.last_24_hours ?? {}))

function refresh(): Promise<void> {
  return store.refresh(maySeeLog.value)
}

onMounted(refresh)
</script>

<template>
  <div class="main-content">
    <PageHeader title="Agent and tool activity">
      <template #actions>
        <v-btn variant="tonal" size="small" prepend-icon="mdi-refresh" :loading="loading" @click="refresh">Refresh</v-btn>
      </template>
    </PageHeader>

    <h2 class="t-section mb-2">Recent queries</h2>
    <v-alert v-if="!maySeeLog" type="info" variant="tonal" density="comfortable" class="mb-6">
      The log of queries is shown to analysts and administrators. Which agent answered each of
      your own questions, and how it ended, is shown with every answer on the Assistant page.
    </v-alert>
    <template v-else>
      <div v-if="counts.length" class="d-flex flex-wrap ga-2 mb-3">
        <v-chip v-for="[status, count] in counts" :key="status" size="small" class="status-chip" variant="tonal" :color="statusLook(status).color">
          {{ statusLook(status).label }}: {{ formatNumber(count) }} in 24 hours
        </v-chip>
      </div>
      <StateBlock
        :loading="loading && !activity"
        :error="activityError"
        :empty="!!activity && activity.entries.length === 0"
        empty-text="No query has been run yet."
        skeleton="table-row@5"
        class="mb-6"
        @retry="refresh"
      >
        <v-table density="comfortable" class="table-card mb-6">
          <thead>
            <tr>
              <th>When</th>
              <th>Who</th>
              <th>Question</th>
              <th>Outcome</th>
              <th class="text-right">Rows</th>
              <th class="text-right">Time</th>
              <th />
            </tr>
          </thead>
          <tbody>
            <template v-for="entry in shownEntries" :key="entry.id">
              <tr>
                <td class="text-no-wrap">{{ formatDate(entry.created_at) }}</td>
                <td>{{ entry.asked_by }}</td>
                <td class="question">{{ entry.question }}</td>
                <td>
                  <v-chip size="small" class="status-chip" variant="tonal" :color="statusLook(entry.status).color" :prepend-icon="statusLook(entry.status).icon">
                    {{ statusLook(entry.status).label }}
                  </v-chip>
                  <span v-if="entry.reason" class="text-caption text-medium-emphasis ml-1">{{ entry.reason }}</span>
                </td>
                <td class="text-right">{{ entry.row_count ?? '—' }}</td>
                <td class="text-right text-no-wrap">{{ entry.duration_ms === null ? '—' : `${entry.duration_ms} ms` }}</td>
                <td class="text-right">
                  <v-btn v-if="entry.sql" size="small" variant="text" @click="open = open === entry.id ? '' : entry.id">
                    {{ open === entry.id ? 'Hide' : 'Query' }}
                  </v-btn>
                </td>
              </tr>
              <tr v-if="open === entry.id && entry.sql">
                <td colspan="7" class="py-2"><pre class="code-block">{{ entry.sql }}</pre></td>
              </tr>
            </template>
          </tbody>
          <template #bottom>
            <TablePager v-model="page" :total="entries.length" />
          </template>
        </v-table>
      </StateBlock>
    </template>

    <h2 class="t-section mb-1">Tools</h2>
    <p class="text-body-2 text-medium-emphasis mb-3">
      What the agents and this app may call to read data. Every tool is read-only and answers
      for your company and outlets only.
      <template v-if="transport">Agents reach tools: {{ transport === 'mcp' ? 'through the MCP server' : 'in process' }}.</template>
    </p>
    <StateBlock :loading="loading && !tools.length" :error="toolsError" :empty="!loading && !tools.length" empty-text="No tools are available to you." @retry="refresh">
      <DataCard v-for="group in grouped" :key="group.title" :title="group.title" flush class="mb-4">
        <v-list density="compact" lines="three">
          <v-list-item v-for="tool in group.tools" :key="tool.name">
            <v-list-item-title class="font-weight-medium">
              {{ tool.title }} <span class="text-caption text-medium-emphasis">{{ tool.name }}</span>
            </v-list-item-title>
            <div class="text-body-2 text-medium-emphasis description">{{ tool.description }}</div>
          </v-list-item>
        </v-list>
      </DataCard>
    </StateBlock>
  </div>
</template>

<style scoped>
.question {
  max-width: 320px;
  overflow-wrap: anywhere;
}
.description {
  white-space: normal;
}
</style>
