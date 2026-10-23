import { describe, expect, it } from 'vitest'
import { validateRaw, validateSchedule, type TaskConfig } from '../contracts'
import { fixtureConfig, fixtureSettings } from '../testSupport/fixtures'
import { pcFpsFactory, pcFpsInstructions } from './pcFps'

const viewport = { width: 800, height: 450 }

describe('PC FPS suite factory', () => {
  it('binds exactly the four registered PC FPS engines and emits schema-valid raw results', () => {
    for (const test of ['precision', 'tracking', 'switching', 'recognition'] as const) {
      const config = fixtureConfig('pc-fps', test)
      const raw = pcFpsFactory.create(config, 123, fixtureSettings('pc-fps'), viewport).finish()
      expect(validateRaw(config, raw)).toEqual(raw)
      if (test === 'recognition') expect(() => validateSchedule(config, raw, 123)).not.toThrow()
    }
  })

  it('rejects another suite and an unregistered PC FPS project instead of falling back', () => {
    expect(() => pcFpsFactory.create(
      fixtureConfig('mobile-fps', 'precision'), 123, fixtureSettings('mobile-fps'), viewport,
    )).toThrow(/套系配置不匹配/)
    expect(() => pcFpsFactory.create(
      { ...fixtureConfig('pc-fps', 'recognition'), test_id: 'move-fire' } as TaskConfig,
      123,
      fixtureSettings('pc-fps'),
      viewport,
    )).toThrow(/未登记的 PC FPS 项目/)
  })

  it('provides mouse, left-button, ESC, shape, and text instructions without typing tasks', () => {
    const instructions = pcFpsInstructions.join(' ')
    expect(instructions).toMatch(/鼠标/)
    expect(instructions).toMatch(/左键/)
    expect(instructions).toMatch(/ESC/)
    expect(instructions).toMatch(/形状/)
    expect(instructions).toMatch(/文字/)
    expect(instructions).not.toMatch(/打字|记忆/)
  })
})
