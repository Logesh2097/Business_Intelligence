// The app's colours, derived from one brand colour.
//
// Ported from the Digitory Reservation portal (apps/theme.mjs and
// doc/design/design-sop.md there), so both products wear one design system: an app
// supplies one colour; the page, the cards, the brand wash, the lines and the chart
// colours are computed from it. Nothing here is specific to a screen.

export type Mode = 'light' | 'dark'

/**
 * The one colour the app supplies: an ocean blue. (The reservation app this system is
 * ported from supplies an orange; everything derived below follows whichever is here.)
 * White on it is 4.9 : 1; in dark it is lifted so that it also reads as words.
 */
export const BRAND = {
  light: { primary: '#0277bd', primaryDarken: '#01639e', primaryLighten: '#0d8fd6', onPrimary: '#ffffff', textBrand: '#01579b' },
  dark: { primary: '#2f9fe0', primaryDarken: '#1f8bcb', primaryLighten: '#56b4ea', onPrimary: '#06243a' },
} as const

/** How much of the brand each neutral ground carries. */
export const TINT = {
  // a very light ocean blue: mostly white, with a little of the brand in it
  light: { page: 0.05, card: 0.01, muted: 0.04, lift: 0.08, fill: 0.45 },
  dark: { page: 0.1, card: 0.12, muted: 0.1, lift: 0.15, fill: 0.35 },
} as const

/** The neutrals before the tint. Dark is designed, not derived from light. */
export const GROUNDS = {
  light: { page: '#f6f7f9', card: '#ffffff', bright: '#ffffff', light: '#fbfcfd', variant: '#212326', ink: '#111827' },
  dark: { page: '#101114', card: '#17191d', bright: '#1e2126', light: '#1e2126', variant: '#2a2e35', ink: '#e7e9ec' },
} as const

/** The brand wash: the card with this share of the brand in it (heads, the active row, a notice). */
export const WASH = 0.09
/** The wash behind the mark in the top bar: its strength at the left edge. */
export const LOGO_WASH_EDGE = 0.18

type Rgb = [number, number, number]

export function toRgb(hex: string): Rgb {
  const match = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex.trim())
  if (!match) throw new Error(`not a #rrggbb colour: ${hex}`)
  return [parseInt(match[1], 16), parseInt(match[2], 16), parseInt(match[3], 16)]
}

function toHex(rgb: number[]): string {
  return '#' + rgb.map((v) => Math.round(Math.min(255, Math.max(0, v))).toString(16).padStart(2, '0')).join('')
}

/** `a` with the share `t` of `b` mixed in. */
export function mix(a: string, b: string, t: number): string {
  const ca = toRgb(a)
  const cb = toRgb(b)
  return toHex(ca.map((v, i) => v + (cb[i] - v) * t))
}

export function luminance(hex: string): number {
  const [r, g, b] = toRgb(hex).map((v) => {
    const c = v / 255
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/** The WCAG contrast ratio of two colours, 1 to 21. */
export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

const toLinear = (v: number): number => {
  const c = v / 255
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
}
const fromLinear = (c: number): number => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055) * 255

export function toOklch(hex: string): [number, number, number] {
  const [r, g, b] = toRgb(hex).map(toLinear)
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
  const L = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s
  const a = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s
  const bb = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s
  return [L, Math.hypot(a, bb), ((Math.atan2(bb, a) * 180) / Math.PI + 360) % 360]
}

export function fromOklch(L: number, C: number, h: number): string {
  for (let c = C; c >= 0; c -= 0.002) {
    const a = c * Math.cos((h * Math.PI) / 180)
    const b = c * Math.sin((h * Math.PI) / 180)
    const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3
    const rgb = [
      4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
      -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
      -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
    ]
    if (rgb.every((v) => v >= -0.0005 && v <= 1.0005)) {
      return toHex(rgb.map((v) => fromLinear(Math.min(1, Math.max(0, v)))))
    }
  }
  return '#000000'
}

/** `hex` moved lighter or darker until it has `min` contrast on every ground. */
export function ensureOn(hex: string, grounds: string[], min: number, dir: 1 | -1): string {
  const [L0, C, h] = toOklch(hex)
  let L = L0
  let out = hex
  const worst = (): number => Math.min(...grounds.map((g) => contrastRatio(out, g)))
  for (let i = 0; i < 160 && worst() < min; i++) {
    L += dir * 0.005
    out = fromOklch(L, C, h)
  }
  return out
}

export interface Grounds {
  background: string
  surface: string
  wash: string
  surfaceBright: string
  surfaceLight: string
  surfaceVariant: string
  fillInk: string
  ink: string
}

/** The neutral grounds tinted with the brand: page, card, wash and the steps between. */
export function groundsFor(brand: string, mode: Mode): Grounds {
  const base = GROUNDS[mode]
  const t = TINT[mode]
  const dark = mode === 'dark'
  const surface = mix(base.card, brand, t.card)
  return {
    background: mix(base.page, brand, t.page),
    surface,
    wash: mix(surface, brand, WASH),
    surfaceBright: mix(base.bright, brand, dark ? t.muted : t.card),
    surfaceLight: mix(base.light, brand, t.muted),
    surfaceVariant: dark ? mix(base.variant, brand, t.lift) : base.variant,
    fillInk: mix(base.ink, brand, t.fill),
    ink: base.ink,
  }
}

// The chart hues that sit beside the brand's own (the reservation app's CHART_POOL).
const CHART_POOL = {
  light: ['#2563eb', '#0d9488', '#a21caf', '#b7791f', '#db2777'],
  dark: ['#3d7dff', '#0d9488', '#bb32cb', '#b07a00', '#e0568f'],
} as const
const CHART_BAND = { light: [0.43, 0.77], dark: [0.48, 0.67] } as const
const CHART_MARGIN = 3.2 // contrast a chart colour keeps on the card

/** The brand as a chart colour: its hue, at a lightness that reads on the card. */
export function chartColour(primary: string, surface: string, mode: Mode): string {
  const [lo, hi] = CHART_BAND[mode]
  const [L0, C0, h] = toOklch(primary)
  const C = Math.max(C0, 0.12)
  const step = mode === 'light' ? -0.004 : 0.004
  let L = Math.min(hi - 0.005, Math.max(lo + 0.005, L0))
  let hex = fromOklch(L, C, h)
  while (contrastRatio(hex, surface) < CHART_MARGIN && L > lo && L < hi) {
    L += step
    hex = fromOklch(L, C, h)
  }
  return hex
}

export interface ModeTheme extends Grounds {
  primary: string
  primaryDarken: string
  primaryLighten: string
  onPrimary: string
  textBrand: string
  logoWash: string
  chart: string[]
}

/** Everything one mode needs, from the brand. */
export function themeFor(mode: Mode): ModeTheme {
  const brand = BRAND[mode]
  const grounds = groundsFor(BRAND.light.primary, mode)
  const plain = [grounds.background, grounds.surface, grounds.surfaceBright, grounds.surfaceLight]
  const textBrand = mode === 'light' ? BRAND.light.textBrand : ensureOn(brand.primary, plain, 4.5, 1)
  return {
    ...grounds,
    primary: brand.primary,
    primaryDarken: brand.primaryDarken,
    primaryLighten: brand.primaryLighten,
    onPrimary: brand.onPrimary,
    textBrand,
    logoWash: mix(grounds.surface, brand.primary, LOGO_WASH_EDGE),
    chart: [chartColour(brand.primary, grounds.surface, mode), ...CHART_POOL[mode]],
  }
}
