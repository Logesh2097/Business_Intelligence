import { createRouter, createWebHistory } from 'vue-router'
import { useSessionStore } from './stores/session'

// Every page but the two public ones needs a user. Sign-in is not built: the "user" is
// a demo user chosen on /sign-in, in development (docs/design/ui.md).
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/dashboard' },
    { path: '/sign-in', name: 'sign-in', component: () => import('./pages/SignInPage.vue'), meta: { open: true } },
    { path: '/status', name: 'status', component: () => import('./pages/StatusPage.vue'), meta: { open: true } },
    { path: '/dashboard', name: 'dashboard', component: () => import('./pages/DashboardPage.vue') },
    { path: '/assistant', name: 'assistant', component: () => import('./pages/AssistantPage.vue') },
    { path: '/analytics', name: 'analytics', component: () => import('./pages/AnalyticsPage.vue') },
    { path: '/insights', name: 'insights', component: () => import('./pages/InsightsPage.vue') },
    { path: '/knowledge', name: 'knowledge', component: () => import('./pages/KnowledgePage.vue') },
    { path: '/activity', name: 'activity', component: () => import('./pages/ActivityPage.vue') },
    { path: '/:rest(.*)*', redirect: '/dashboard' },
  ],
})

router.beforeEach(async (to) => {
  if (to.meta.open) return true
  const session = useSessionStore()
  if (session.signedIn || (await session.restore())) return true
  return { name: 'sign-in' }
})
