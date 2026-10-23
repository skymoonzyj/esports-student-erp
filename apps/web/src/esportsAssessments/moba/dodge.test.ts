import { describe, expect, it } from 'vitest'
import { fixtureConfig, fixtureSettings } from '../testSupport/fixtures'
import { taskConfigSchema, validateRaw } from '../contracts'
import { createDodgeEngine } from './dodge'

describe.each(['pc-moba', 'mobile-moba'])('%s dodge', suite => {
  it.each([1, 2, 3, 4, 5])('settles all 30 stationary threats at difficulty %i before 60 seconds', difficulty => {
    const config = fixtureConfig(suite, 'dodge')
    if (config.params.kind !== 'dodge') throw new Error('config')
    const speed = [.6, .8, 1, 1.15, 1.3][difficulty - 1]
    const window = [1.4, 1.2, 1, .85, .7][difficulty - 1]
    const practice = taskConfigSchema.parse({ ...config, difficulty, params: { ...config.params, projectile_speed: config.params.projectile_speed * speed, warning_ms: config.params.warning_ms * window } })
    const game = createDodgeEngine(practice, 123, fixtureSettings(suite))
    game.advanceTo(59999)
    const raw = game.finish()
    if (raw.kind !== 'dodge') throw new Error('raw')
    expect(raw.threats).toHaveLength(30)
    expect(raw.threats.every(t => t.hit && t.response_ms === null)).toBe(true)
    expect(() => validateRaw(practice, raw)).not.toThrow()
  })
  it('aims at the captured warning-start position and detects effective movement once', () => {
    const game = createDodgeEngine(fixtureConfig(suite, 'dodge'), 123, fixtureSettings(suite))
    const scene = game.scene()
    if (scene.kind !== 'moba') throw new Error('scene')
    const target = scene.projectiles[0].target
    const origin = scene.projectiles[0].origin
    const dx = (origin.y - target.y) / 240, dy = -(origin.x - target.x) / 240
    game.input({ kind: 'move', at_ms: 100, x: dx, y: dy })
    game.advanceTo(1900)
    const moved = game.scene()
    if (moved.kind !== 'moba') throw new Error('scene')
    expect(moved.projectiles[0].target).toEqual(target)
    const raw = game.finish()
    if (raw.kind !== 'dodge') throw new Error('raw')
    expect(raw.threats[0].hit).toBe(false)
    expect(raw.threats[0].response_ms).toBeCloseTo(100, 6)
  })
  it('is repeatable across rendering rates and ignores inputs beyond test time', () => {
    const run = (hz: number) => {
      const game = createDodgeEngine(fixtureConfig(suite, 'dodge'), 32, fixtureSettings(suite))
      game.input({ kind: 'move', at_ms: -1, x: 1, y: 0 })
      for (let i = 1; i <= 60 * hz; i++) game.advanceTo(i * 1000 / hz)
      const before = game.finish()
      game.input({ kind: 'move', at_ms: 60000, x: 1, y: 0 })
      expect(game.finish()).toEqual(before)
      return before
    }
    expect(run(30)).toEqual(run(60))
    expect(run(60)).toEqual(run(120))
  })
  it('keeps a viable perpendicular escape after the player has reached the world edge', () => {
    const game = createDodgeEngine(fixtureConfig(suite, 'dodge'), 123, fixtureSettings(suite))
    game.input({ kind: 'point', at_ms: 0, x: 0, y: 0, button: 2 })
    game.advanceTo(4000)
    const scene = game.scene()
    if (scene.kind !== 'moba') throw new Error('scene')
    expect(scene.player.position).toEqual({ x: 12, y: 12 })
    const p = scene.projectiles[0], dx = (p.target.x - p.origin.x) / 240, dy = (p.target.y - p.origin.y) / 240
    const candidates = [{ x: -dy, y: dx }, { x: dy, y: -dx }]
    const direction = candidates.find(v => v.x >= 0 && v.y >= 0)
    expect(direction).toBeDefined()
    game.input({ kind: 'move', at_ms: 4000, ...direction! })
    game.advanceTo(4200)
    game.input({ kind: 'move', at_ms: 4200, x: 0, y: 0 })
    game.advanceTo(5999)
    const raw = game.finish()
    if (raw.kind !== 'dodge') throw new Error('raw')
    expect(raw.threats[2]).toEqual({ hit: false, response_ms: 0 })
  })
  it('gets the same collisions for moving players with 30, 59, and 120 Hz rendering', () => {
    const run = (hz: number) => {
      const game = createDodgeEngine(fixtureConfig(suite, 'dodge'), 746, fixtureSettings(suite))
      const actions = Array.from({ length: 120 }, (_, i) => ({ kind: 'move' as const, at_ms: i * 500 + 17, x: i % 3 === 0 ? 1 : i % 3 === 1 ? -1 : 0, y: i % 4 === 0 ? .3 : 0 }))
      let index = 0
      for (let frame = 1; frame <= hz * 60; frame++) {
        const now = frame * 1000 / hz
        while (index < actions.length && actions[index].at_ms <= now) game.input(actions[index++])
        game.advanceTo(now)
      }
      return game.finish()
    }
    expect(run(30)).toEqual(run(59))
    expect(run(59)).toEqual(run(120))
  })
})
