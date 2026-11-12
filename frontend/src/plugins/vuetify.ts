// Vuetify: the theme and the shared component defaults.
//
// The look is the Digitory Reservation portal's (its plugins/vuetify.js and
// doc/design/design-sop.md): one brand colour, tinted neutral grounds, flat cards with a
// hairline, 44 px controls, a brand wash for heads and the active row. The colours are
// derived in lib/theme.ts; the tokens and the type ladder are in assets/main.css.
import '@mdi/font/css/materialdesignicons.css'
import 'vuetify/styles'
import '../assets/main.css'
import { createVuetify } from 'vuetify'
import { themeFor } from '../lib/theme'

const THEME_KEY = 'bi.theme'
export type ThemeName = 'appLight' | 'appDark'

/** The remembered choice; without one, what the device prefers. */
export function savedTheme(): ThemeName {
  try {
    const saved = localStorage.getItem(THEME_KEY)
    if (saved === 'appDark' || saved === 'dark') return 'appDark'
    if (saved === 'appLight' || saved === 'light') return 'appLight'
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'appDark' : 'appLight'
  } catch {
    return 'appLight'
  }
}

export function rememberTheme(name: string): void {
  try {
    localStorage.setItem(THEME_KEY, name)
  } catch {
    // private browsing: the choice lasts for this visit only
  }
}

const light = themeFor('light')
const dark = themeFor('dark')

/** `--text-brand`: the brand as words. Set here because it is derived, not written in the CSS. */
export function brandWords(name: ThemeName): string {
  return name === 'appDark' ? dark.textBrand : light.textBrand
}

export const appLight = {
  dark: false,
  colors: {
    primary: light.primary,
    'primary-darken-1': light.primaryDarken,
    'primary-lighten-1': light.primaryLighten,
    secondary: '#111827',
    accent: '#2563eb',
    background: light.background,
    surface: light.surface,
    'surface-bright': light.surfaceBright,
    'surface-light': light.surfaceLight,
    'surface-variant': light.surfaceVariant,
    'fill-ink': light.fillInk, // the ink the neutral washes are drawn in; never for text
    wash: light.wash, // the brand wash: heads, the active row, a notice
    head: light.wash,
    'logo-wash': light.logoWash,
    error: '#dc2626',
    warning: '#f59e0b',
    info: '#2563eb',
    success: '#10b981',
    'on-primary': light.onPrimary,
    'on-secondary': '#ffffff',
    'on-background': '#111827',
    'on-surface': '#111827',
    'chart-1': light.chart[0],
    'chart-2': light.chart[1],
    'chart-3': light.chart[2],
  },
}

// Dark is designed, not derived from light: the page is not near-black, the card is a
// clear step above it, and the ink is slightly lowered so the screen is calm in a dim room.
export const appDark = {
  dark: true,
  colors: {
    primary: dark.primary,
    'primary-darken-1': dark.primaryDarken,
    'primary-lighten-1': dark.primaryLighten,
    secondary: '#e8eaed',
    accent: '#5c9bff',
    background: dark.background,
    surface: dark.surface,
    'surface-bright': dark.surfaceBright,
    'surface-light': dark.surfaceLight,
    'surface-variant': dark.surfaceVariant,
    'fill-ink': dark.fillInk,
    wash: dark.wash,
    head: dark.wash,
    'logo-wash': dark.logoWash,
    error: '#ef5350',
    warning: '#ffa726',
    info: '#5c9bff',
    success: '#34d399',
    'on-primary': dark.onPrimary,
    'on-secondary': '#111827',
    'on-background': '#e7e9ec',
    'on-surface': '#e7e9ec',
    'on-surface-variant': '#e7e9ec',
    'on-surface-bright': '#e7e9ec',
    'on-surface-light': '#e7e9ec',
    'chart-1': dark.chart[0],
    'chart-2': dark.chart[1],
    'chart-3': dark.chart[2],
  },
}

// The brand fill is spent on the one primary action, the focus ring and the tab
// indicator. A choice (switch, checkbox, radio) is green. Resting cards are flat with a
// hairline; shadows are for things that float. Every control is 44 px unless it says small.
const field = { variant: 'outlined', density: 'comfortable', rounded: 'lg', color: 'primary', hideDetails: 'auto' } as const

export const vuetify = createVuetify({
  theme: {
    defaultTheme: savedTheme(),
    themes: { appLight, appDark },
  },
  defaults: {
    VBtn: { rounded: 'lg', height: 44, variant: 'flat' },
    VBtnToggle: { rounded: 'lg', VBtn: { rounded: 0 } },
    VCard: { rounded: 'lg', elevation: 0 },
    VTextField: field,
    VTextarea: field,
    VSelect: field,
    VFileInput: field,
    VAutocomplete: field,
    VChip: { rounded: 'lg' },
    VDialog: { rounded: 'lg', contentClass: 'app-overlay app-overlay--dialog' },
    VMenu: { rounded: 'lg', contentClass: 'app-overlay app-overlay--menu' },
    VAvatar: { rounded: 'lg' },
    VTabs: { sliderColor: 'primary', density: 'comfortable' },
    VSwitch: { color: 'success', density: 'compact', hideDetails: true },
    VCheckbox: { color: 'success' },
    VRadio: { color: 'success' },
    VTooltip: { openDelay: 200, closeDelay: 100, contentClass: 'v-tooltip-custom' },
  },
})
