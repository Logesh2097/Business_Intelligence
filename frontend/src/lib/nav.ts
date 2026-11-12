// The screens of the app, grouped as the sidebar shows them.

export interface NavItem {
  title: string
  short: string // the label under a tab on a phone
  icon: string
  to: string
}

export interface NavGroup {
  title: string
  items: NavItem[]
}

const OVERVIEW: NavItem[] = [
  { title: 'Dashboard', short: 'Home', icon: 'mdi-view-dashboard-outline', to: '/dashboard' },
  { title: 'Assistant', short: 'Ask', icon: 'mdi-message-text-outline', to: '/assistant' },
]
const ANALYSE: NavItem[] = [
  { title: 'Analytics', short: 'Analytics', icon: 'mdi-chart-box-outline', to: '/analytics' },
  { title: 'Insights', short: 'Insights', icon: 'mdi-lightbulb-on-outline', to: '/insights' },
]
const MANAGE: NavItem[] = [
  { title: 'Knowledge base', short: 'Documents', icon: 'mdi-file-document-multiple-outline', to: '/knowledge' },
  { title: 'Activity', short: 'Activity', icon: 'mdi-history', to: '/activity' },
]
const SYSTEM: NavItem[] = [{ title: 'Status', short: 'Status', icon: 'mdi-heart-pulse', to: '/status' }]

/** What the rail lists. Without a user only the open page is shown. */
export function navGroups(signedIn: boolean): NavGroup[] {
  const system = { title: 'System', items: SYSTEM }
  if (!signedIn) return [system]
  return [
    { title: 'Overview', items: OVERVIEW },
    { title: 'Analyse', items: ANALYSE },
    { title: 'Manage', items: MANAGE },
    system,
  ]
}

/** The four tabs of the phone's bottom bar; everything else is under "More". */
export function mobileTabs(signedIn: boolean): NavItem[] {
  return signedIn ? [...OVERVIEW, ANALYSE[0], MANAGE[0]] : []
}

/** The first letters of an email's name part: "analyst@brewbite.example" -> "AN". */
export function initials(email: string): string {
  const name = email.split('@')[0] ?? ''
  const parts = name.split(/[._-]+/).filter(Boolean)
  const letters = parts.length > 1 ? parts[0][0] + parts[1][0] : name.slice(0, 2)
  return letters.toUpperCase() || '?'
}
