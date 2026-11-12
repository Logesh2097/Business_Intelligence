<script setup lang="ts">
import PageHeader from '../components/PageHeader.vue'
import { storeToRefs } from 'pinia'
import { computed, onMounted, watch } from 'vue'
import PeriodSelect from '../components/PeriodSelect.vue'
import StatTile from '../components/StatTile.vue'
import StateBlock from '../components/StateBlock.vue'
import ToolPanel from '../components/ToolPanel.vue'
import { tilesOf } from '../lib/format'
import { usePanelsStore } from '../stores/panels'
import { useSessionStore } from '../stores/session'

const panels = usePanelsStore()
const session = useSessionStore()
const { period } = storeToRefs(panels)
const { currency, me } = storeToRefs(session)

const summary = computed(() => panels.panel('dash.summary'))
const ICONS: Record<string, string> = {
  revenue: 'mdi-cash-multiple',
  orders: 'mdi-receipt-text-outline',
  average_order_value: 'mdi-scale-balance',
  units_sold: 'mdi-package-variant-closed',
  visits: 'mdi-walk',
  visiting_customers: 'mdi-account-group-outline',
  new_customers: 'mdi-account-plus-outline',
  buying_customers: 'mdi-account-cash-outline',
  low_stock_lines: 'mdi-alert-outline',
  out_of_stock_lines: 'mdi-package-variant-remove',
  expenses: 'mdi-cash-minus',
  purchases_received_amount: 'mdi-truck-delivery-outline',
}
const SHOWN = Object.keys(ICONS)

const tiles = computed(() => {
  const result = summary.value.result
  if (!result) return []
  return tilesOf(result.columns, result.rows[0], currency.value)
    .filter((tile) => SHOWN.includes(tile.key))
    .map((tile) => ({ ...tile, icon: ICONS[tile.key] }))
})

function requests(): { key: string; tool: string; args?: Record<string, unknown> }[] {
  const chosen = { period: period.value }
  return [
    { key: 'dash.summary', tool: 'business_summary', args: chosen },
    { key: 'dash.trend', tool: 'sales_trend', args: chosen },
    { key: 'dash.outlets', tool: 'revenue_by_outlet', args: chosen },
    { key: 'dash.products', tool: 'top_products', args: { ...chosen, limit: 8 } },
    { key: 'dash.stock', tool: 'inventory_status', args: { only_low: true, limit: 10 } },
  ]
}

function load(): Promise<void> {
  return panels.loadAll(requests())
}
function reload(key: string): void {
  const request = requests().find((item) => item.key === key)
  if (request) void panels.load(request.key, request.tool, request.args)
}

onMounted(load)
watch(period, load)
</script>

<template>
  <div class="main-content">
    <PageHeader title="Dashboard" :subtitle="me?.scope_note ?? undefined">
      <template #actions>
        <PeriodSelect v-model="period" />
        <v-btn variant="tonal" size="small" prepend-icon="mdi-refresh" :loading="summary.loading" @click="load">
          Refresh
        </v-btn>
      </template>
    </PageHeader>

    <StateBlock
      :loading="summary.loading && !summary.result"
      :error="summary.error"
      skeleton="heading@4"
      @retry="reload('dash.summary')"
    >
      <div class="tile-grid">
        <StatTile v-for="tile in tiles" :key="tile.key" :label="tile.label" :value="tile.value" :icon="tile.icon" />
      </div>
      <p v-for="(note, index) in summary.result?.notes ?? []" :key="index" class="text-caption text-medium-emphasis mt-2 mb-0">
        {{ note }}
      </p>
    </StateBlock>

    <v-row class="mt-1">
      <v-col cols="12">
        <ToolPanel title="Revenue by day" :panel="panels.panel('dash.trend')" :currency="currency" @retry="reload('dash.trend')" />
      </v-col>
      <v-col cols="12">
        <ToolPanel title="Revenue by outlet" :panel="panels.panel('dash.outlets')" :currency="currency" @retry="reload('dash.outlets')" />
      </v-col>
      <v-col cols="12">
        <ToolPanel title="Top products" :panel="panels.panel('dash.products')" :currency="currency" :chart="false" @retry="reload('dash.products')" />
      </v-col>
      <v-col cols="12">
        <ToolPanel
          title="Low stock now"
          :panel="panels.panel('dash.stock')"
          :currency="currency"
          :chart="false"
          empty-text="Nothing is at or below its reorder level."
          @retry="reload('dash.stock')"
        />
      </v-col>
    </v-row>
  </div>
</template>

<style scoped>
.tile-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: var(--space-3);
}
@media (max-width: 600px) {
  .tile-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: var(--space-2);
  }
}
</style>
