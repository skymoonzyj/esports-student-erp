import { describe, expect, it } from 'vitest'
import { previewTaskConfig, validateRaw, validateSchedule, type RawResult } from '../contracts'
import type { GameEngine, InputAction, MobaSceneSnapshot } from '../engineRuntime/types'
import { buildDodgeChoicePlan } from '../ruleSchedule'
import { pcMobaFactory } from '../suites/pcMoba'
import { mobileMobaFactory } from '../suites/mobileMoba'
import { fixtureSettings, fixtureV3Config } from '../testSupport/fixtures'
import { MobaEngine } from './world'

const suites = ['pc-moba', 'mobile-moba'] as const
function config(suite: typeof suites[number], difficulty = 3) { return previewTaskConfig(fixtureV3Config(suite, 'dodge'), difficulty) }
function create(suite: typeof suites[number] = 'pc-moba', seed = 123, difficulty = 3) {
  return (suite === 'pc-moba' ? pcMobaFactory : mobileMobaFactory).create(config(suite, difficulty), seed, fixtureSettings(suite), { width: 1000, height: 562.5 })
}
function scene(game: GameEngine) { return game.scene() as MobaSceneSnapshot }
function raw(game: GameEngine) {
  const result = game.finish()
  if (result.kind !== 'dodge_choice') throw new Error('dodge_choice result required')
  return result
}
function plan(suite: typeof suites[number], seed = 123, difficulty = 3) {
  const p = config(suite, difficulty).params
  if (p.kind !== 'dodge_choice') throw new Error('choice config required')
  return buildDodgeChoicePlan(seed, p)
}
function toCenter(game: GameEngine, at: number, mobile = false, button: 0 | 2 = 0) {
  game.advanceTo(at)
  const s = scene(game), center = s.danger_choice!.safe_zone!.position
  if (!mobile) game.input({ kind: 'point', at_ms: at, ...center, button })
  else {
    const dx = center.x - s.player.position.x, dy = center.y - s.player.position.y, distance = Math.hypot(dx, dy)
    game.input({ kind: 'move', at_ms: at, x: dx / distance, y: dy / distance })
    game.input({ kind: 'move', at_ms: at + distance / .18, x: 0, y: 0 })
  }
  return center
}

describe('safe-circle dodge v3', () => {
  it.each(suites)('keeps %s waiting private, freezes warning geometry, and makes scene reads pure', suite => {
    const game = create(suite), first = plan(suite)[0]
    expect(scene(game).danger_choice).toEqual({ kind: 'safe-circle-v3', phase: 'waiting', safe_zone: null })
    game.advanceTo(first.warning_at_ms - 1)
    expect(scene(game).danger_choice!.safe_zone).toBeNull()
    game.advanceTo(first.warning_at_ms)
    const warning = scene(game), before = raw(game)
    expect(warning.danger_choice!.phase).toBe('warning')
    expect(warning.danger_choice!.safe_zone!.radius).toBe(32)
    expect(warning.danger_choice).not.toHaveProperty('safe_option_id')
    for (let i = 0; i < 10; i++) expect(scene(game)).toEqual(warning)
    expect(raw(game)).toEqual(before)
    game.input({ kind: 'move', at_ms: first.warning_at_ms, x: 1, y: 0 })
    game.advanceTo(first.warning_at_ms + 100)
    expect(scene(game).danger_choice!.safe_zone).toEqual(warning.danger_choice!.safe_zone)
  })

  it.each(suites)('resolves all 30 idle %s rounds as hits', suite => {
    const game = create(suite)
    game.advanceTo(60000)
    expect(raw(game).rounds).toHaveLength(30)
    expect(raw(game).rounds.every(round => round.hit && round.safe_entry_latency_ms === null)).toBe(true)
    expect(scene(game).danger_choice!.phase).toBe('resolved')
    expect(validateRaw(config(suite), raw(game))).toEqual(raw(game))
    validateSchedule(config(suite), raw(game), 123)
  })

  it.each(suites.flatMap(suite => [1, 2, 3, 4, 5].map(difficulty => [suite, difficulty] as const)))('succeeds in all 30 %s difficulty %i rounds using real input to the visible cue', (suite, difficulty) => {
    const game = create(suite, 123, difficulty)
    for (const event of plan(suite, 123, difficulty)) toCenter(game, event.warning_at_ms, suite === 'mobile-moba')
    game.advanceTo(60000)
    expect(raw(game).rounds.every(round => !round.hit && round.safe_entry_latency_ms !== null)).toBe(true)
    expect(validateRaw(config(suite, difficulty), raw(game))).toEqual(raw(game))
    expect(scene(game).at_ms).toBe(60000)
  })

  it('accepts primary and secondary PC clicks identically, with analytic entry before destination stop', () => {
    const results = [0, 2].map(button => {
      const game = create(), event = plan('pc-moba')[0]
      game.advanceTo(event.warning_at_ms)
      const start = scene(game).player.position, center = toCenter(game, event.warning_at_ms, false, button as 0 | 2)
      game.advanceTo(event.resolved_at_ms)
      expect(raw(game).rounds[0].safe_entry_latency_ms).toBeCloseTo((Math.hypot(center.x - start.x, center.y - start.y) - 20) / .18, 8)
      expect(raw(game).rounds[0].hit).toBe(false)
      expect(scene(game).player.position).toEqual(center)
      return raw(game)
    })
    expect(results[0]).toEqual(results[1])
  })

  it('locks failure after briefly leaving during the burst even when back inside at resolve', () => {
    const game = create(), event = plan('pc-moba')[0]
    const center = toCenter(game, event.warning_at_ms)
    game.input({ kind: 'point', at_ms: event.activation_at_ms - 200, x: center.x + 19, y: center.y, button: 0 })
    game.input({ kind: 'move', at_ms: event.activation_at_ms, x: 1, y: 0 })
    game.input({ kind: 'point', at_ms: event.activation_at_ms + 15, ...center, button: 0 })
    game.advanceTo(event.resolved_at_ms)
    expect(Math.hypot(scene(game).player.position.x - center.x, scene(game).player.position.y - center.y)).toBeLessThan(20)
    expect(raw(game).rounds[0]).toMatchObject({ hit: true, safe_entry_latency_ms: expect.any(Number) })
    game.advanceTo(2000)
    expect(raw(game).rounds[0].hit).toBe(true)
  })

  it('treats the closed radius boundary as safe throughout the half-open burst and freezes resolved results', () => {
    const game = create(), event = plan('pc-moba')[0]
    const center = toCenter(game, event.warning_at_ms)
    game.input({ kind: 'point', at_ms: event.activation_at_ms - 200, x: center.x + 20, y: center.y, button: 0 })
    game.advanceTo(event.resolved_at_ms)
    expect(raw(game).rounds[0].hit).toBe(false)
    const before = raw(game).rounds[0]
    game.input({ kind: 'move', at_ms: event.resolved_at_ms, x: 1, y: 0 })
    game.advanceTo(2000)
    expect(raw(game).rounds[0]).toEqual(before)
  })

  it('settles the final mobile difficulty-1 burst at 60000 before finishing and ignores deadline input', () => {
    const game = create('mobile-moba', 62, 1), events = plan('mobile-moba', 62, 1)
    expect(events[29].resolved_at_ms).toBe(60000)
    for (const event of events) toCenter(game, event.warning_at_ms, true)
    game.advanceTo(59999)
    expect(scene(game).danger_choice!.phase).toBe('active')
    const position = scene(game).player.position
    game.input({ kind: 'move', at_ms: 60000, x: 1, y: 1 })
    expect(scene(game).at_ms).toBe(59999)
    game.advanceTo(60000)
    expect(scene(game).danger_choice!.phase).toBe('resolved')
    expect(scene(game).player.position).toEqual(position)
    expect(raw(game).rounds.every(round => !round.hit)).toBe(true)
    expect(scene(game).feedback!.outcomes).toEqual([{ objective: 'dodge', round: 30, result: 'success' }])
    const before = raw(game)
    game.input({ kind: 'point', at_ms: 60000, x: 12, y: 12, button: 2 })
    game.advanceTo(70000)
    expect(raw(game)).toEqual(before)
  })

  it('does not count first entry exactly at resolve and does retain a late burst entry as a hit', () => {
    for (const earlyBy of [0, 1]) {
      const game = create(), event = plan('pc-moba')[0]
      game.advanceTo(event.warning_at_ms)
      const s = scene(game), center = s.danger_choice!.safe_zone!.position
      const distance = Math.hypot(center.x - s.player.position.x, center.y - s.player.position.y)
      game.input({ kind: 'point', at_ms: event.resolved_at_ms - (distance - 20) / .18 - earlyBy, ...center, button: 0 })
      game.advanceTo(event.resolved_at_ms)
      expect(raw(game).rounds[0].hit).toBe(true)
      if (earlyBy === 0) expect(raw(game).rounds[0].safe_entry_latency_ms).toBeNull()
      else expect(raw(game).rounds[0].safe_entry_latency_ms).toBeCloseTo(1149, 8)
    }
  })

  it('counts the same entry for an already moving stick and a newly issued destination', () => {
    const event = plan('pc-moba')[1]
    const direction = { x: 120, y: 20 }
    const distance = Math.hypot(direction.x, direction.y)
    const continuous = create(), clicked = create()
    for (const game of [continuous, clicked]) {
      game.input({ kind: 'point', at_ms: 0, x: 200, y: 150, button: 0 })
      game.advanceTo(2000)
    }
    continuous.input({ kind: 'move', at_ms: 2000, x: direction.x / distance, y: direction.y / distance })
    continuous.advanceTo(event.warning_at_ms)
    continuous.input({ kind: 'move', at_ms: event.warning_at_ms + distance / .18, x: 0, y: 0 })
    toCenter(clicked, event.warning_at_ms)
    continuous.advanceTo(event.resolved_at_ms); clicked.advanceTo(event.resolved_at_ms)
    expect(raw(continuous).rounds[1].safe_entry_latency_ms).toBeCloseTo(raw(clicked).rounds[1].safe_entry_latency_ms!, 8)
    expect(raw(continuous).rounds[1].hit).toBe(false)
  })

  it.each(['stop', 'reverse'] as const)('cancels a predicted entry when real input chooses to %s before arrival', command => {
    const game = create(), event = plan('pc-moba')[0]
    toCenter(game, event.warning_at_ms)
    game.input({ kind: 'move', at_ms: event.warning_at_ms + 100, x: command === 'stop' ? 0 : -1, y: 0 })
    game.advanceTo(event.resolved_at_ms)
    expect(raw(game).rounds[0]).toMatchObject({ hit: true, safe_entry_latency_ms: null })
  })

  it.each([30, 60, 120])('preserves exact raw and positions through wall slides, stops and direction changes at %i Hz', hz => {
    const run = (render: boolean) => {
      const game = create('mobile-moba')
      let frame = 0
      const inputs: InputAction[] = [
        { kind: 'move', at_ms: 0, x: 1, y: 1 },
        { kind: 'move', at_ms: 4301, x: -1, y: 0 },
        { kind: 'move', at_ms: 5971, x: 0, y: 0 },
        { kind: 'move', at_ms: 6103, x: -.2, y: -.9 },
        { kind: 'move', at_ms: 9181, x: 1, y: 1 },
        { kind: 'move', at_ms: 10003, x: 0, y: 0 },
      ]
      for (const input of [...inputs, { kind: 'move', at_ms: 59999, x: 0, y: 0 } as const]) {
        if (render) while (frame * 1000 / hz < input.at_ms) { game.advanceTo(frame * 1000 / hz); game.scene(); frame++ }
        game.input(input)
      }
      game.advanceTo(60000)
      return { raw: raw(game), position: scene(game).player.position }
    }
    expect(run(true)).toEqual(run(false))
  })

  it.each(suites.flatMap(suite => [30, 60, 120].map(hz => [suite, hz] as const)))('matches sparse %s real-input results with %i Hz rendering and repeated scenes', (suite, hz) => {
    const run = (render: boolean) => {
      const game = create(suite)
      let nextFrame = 1000 / hz
      const advance = (to: number) => {
        if (render) while (nextFrame < to) { game.advanceTo(nextFrame); game.scene(); game.scene(); nextFrame += 1000 / hz }
        game.advanceTo(to)
      }
      for (const event of plan(suite)) {
        advance(event.warning_at_ms)
        const center = scene(game).danger_choice!.safe_zone!.position
        advance(event.warning_at_ms + 37)
        if (suite === 'pc-moba') game.input({ kind: 'point', at_ms: event.warning_at_ms + 37, ...center, button: 0 })
        else {
          const position = scene(game).player.position
          const dx = center.x - position.x, dy = center.y - position.y, distance = Math.hypot(dx, dy)
          const at = event.warning_at_ms + 37
          game.input({ kind: 'move', at_ms: at, x: dx / distance, y: dy / distance })
          advance(at + distance / .18)
          game.input({ kind: 'move', at_ms: at + distance / .18, x: 0, y: 0 })
        }
      }
      advance(60000)
      return raw(game)
    }
    expect(run(true)).toEqual(run(false))
  })
})

class MovementProbe extends MobaEngine {
  boundary() { return this.nextMovementBoundaryAt() }
  protected nextEventAt() { return this.nextMovementBoundaryAt() }
  protected simulate() {}
  protected acceptInput(action: InputAction) { this.movementInput(action) }
  scene() { return this.snapshot() }
  finish(): RawResult { return { kind: 'dodge_choice', rounds: [] } }
}

describe('piecewise movement boundaries', () => {
  it('returns Infinity for zero direction, reached destinations and a held stick against a wall', () => {
    const game = new MovementProbe(config('pc-moba'), 123, fixtureSettings('pc-moba'))
    expect(game.boundary()).toBe(Infinity)
    game.input({ kind: 'point', at_ms: 0, x: 500, y: 281.25, button: 0 })
    expect(game.boundary()).toBe(Infinity)
    game.input({ kind: 'move', at_ms: 0, x: 1, y: 0 })
    expect(game.boundary()).toBeCloseTo(488 / .18, 8)
    game.advanceTo(3000)
    expect(game.boundary()).toBe(Infinity)
    game.input({ kind: 'move', at_ms: 3000, x: 0, y: 0 })
    expect(game.boundary()).toBe(Infinity)
  })
  it('splits at the first wall of diagonal motion and at a click destination', () => {
    const game = new MovementProbe(config('pc-moba'), 123, fixtureSettings('pc-moba'))
    game.input({ kind: 'move', at_ms: 0, x: 1, y: 1 })
    expect(game.boundary()).toBeCloseTo(269.25 * Math.SQRT2 / .18, 8)
    game.advanceTo(2200)
    expect(game.boundary()).toBeGreaterThan(2200)
    game.input({ kind: 'point', at_ms: 2200, x: 800, y: 500, button: 0 })
    const p = scene(game).player.position
    expect(game.boundary()).toBeCloseTo(2200 + Math.hypot(p.x - 800, p.y - 500) / .18, 8)
    game.advanceTo(5000)
    expect(game.boundary()).toBe(Infinity)
  })
})
