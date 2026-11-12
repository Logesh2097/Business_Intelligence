<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { computed, ref, watchEffect } from 'vue'
import { useRouter } from 'vue-router'
import { useDisplay, useTheme } from 'vuetify'
import AppSidebar from './layouts/AppSidebar.vue'
import AppUserMenu from './layouts/AppUserMenu.vue'
import MobileNav from './layouts/MobileNav.vue'
import { mobileTabs, navGroups } from './lib/nav'
import { brandWords, rememberTheme, type ThemeName } from './plugins/vuetify'
import { usePanelsStore } from './stores/panels'
import { useSessionStore } from './stores/session'

const MINI_KEY = 'bi.navMini'

const theme = useTheme()
const { mdAndUp } = useDisplay()
const router = useRouter()
const session = useSessionStore()
const { me, email, signedIn } = storeToRefs(session)

const drawer = ref<boolean | null>(null)
const mini = ref(readMini())

const isDark = computed(() => theme.global.current.value.dark)
const groups = computed(() => navGroups(signedIn.value))
const tabs = computed(() => mobileTabs(signedIn.value))

function readMini(): boolean {
  try {
    return localStorage.getItem(MINI_KEY) === '1'
  } catch {
    return false
  }
}

function toggleMini(): void {
  mini.value = !mini.value
  try {
    localStorage.setItem(MINI_KEY, mini.value ? '1' : '0')
  } catch {
    // private browsing: the choice lasts for this visit only
  }
}

function setTheme(dark: boolean): void {
  const name: ThemeName = dark ? 'appDark' : 'appLight'
  theme.change(name)
  rememberTheme(name)
}

// the brand as words is derived from the theme, so it is set here, not written in the CSS
watchEffect(() => {
  document.documentElement.style.setProperty('--text-brand', brandWords(isDark.value ? 'appDark' : 'appLight'))
})

async function switchUser(): Promise<void> {
  session.signOut()
  usePanelsStore().reset() // figures of one user must not be shown to the next
  await router.replace('/sign-in')
}
</script>

<template>
  <v-app>
    <v-app-bar flat height="56" class="px-2 appbar" :class="{ 'appbar--phone': !mdAndUp }">
      <router-link :to="signedIn ? '/dashboard' : '/sign-in'" class="d-flex align-center ga-2 ml-2 appbar-brand">
        <span class="appbar-name">DataPulse</span>
      </router-link>
      <v-btn
        v-if="mdAndUp"
        icon
        variant="text"
        size="small"
        class="ml-1"
        aria-label="Fold or open the navigation"
        @click="toggleMini"
      >
        <v-icon :icon="mini ? 'mdi-menu' : 'mdi-menu-open'" size="20" />
      </v-btn>
      <span v-if="me" class="appbar-place d-none d-sm-inline">{{ me.company }}</span>
      <v-spacer />
      <v-btn
        v-if="!me"
        icon
        variant="text"
        size="small"
        class="mr-1"
        :aria-label="isDark ? 'Switch to the light theme' : 'Switch to the dark theme'"
        @click="setTheme(!isDark)"
      >
        <v-icon :icon="isDark ? 'mdi-white-balance-sunny' : 'mdi-weather-night'" size="20" />
      </v-btn>
      <AppUserMenu v-if="me" :me="me" :email="email" :dark="isDark" @theme="setTheme" @switch="switchUser" />
    </v-app-bar>

    <AppSidebar v-model="drawer" :groups="groups" :mini="mini" :desktop="mdAndUp" />

    <v-main class="app-main">
      <router-view />
    </v-main>

    <MobileNav v-if="!mdAndUp && signedIn" :tabs="tabs" :more-open="!!drawer" @more="drawer = !drawer" />
  </v-app>
</template>

<style scoped>
/* The bar is a plain surface with a hairline under it and a soft brand wash behind the
   mark that is gone by 300 px. No block: the bar stays one piece. */
.appbar {
  z-index: 1007;
  background-color: rgb(var(--v-theme-surface)) !important;
  border-bottom: 1px solid var(--hairline) !important;
  background-image: linear-gradient(
    100deg,
    rgb(var(--v-theme-logo-wash)) 0,
    rgba(var(--v-theme-logo-wash), 0.39) 190px,
    rgba(var(--v-theme-logo-wash), 0) 300px
  ) !important;
}
/* on a phone the wash crosses the whole bar and there is no line under it */
.appbar--phone {
  border-bottom: 0 !important;
  background-image: linear-gradient(
    100deg,
    rgb(var(--v-theme-logo-wash)) 0,
    rgba(var(--v-theme-logo-wash), 0.55) 55%,
    rgba(var(--v-theme-logo-wash), 0.28) 100%
  ) !important;
}
.appbar-brand {
  flex: 0 0 auto;
  text-decoration: none;
  color: inherit;
  min-height: var(--control-h);
}
.appbar-logo {
  flex: 0 0 auto;
}
.appbar-name {
  font-family: var(--font-heading);
  font-size: var(--text-section);
  font-weight: 600;
  letter-spacing: -0.01em;
  white-space: nowrap;
  color: var(--ink);
}
.appbar-place {
  margin-left: 8px;
  font-size: var(--text-meta);
  font-weight: 500;
  color: var(--ink-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* the page fades from white at the top into the light blue ground */
.app-main {
  background:
    linear-gradient(180deg, rgb(var(--v-theme-surface)) 0, rgba(var(--v-theme-surface), 0) 420px),
    rgb(var(--v-theme-background));
  min-height: 100dvh;
}
</style>
