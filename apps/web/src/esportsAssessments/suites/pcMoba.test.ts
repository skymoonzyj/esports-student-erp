import { describe, expect, it } from 'vitest'
import { validateRaw, type TaskConfig } from '../contracts'
import { fixtureConfig, fixtureCurrentConfig, fixtureSettings } from '../testSupport/fixtures'
import { pcMobaFactory } from './pcMoba'

describe('PC MOBA factory', () => {
  it.each([['last-hit', 'last_hit'], ['orb-walk', 'rhythm'], ['dodge', 'dodge'], ['map-awareness', 'map']] as const)('assembles %s into the real %s engine', (test, kind) => {
    const config = fixtureConfig('pc-moba', test)
    const game = pcMobaFactory.create(config, 123, fixtureSettings('pc-moba'), { width: 700, height: 350 })
    game.advanceTo(60000)
    expect(game.finish().kind).toBe(kind)
    expect(validateRaw(config, game.finish())).toEqual(game.finish())
    const scene = game.scene()
    if (scene.kind !== 'moba') throw new Error('scene')
    expect(scene.logical_size).toEqual({ width: 1000, height: 562.5 })
    expect(scene.minimap.visible).toBe(test === 'map-awareness')
  })

  it('rejects other suites and an unregistered member without fallback', () => {
    const viewport = { width: 1000, height: 562.5 }
    expect(() => pcMobaFactory.create(fixtureConfig('mobile-moba', 'dodge'), 123, fixtureSettings('mobile-moba'), viewport)).toThrow()
    expect(() => pcMobaFactory.create({ ...fixtureConfig('pc-moba', 'last-hit'), test_id: 'precision' } as TaskConfig, 123, fixtureSettings('pc-moba'), viewport)).toThrow()
  })

  it.each(['last-hit', 'orb-walk', 'dodge', 'map-awareness'] as const)('routes current %s configs to a v2-feedback engine', test => {
    const game = pcMobaFactory.create(fixtureCurrentConfig('pc-moba', test), 123, fixtureSettings('pc-moba'), { width: 800, height: 450 })
    expect(game.scene()).toMatchObject({ kind: 'moba', feedback: { version: 'v2', task_id: test } })
  })
})
