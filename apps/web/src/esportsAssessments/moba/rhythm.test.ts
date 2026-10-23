import { describe, expect, it } from 'vitest'
import { fixtureConfig, fixtureSettings } from '../testSupport/fixtures'
import { taskConfigSchema, validateRaw } from '../contracts'
import { createRhythmEngine } from './rhythm'

describe.each([['pc-moba', 'orb-walk', 250, 100, 25], ['mobile-moba', 'move-attack', 300, 90, 30]] as const)('%s rhythm', (suite, test, windup, distance, tolerance) => {
  it('counts 30 independent successful rounds using exact platform parameters', () => {
    const game = createRhythmEngine(fixtureConfig(suite, test), 123, fixtureSettings(suite))
    for (let round = 0; round < 30; round++) {
      const start = round * 2000
      game.advanceTo(start)
      const scene = game.scene()
      if (scene.kind !== 'moba') throw new Error('scene')
      const checkpoint = scene.targets.find(t => t.type === 'checkpoint')!
      const enemy = scene.targets.find(t => t.type === 'enemy')!
      expect(Math.hypot(checkpoint.position.x - enemy.position.x, checkpoint.position.y - enemy.position.y)).toBeGreaterThan(checkpoint.radius + enemy.radius)
      expect(checkpoint.radius).toBe(tolerance)
      expect(Math.hypot(checkpoint.position.x - scene.player.position.x, checkpoint.position.y - scene.player.position.y)).toBeCloseTo(distance, 8)
      game.input({ kind: 'fire', at_ms: start, pressed: true })
      game.input({ kind: 'point', at_ms: start + windup, ...checkpoint.position, button: 2 })
    }
    game.advanceTo(60000)
    const raw = game.finish()
    if (raw.kind !== 'rhythm') throw new Error('raw')
    expect(raw.rounds).toEqual(Array.from({ length: 30 }, () => ({ attacked: true, reached: true, cancelled_count: 0 })))
  })
  it('cancels early movement, throttles retry, and never repeats a landed attack', () => {
    const game = createRhythmEngine(fixtureConfig(suite, test), 123, fixtureSettings(suite))
    game.input({ kind: 'fire', at_ms: 0, pressed: true })
    game.input({ kind: 'move', at_ms: windup - 1, x: 1, y: 0 })
    game.input({ kind: 'move', at_ms: windup, x: 0, y: 0 })
    game.input({ kind: 'fire', at_ms: 999, pressed: true })
    game.advanceTo(999 + windup)
    const early = game.finish()
    if (early.kind !== 'rhythm') throw new Error('raw')
    expect(early.rounds[0]).toEqual({ attacked: false, reached: false, cancelled_count: 1 })
    game.input({ kind: 'fire', at_ms: 1500, pressed: true })
    game.advanceTo(1900)
    const raw = game.finish()
    if (raw.kind !== 'rhythm') throw new Error('raw')
    expect(raw.rounds[0].attacked).toBe(true)
    expect(raw.rounds[0].cancelled_count).toBe(1)
    expect(raw.rounds[1].attacked).toBe(false)
  })
  it('counts reaching the inclusive tolerance exactly at round end without cross-round splicing', () => {
    const game = createRhythmEngine(fixtureConfig(suite, test), 123, fixtureSettings(suite))
    const scene = game.scene()
    if (scene.kind !== 'moba') throw new Error('scene')
    const checkpoint = scene.targets.find(t => t.type === 'checkpoint')!
    const dx = (checkpoint.position.x - scene.player.position.x) / distance
    const dy = (checkpoint.position.y - scene.player.position.y) / distance
    game.input({ kind: 'move', at_ms: 2000 - (distance - tolerance) / 180 * 1000, x: dx, y: dy })
    game.input({ kind: 'move', at_ms: 2000, x: 0, y: 0 })
    game.input({ kind: 'fire', at_ms: 2000, pressed: true })
    game.advanceTo(4000)
    const raw = game.finish()
    if (raw.kind !== 'rhythm') throw new Error('raw')
    expect(raw.rounds[0]).toEqual({ attacked: false, reached: true, cancelled_count: 0 })
    expect(raw.rounds[1]).toEqual({ attacked: true, reached: false, cancelled_count: 0 })
  })
  it.each([1, 2, 3, 4, 5])('keeps scaled checkpoint distance and inclusive end position at practice level %i', difficulty => {
    const base = fixtureConfig(suite, test)
    if (base.params.kind !== 'rhythm') throw new Error('config')
    const scaledDistance = distance * [.7, .85, 1, 1.1, 1.2][difficulty - 1]
    const config = taskConfigSchema.parse({ ...base, difficulty, params: { ...base.params, checkpoint_distance: scaledDistance } })
    const game = createRhythmEngine(config, 100 + difficulty, fixtureSettings(suite))
    const scene = game.scene()
    if (scene.kind !== 'moba') throw new Error('scene')
    const checkpoint = scene.targets.find(t => t.type === 'checkpoint')!
    expect(Math.hypot(checkpoint.position.x - scene.player.position.x, checkpoint.position.y - scene.player.position.y)).toBeCloseTo(scaledDistance, 8)
    game.input({ kind: 'fire', at_ms: 0, pressed: true })
    const travel = (scaledDistance - tolerance) / 180 * 1000
    game.input({ kind: 'move', at_ms: 2000 - travel, x: (checkpoint.position.x - scene.player.position.x) / scaledDistance, y: (checkpoint.position.y - scene.player.position.y) / scaledDistance })
    game.input({ kind: 'move', at_ms: 2000, x: 0, y: 0 })
    const raw = game.finish()
    if (raw.kind !== 'rhythm') throw new Error('raw')
    expect(raw.rounds[0]).toEqual({ attacked: true, reached: true, cancelled_count: 0 })
    expect(() => validateRaw(config, raw)).not.toThrow()
  })
  it('does not carry a windup landing at the round boundary into either round', () => {
    const game = createRhythmEngine(fixtureConfig(suite, test), 123, fixtureSettings(suite))
    game.input({ kind: 'fire', at_ms: 2000 - windup, pressed: true })
    game.advanceTo(4000)
    const raw = game.finish()
    if (raw.kind !== 'rhythm') throw new Error('raw')
    expect(raw.rounds.slice(0, 2).map(r => r.attacked)).toEqual([false, false])
  })
})

it('an attack button cannot silently stop a held movement stick to protect the windup', () => {
  const game = createRhythmEngine(fixtureConfig('mobile-moba', 'move-attack'), 123, fixtureSettings('mobile-moba'))
  game.input({ kind: 'move', at_ms: 0, x: 1, y: 0 })
  game.input({ kind: 'fire', at_ms: 100, pressed: true })
  game.advanceTo(500)
  const raw = game.finish()
  if (raw.kind !== 'rhythm') throw new Error('raw')
  expect(raw.rounds[0]).toEqual({ attacked: false, reached: false, cancelled_count: 1 })
})
