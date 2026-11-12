<script setup lang="ts">
import PageHeader from '../components/PageHeader.vue'
import { storeToRefs } from 'pinia'
import { computed, onMounted, ref, watch } from 'vue'
import type { Row } from '../api/types'
import PeriodSelect from '../components/PeriodSelect.vue'
import ToolPanel from '../components/ToolPanel.vue'
import { usePanelsStore } from '../stores/panels'
import { useSessionStore } from '../stores/session'

const panels = usePanelsStore()
const session = useSessionStore()
const { period } = storeToRefs(panels)
const { currency } = storeToRefs(session)

const tab = ref('sales')
const outlet = ref<string | null>(null)

type Request = { key: string; tool: string; args?: Record<string, unknown> }

function requests(area: string): Request[] {
  const chosen = { period: period.value }
  if (area === 'sales') {
    return [
      { key: 'an.sales', tool: 'sales_summary', args: chosen },
      { key: 'an.trend', tool: 'sales_trend', args: chosen },
      { key: 'an.outlets', tool: 'revenue_by_outlet', args: chosen },
      { key: 'an.units', tool: 'top_products', args: { ...chosen, by: 'units', limit: 15 } },
      { key: 'an.revenue', tool: 'top_products', args: { ...chosen, by: 'revenue', limit: 15 } },
    ]
  }
  if (area === 'inventory') {
    return [
      { key: 'an.low', tool: 'inventory_status', args: { only_low: true, limit: 200 } },
      { key: 'an.stock', tool: 'inventory_status', args: { only_low: false, limit: 200 } },
      { key: 'an.outlets', tool: 'revenue_by_outlet', args: chosen }, // for the outlet filter
    ]
  }
  if (area === 'customers') {
    return [
      { key: 'an.customers', tool: 'customer_insights', args: chosen },
      { key: 'an.segments', tool: 'visits_by_segment', args: chosen },
    ]
  }
  return [{ key: 'an.overview', tool: 'business_summary', args: chosen }]
}

function load(): Promise<void> {
  return panels.loadAll(requests(tab.value))
}
function reload(key: string): void {
  const request = requests(tab.value).find((item) => item.key === key)
  if (request) void panels.load(request.key, request.tool, request.args)
}

// The outlets the caller may see, read from a result: there is no outlet argument to send.
const outlets = computed(() =>
  (panels.panel('an.outlets').result?.rows ?? []).map((row) => String(row.outlet)).sort(),
)
function inOutlet(row: Row): boolean {
  return outlet.value === null || row.outlet === outlet.value
}

onMounted(load)
watch([period, tab], load)
</script>

<template>
  <div class="main-content">
    <PageHeader title="Analytics">
      <template #actions>
        <PeriodSelect v-if="tab !== 'inventory'" v-model="period" />
        <v-select
          v-if="tab === 'inventory' || tab === 'sales'"
          v-model="outlet"
          :items="outlets"
          label="Outlet"
          clearable
          density="compact"
          hide-details
          class="filter-input"
        />
      </template>
    </PageHeader>

    <v-tabs v-model="tab" class="mb-4" show-arrows>
      <v-tab value="sales">Sales</v-tab>
      <v-tab value="inventory">Inventory</v-tab>
      <v-tab value="customers">Customers</v-tab>
      <v-tab value="overview">Across areas</v-tab>
    </v-tabs>

    <v-row v-if="tab === 'sales'">
      <v-col cols="12">
        <ToolPanel title="Sales summary" :panel="panels.panel('an.sales')" :currency="currency" :chart="false" @retry="reload('an.sales')" />
      </v-col>
      <v-col cols="12">
        <ToolPanel title="Revenue by day" :panel="panels.panel('an.trend')" :currency="currency" @retry="reload('an.trend')" />
      </v-col>
      <v-col cols="12">
        <ToolPanel title="Revenue by outlet" :panel="panels.panel('an.outlets')" :currency="currency" :filter="inOutlet" @retry="reload('an.outlets')" />
      </v-col>
      <v-col cols="12">
        <ToolPanel title="Top products by units" :panel="panels.panel('an.units')" :currency="currency" :chart="false" @retry="reload('an.units')" />
      </v-col>
      <v-col cols="12">
        <ToolPanel title="Top products by revenue" :panel="panels.panel('an.revenue')" :currency="currency" :chart="false" @retry="reload('an.revenue')" />
      </v-col>
      <v-col cols="12">
        <p class="text-caption text-medium-emphasis mb-0">
          The outlet filter narrows the "Revenue by outlet" panel only. The other sales panels
          cover every outlet you may see: the data tools take a period, not an outlet.
        </p>
      </v-col>
    </v-row>

    <v-row v-else-if="tab === 'inventory'">
      <v-col cols="12">
        <ToolPanel
          title="At or below the reorder level"
          :panel="panels.panel('an.low')"
          :currency="currency"
          :chart="false"
          :filter="inOutlet"
          empty-text="Nothing is at or below its reorder level."
          @retry="reload('an.low')"
        />
      </v-col>
      <v-col cols="12">
        <ToolPanel title="All stock lines" :panel="panels.panel('an.stock')" :currency="currency" :chart="false" :filter="inOutlet" @retry="reload('an.stock')" />
      </v-col>
      <v-col cols="12">
        <p class="text-caption text-medium-emphasis mb-0">Stock is as of now; it has no period.</p>
      </v-col>
    </v-row>

    <v-row v-else-if="tab === 'customers'">
      <v-col cols="12">
        <ToolPanel title="Customers" :panel="panels.panel('an.customers')" :currency="currency" :chart="false" @retry="reload('an.customers')" />
      </v-col>
      <v-col cols="12">
        <ToolPanel title="Visits by segment" :panel="panels.panel('an.segments')" :currency="currency" @retry="reload('an.segments')" />
      </v-col>
    </v-row>

    <v-row v-else>
      <v-col cols="12">
        <ToolPanel title="Sales, customers, spending and stock side by side" :panel="panels.panel('an.overview')" :currency="currency" :chart="false" @retry="reload('an.overview')" />
      </v-col>
      <v-col cols="12">
        <p class="text-body-2 text-medium-emphasis mb-0">
          For a question that joins areas (revenue of the products that are low on stock, revenue
          by customer segment), ask the Assistant: it writes and checks a query for it.
        </p>
      </v-col>
    </v-row>
  </div>
</template>
