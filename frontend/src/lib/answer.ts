// How an answer's status, route and parts are worded and coloured.

export interface StatusLook {
  label: string
  color: string
  icon: string
}

const STATUS: Record<string, StatusLook> = {
  answered: { label: 'Answered', color: 'success', icon: 'mdi-check-circle' },
  ok: { label: 'Done', color: 'success', icon: 'mdi-check-circle' },
  ready: { label: 'Ready', color: 'success', icon: 'mdi-check-circle' },
  partial: { label: 'Partly answered', color: 'warning', icon: 'mdi-alert' },
  no_data: { label: 'Nothing found', color: 'info', icon: 'mdi-information' },
  needs_clarification: { label: 'Needs a clearer question', color: 'info', icon: 'mdi-help-circle' },
  unsupported: { label: 'Not supported', color: 'info', icon: 'mdi-cancel' },
  unanswerable: { label: 'Cannot be answered', color: 'warning', icon: 'mdi-alert' },
  processing: { label: 'Processing', color: 'info', icon: 'mdi-progress-clock' },
  rejected: { label: 'Refused by a check', color: 'warning', icon: 'mdi-shield-alert' },
  refused: { label: 'Refused by a check', color: 'warning', icon: 'mdi-shield-alert' },
  timeout: { label: 'Took too long', color: 'warning', icon: 'mdi-timer-alert' },
  failed: { label: 'Failed', color: 'error', icon: 'mdi-alert-circle' },
  error: { label: 'Failed', color: 'error', icon: 'mdi-alert-circle' },
}

export function statusLook(status: string): StatusLook {
  return STATUS[status] ?? { label: status, color: 'grey', icon: 'mdi-help-circle' }
}

const AGENTS: Record<string, string> = {
  sales_agent: 'Sales agent',
  inventory_agent: 'Inventory agent',
  customer_agent: 'Customer agent',
  document_agent: 'Documents',
  general: 'General analysis',
}

export function agentTitle(agent: string): string {
  return AGENTS[agent] ?? agent
}

const ROUTES: Record<string, string> = {
  agents: 'Sent to the specialist agents',
  general: 'Sent to general analysis',
  documents: 'Answered from your documents',
  clarify: 'A clearer question is needed',
  unsupported: 'Outside what the assistant covers',
  undecided: 'No route could be chosen',
}

export function routeTitle(route: string): string {
  return ROUTES[route] ?? route
}

/** Whether an answer holds anything beyond its sentence: a table, sources or details. */
export function hasDetail(part: { data: unknown[]; sources: unknown[]; assumptions: string[] }): boolean {
  return part.data.length > 0 || part.sources.length > 0 || part.assumptions.length > 0
}

export const PERIODS: { value: string; title: string }[] = [
  { value: 'today', title: 'Today' },
  { value: 'yesterday', title: 'Yesterday' },
  { value: 'this_week', title: 'This week' },
  { value: 'last_week', title: 'Last week' },
  { value: 'last_7_days', title: 'Last 7 days' },
  { value: 'this_month', title: 'This month' },
  { value: 'last_month', title: 'Last month' },
  { value: 'last_30_days', title: 'Last 30 days' },
  { value: 'last_90_days', title: 'Last 90 days' },
  { value: 'this_quarter', title: 'This quarter' },
  { value: 'last_quarter', title: 'Last quarter' },
  { value: 'this_year', title: 'This year' },
  { value: 'last_year', title: 'Last year' },
]
