import { describe, expect, it } from 'vitest'
import { taskConfigSchema, validateRaw, type TaskConfig } from '../contracts'
import type { GameEngine } from '../engineRuntime/types'
import { fixtureConfig, fixtureSettings } from '../testSupport/fixtures'
import { createLastHitEngine } from './lastHit'

const config = fixtureConfig('pc-moba', 'last-hit')
const settings = fixtureSettings('pc-moba')
const make = (seed = 123, task = config) => createLastHitEngine(task, seed, settings)
function minion(game: GameEngine) {
  const scene = game.scene()
  if (scene.kind !== 'moba') throw new Error('scene')
  return scene.targets[0]
}
function waves(game: GameEngine) {
  const raw = game.finish()
  if (raw.kind !== 'last_hit') throw new Error('raw')
  return raw.waves
}
function attack(game: GameEngine, at_ms: number) {
  game.advanceTo(at_ms)
  game.input({ kind: 'point', at_ms, ...minion(game).position, button: 2 })
}
// Observe the visual HP thresholds, not a production schedule helper.
function windowStarts(seed = 123, task = config): number[] {
  const game = make(seed, task)
  return Array.from({ length: 15 }, (_, wave) => {
    for (const start of [2000, 2250, 2500, 2750, 3000]) {
      game.advanceTo(wave * 4000 + start)
      if (minion(game).health_ratio! <= .1) return start
    }
    throw new Error('no visible last-hit window')
  })
}

describe('last-hit engine', () => {
  it('balances all five start bins three times and replays the seed without a visible minimap', () => {
    const starts = windowStarts()
    expect([...starts].sort((a, b) => a - b)).toEqual([2000, 2000, 2000, 2250, 2250, 2250, 2500, 2500, 2500, 2750, 2750, 2750, 3000, 3000, 3000])
    expect(windowStarts()).toEqual(starts)
    expect(windowStarts(456)).not.toEqual(starts)
    const scene = make().scene()
    if (scene.kind !== 'moba') throw new Error('scene')
    expect(scene.minimap.visible).toBe(false)
    expect(scene.logical_size).toEqual({ width: 1000, height: 562.5 })
  })

  it.each([
    [-1, false, 1, 0, null], [0, true, 0, 0, 0],
    [499, true, 0, 0, 499], [500, false, 0, 1, null],
  ] as const)('judges arrival at window offset %i, not button press', (offset, success, early, late, offset_ms) => {
    const game = make(), start = windowStarts()[0]
    attack(game, start + offset - 250)
    game.advanceTo(start + offset - 1)
    expect(waves(game)[0]).toEqual({ success: false, early: 0, late: 0, offset_ms: null })
    game.advanceTo(start + offset)
    expect(waves(game)[0]).toEqual({ success, early, late, offset_ms })
  })

  it('retains once-only attack resolution and ignores repeated commands inside 1000 ms', () => {
    const game = make()
    attack(game, 0)
    for (const time of [249, 250, 251, 500, 999]) {
      attack(game, time)
      game.advanceTo(time)
    }
    expect(waves(game)[0].early).toBe(1)
    attack(game, 1000)
    game.advanceTo(1249)
    expect(waves(game)[0].early).toBe(1)
    game.advanceTo(1250)
    expect(waves(game)[0].early).toBe(2)
  })

  it.each([249, 250])('movement at %i ms cancels only an unfinished windup', at_ms => {
    const game = make()
    attack(game, 0)
    game.input({ kind: 'point', at_ms, x: 980, y: 550, button: 2 })
    game.advanceTo(750)
    expect(waves(game)[0].early).toBe(at_ms === 249 ? 0 : 1)
    const scene = game.scene()
    if (scene.kind !== 'moba') throw new Error('scene')
    expect(scene.player.position.x).toBeGreaterThan(500)
    expect(Math.hypot(minion(game).position.x - scene.player.position.x, minion(game).position.y - scene.player.position.y)).toBeLessThanOrEqual(180)
    attack(game, 1000)
    game.advanceTo(1250)
    expect(waves(game)[0].early).toBe(at_ms === 249 ? 1 : 2)
  })

  it('early attacks preserve preset HP and success consumes a wave only once', () => {
    const game = make(), idle = make(), start = windowStarts()[0]
    attack(game, 0)
    for (const time of [250, 1000, start - 1]) {
      game.advanceTo(time); idle.advanceTo(time)
      expect(minion(game).health_ratio).toBe(minion(idle).health_ratio)
      expect(minion(game).health_ratio).toBeGreaterThan(.1)
    }
    const winner = make()
    attack(winner, start - 250)
    winner.advanceTo(start)
    expect(minion(winner).state).toBe('hit')
    attack(winner, start + 750)
    winner.advanceTo(4000)
    expect(waves(winner)[0]).toEqual({ success: true, early: 0, late: 0, offset_ms: 0 })
    idle.advanceTo(start)
    expect(minion(idle).health_ratio).toBe(.1)
    idle.advanceTo(start + 499)
    expect(minion(idle).health_ratio).toBeGreaterThanOrEqual(.01)
    idle.advanceTo(start + 500)
    expect(minion(idle).health_ratio).toBe(0)
  })

  it('expires wave-boundary attacks without carrying them to the next wave', () => {
    const game = make()
    attack(game, 3750)
    game.advanceTo(4000)
    expect(minion(game).id).not.toBe('minion-0')
    game.advanceTo(4250)
    expect(waves(game).slice(0, 2)).toEqual(Array.from({ length: 2 }, () => ({ success: false, early: 0, late: 0, offset_ms: null })))
  })

  it('produces fifteen misses with no input and ignores input at 60000 ms', () => {
    const game = make()
    game.advanceTo(60000)
    const before = game.finish()
    attack(game, 60000)
    game.advanceTo(70000)
    expect(game.finish()).toEqual(before)
    expect(waves(game)).toEqual(Array.from({ length: 15 }, () => ({ success: false, early: 0, late: 0, offset_ms: null })))
    expect(validateRaw(config, before)).toEqual(before)
  })

  it.each([[0, 0], [1000, 0], [0, 562.5], [1000, 562.5]])('keeps the minion visible, distinct and in range at corner %i,%i without altering HP', (x, y) => {
    const game = make(), idle = make()
    game.input({ kind: 'point', at_ms: 0, x, y, button: 2 })
    game.advanceTo(3500); idle.advanceTo(3500)
    const scene = game.scene(), target = minion(game)
    if (scene.kind !== 'moba') throw new Error('scene')
    const distance = Math.hypot(target.position.x - scene.player.position.x, target.position.y - scene.player.position.y)
    expect(distance).toBeGreaterThan(32)
    expect(distance).toBeLessThanOrEqual(180)
    expect(target.position.x).toBeGreaterThanOrEqual(target.radius)
    expect(target.position.x).toBeLessThanOrEqual(1000 - target.radius)
    expect(target.position.y).toBeGreaterThanOrEqual(target.radius)
    expect(target.position.y).toBeLessThanOrEqual(562.5 - target.radius)
    expect(target.health_ratio).toBe(minion(idle).health_ratio)
  })

  it('ignores left clicks, synthetic fire buttons, and outside-world controls', () => {
    const game = make()
    game.input({ kind: 'point', at_ms: 0, ...minion(game).position, button: 0 })
    game.input({ kind: 'fire', at_ms: 1000, pressed: true })
    game.input({ kind: 'point', at_ms: 2000, x: -1, y: 100, button: 2 })
    game.advanceTo(4000)
    expect(waves(game)[0]).toEqual({ success: false, early: 0, late: 0, offset_ms: null })
    const scene = game.scene()
    if (scene.kind !== 'moba') throw new Error('scene')
    expect(scene.player.position).toEqual({ x: 500, y: 281.25 })
  })

  it('preserves completed state under dense rendering and detached result mutations', () => {
    const sparse = make(), dense = make(), start = windowStarts()[0]
    attack(sparse, start - 250); attack(dense, start - 250)
    for (let time = start - 249; time < 60000; time += 1000 / 144) {
      dense.advanceTo(time)
      dense.scene()
    }
    sparse.advanceTo(60000); dense.advanceTo(60000)
    expect(dense.finish()).toEqual(sparse.finish())
    const detached = waves(dense)
    detached[0].success = false
    detached[0].offset_ms = null
    expect(waves(dense)[0].success).toBe(true)
  })

  it.each([[1, 700], [2, 600], [3, 500], [4, 425], [5, 350]])('uses the already-scaled practice %i window of %i ms for all fifteen waves', (difficulty, length) => {
    const task = taskConfigSchema.parse({ ...config, difficulty, params: { ...config.params, last_hit_window_ms: length } })
    const starts = windowStarts(123, task), game = make(123, task)
    for (let wave = 0; wave < 15; wave++) {
      attack(game, wave * 4000 + starts[wave] + length - 251)
      game.advanceTo((wave + 1) * 4000)
    }
    expect(waves(game)).toEqual(Array.from({ length: 15 }, () => ({ success: true, early: 0, late: 0, offset_ms: length - 1 })))
    expect(validateRaw(task, game.finish())).toEqual(game.finish())
    const late = make(123, task)
    attack(late, starts[0] + length - 250)
    late.advanceTo(4000)
    expect(waves(late)[0]).toEqual({ success: false, early: 0, late: 1, offset_ms: null })
  })

  it('rejects a different registered engine config', () => {
    expect(() => make(123, fixtureConfig('pc-moba', 'dodge'))).toThrow(/last.hit/)
    expect(() => make(123, { ...config, params: { ...config.params, attack_windup_ms: 200 } } as TaskConfig)).toThrow()
  })
})
