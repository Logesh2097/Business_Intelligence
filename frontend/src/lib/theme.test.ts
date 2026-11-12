import { describe, expect, it } from 'vitest'
import { BRAND, contrastRatio, fromOklch, groundsFor, mix, themeFor, toOklch, toRgb, WASH } from './theme'

// The gates of the design standard this theme is ported from (design-sop.md §8):
// ink must read on every ground and on the brand wash, in both modes.
describe('the derived theme', () => {
  it.each(['light', 'dark'] as const)('%s: ink reads at 7:1 on the page, the card and the wash', (mode) => {
    const theme = themeFor(mode)
    for (const ground of [theme.background, theme.surface, theme.surfaceBright, theme.wash]) {
      expect(contrastRatio(theme.ink, ground)).toBeGreaterThanOrEqual(7)
    }
  })

  it.each(['light', 'dark'] as const)('%s: brand-coloured words read on plain grounds and on the wash', (mode) => {
    const theme = themeFor(mode)
    expect(contrastRatio(theme.textBrand, theme.surface)).toBeGreaterThanOrEqual(4.5)
    expect(contrastRatio(theme.textBrand, theme.background)).toBeGreaterThanOrEqual(4.5)
    expect(contrastRatio(theme.textBrand, theme.wash)).toBeGreaterThanOrEqual(3)
  })

  it.each(['light', 'dark'] as const)('%s: every chart colour reads on the card', (mode) => {
    const theme = themeFor(mode)
    expect(theme.chart).toHaveLength(6)
    for (const colour of theme.chart) expect(contrastRatio(colour, theme.surface)).toBeGreaterThanOrEqual(3)
  })

  it('tints the grounds with the brand: a step from page to card, and the wash is the card plus the brand', () => {
    const light = groundsFor(BRAND.light.primary, 'light')
    expect(light.background).not.toBe('#f6f7f9') // carries the brand
    expect(light.wash).toBe(mix(light.surface, BRAND.light.primary, WASH))
    const dark = groundsFor(BRAND.light.primary, 'dark')
    // in dark the card is lighter than the page: it separates by luminance
    expect(contrastRatio(dark.surface, '#000000')).toBeGreaterThan(contrastRatio(dark.background, '#000000'))
  })

  it('the brand is an ocean blue, white reads on it, and the logo wash is the bar with 18 % of it', () => {
    const [, , hue] = toOklch(BRAND.light.primary)
    expect(hue).toBeGreaterThan(220) // blue, not the orange this system was ported with
    expect(hue).toBeLessThan(260)
    expect(contrastRatio(BRAND.light.onPrimary, BRAND.light.primary)).toBeGreaterThanOrEqual(4.5)
    expect(contrastRatio(BRAND.dark.onPrimary, BRAND.dark.primary)).toBeGreaterThanOrEqual(4.5)
    const theme = themeFor('light')
    expect(theme.logoWash).toBe(mix(theme.surface, BRAND.light.primary, 0.18))
  })

  it('the light page is a light blue: more blue than red in it', () => {
    const [red, , blue] = toRgb(themeFor('light').background)
    expect(blue).toBeGreaterThan(red + 8)
    expect(red).toBeGreaterThan(225) // and close to white
  })

  it('mixes and converts colours without drifting', () => {
    expect(mix('#000000', '#ffffff', 0.5)).toBe('#808080')
    expect(mix('#ff0000', '#0000ff', 0)).toBe('#ff0000')
    const [L, C, h] = toOklch('#2563eb')
    expect(fromOklch(L, C, h)).toBe('#2563eb')
    expect(() => mix('red', '#000000', 0.5)).toThrow()
  })
})
