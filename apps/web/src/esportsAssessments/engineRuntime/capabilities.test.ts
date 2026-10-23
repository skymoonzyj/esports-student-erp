import { describe, expect, it } from 'vitest'

import type { EnvironmentSnapshot, SuiteId } from '../contracts'
import { checkCapabilities } from './capabilities'

function observed(overrides: Partial<EnvironmentSnapshot> = {}): EnvironmentSnapshot {
  return {
    input_kind: 'mouse',
    is_phone: false,
    touch_capable: false,
    width_css_px: 800,
    height_css_px: 450,
    device_pixel_ratio: 2,
    pointer_lock_supported: true,
    pointer_lock_active: true,
    raw_mouse_requested: false,
    raw_mouse_confirmed: false,
    webgl2_supported: true,
    ...overrides,
  }
}

describe('checkCapabilities', () => {
  it.each<SuiteId>(['pc-fps', 'pc-moba'])('allows a matching PC environment for %s', suite => {
    expect(checkCapabilities(suite, observed())).toEqual({ allowed: true, reasons: [] })
  })

  it.each<SuiteId>(['mobile-fps', 'mobile-moba'])('allows a matching phone landscape for %s', suite => {
    expect(checkCapabilities(suite, observed({
      input_kind: 'touch',
      is_phone: true,
      touch_capable: true,
      width_css_px: 640,
      height_css_px: 360,
      pointer_lock_supported: false,
      pointer_lock_active: false,
    }))).toEqual({ allowed: true, reasons: [] })
  })

  it('does not silently accept mixed input as a phone', () => {
    const result = checkCapabilities('mobile-fps', observed({
      input_kind: 'mixed',
      is_phone: true,
      touch_capable: true,
      width_css_px: 640,
      height_css_px: 360,
    }))

    expect(result.allowed).toBe(false)
    expect(result.reasons).toContain('input_kind')
  })

  it('enforces the published mobile and PC stage minimums', () => {
    expect(checkCapabilities('mobile-moba', observed({
      input_kind: 'touch', is_phone: true, touch_capable: true,
      width_css_px: 559.99, height_css_px: 280,
    })).reasons).toContain('stage_size')
    expect(checkCapabilities('pc-moba', observed({
      width_css_px: 800, height_css_px: 449.99,
    })).reasons).toContain('stage_size')
  })

  it('requires active Pointer Lock to start PC FPS', () => {
    expect(checkCapabilities('pc-fps', observed({ pointer_lock_supported: false, pointer_lock_active: false })).reasons)
      .toEqual(expect.arrayContaining(['pointer_lock_unsupported', 'pointer_lock_inactive']))
    expect(checkCapabilities('pc-fps', observed({ pointer_lock_active: false })).allowed).toBe(false)
  })

  it('requires WebGL2 for FPS but not MOBA', () => {
    expect(checkCapabilities('pc-fps', observed({ webgl2_supported: false })).reasons)
      .toContain('webgl2_unsupported')
    expect(checkCapabilities('pc-moba', observed({ webgl2_supported: false })).allowed).toBe(true)
  })

  it('keeps raw mouse request and confirmation from becoming an admission shortcut', () => {
    const requestedOnly = observed({ raw_mouse_requested: true, raw_mouse_confirmed: false })

    expect(checkCapabilities('pc-fps', requestedOnly).allowed).toBe(true)
    expect(requestedOnly.raw_mouse_requested).not.toBe(requestedOnly.raw_mouse_confirmed)
  })
})
