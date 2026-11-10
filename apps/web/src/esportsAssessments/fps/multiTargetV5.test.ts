import { describe, expect, it } from 'vitest'
import { validateRaw } from '../contracts'
import type { FpsSceneSnapshot, GameEngine } from '../engineRuntime/types'
import { fixtureSettings } from '../testSupport/fixtures'
import { fixtureV4Config } from '../testSupport/fixturesV4'
import { fixtureV5Config } from '../testSupport/fixturesV5'
import { angularDistanceDeg, isTargetFullyVisible, vectorToDirection } from './math'
import { createMultiTargetEngine } from './multiTarget'
import { createMultiTargetV5Engine } from './multiTargetV5'

const viewport = { width: 1600, height: 900 }, settings = fixtureSettings('pc-fps')
function scene(game: GameEngine): FpsSceneSnapshot {
  const view = game.scene()
  if (view.kind !== 'fps') throw new Error('fps required')
  return view
}
function direction(target: FpsSceneSnapshot['targets'][number]) {
  return vectorToDirection({ x: target.position.x, y: target.position.y - 1.6, z: target.position.z })
}
function aim(game: GameEngine, at_ms: number, index = 0) {
  const view = scene(game), angle = direction(view.targets[index])
  game.input({ kind: 'look', at_ms, dx: angle.yaw_deg - view.camera.yaw_deg, dy: view.camera.pitch_deg - angle.pitch_deg })
}

describe('v5 fixed-board multi target engine', () => {
  it('reproduces seed123 legacy invisible retained targets and keeps the v5 full set visible instead', () => {
    const old = createMultiTargetEngine(fixtureV4Config('pc-fps', 'gridshot'), 123, settings, viewport)
    old.input({ kind: 'look', at_ms: 0, dx: 170, dy: 0 })
    expect(scene(old).targets.some(target => !isTargetFullyVisible(direction(target), target.angular_diameter_deg / 2, scene(old).camera, viewport))).toBe(true)
    const game = createMultiTargetV5Engine(fixtureV5Config('gridshot'), 123, settings, viewport)
    const original = scene(game).targets
    for (const [dx, dy] of [[170, 0], [-1000, 1000], [1000, -1000], [-.25, .25]]) {
      game.input({ kind: 'look', at_ms: 0, dx, dy })
      const view = scene(game)
      expect(view.targets).toEqual(original)
      for (const target of view.targets) expect(isTargetFullyVisible(direction(target), target.angular_diameter_deg / 2 + 1, view.camera, viewport)).toBe(true)
    }
  })

  // Catches candidate exhaustion, center reuse, retained-coordinate drift and nondeterministic replacement.
  it.each(['gridshot', 'sixshot'] as const)('%s refills 200 times for each of 100 seeds at every difficulty', id => {
    for (let difficulty = 1; difficulty <= 5; difficulty++) for (let seed = 0; seed < 100; seed++) {
      const config = fixtureV5Config(id, difficulty)
      const game = createMultiTargetV5Engine(config, seed, settings, viewport)
      const replay = createMultiTargetV5Engine(config, seed, settings, viewport)
      for (let shot = 0; shot < 200; shot++) {
        const at_ms = shot * 50, before = scene(game).targets, index = shot % before.length
        aim(game, at_ms, index); aim(replay, at_ms, index)
        for (const run of [game, replay]) {
          run.input({ kind: 'fire', at_ms, pressed: true })
          run.input({ kind: 'fire', at_ms: at_ms + .1, pressed: false })
        }
        const after = scene(game).targets
        if (after.length !== (id === 'gridshot' ? 3 : 6)) throw new Error('target count drift')
        for (let i = 0; i < before.length; i++) {
          if (i !== index && JSON.stringify(after[i]) !== JSON.stringify(before[i])) throw new Error('retained target moved')
        }
        if (after[index].id === before[index].id || angularDistanceDeg(direction(after[index]), direction(before[index])) < 1e-5) throw new Error(`hit cell reused or missing replacement: ${JSON.stringify({ id, difficulty, seed, shot, before: direction(before[index]), after: direction(after[index]), oldId: before[index].id, newId: after[index].id })}`)
        for (let a = 0; a < after.length; a++) for (let b = a + 1; b < after.length; b++) {
          if (angularDistanceDeg(direction(after[a]), direction(after[b])) < after[a].angular_diameter_deg - 1e-8) throw new Error('overlap')
        }
        if (JSON.stringify(scene(game)) !== JSON.stringify(scene(replay))) throw new Error('replay diverged')
      }
      expect(game.finish()).toEqual(replay.finish())
      const raw = game.finish()
      if (raw.kind !== 'multi_target') throw new Error('raw')
      expect(raw.shots).toHaveLength(200)
      expect(raw.shots.every(shot => shot.hit)).toBe(true)
    }
  }, 120000)

  it('preserves same-time input order, 50ms edge fire, 60s end and immutable idempotent finish', () => {
    const game = createMultiTargetV5Engine(fixtureV5Config('sixshot'), 123, settings, viewport)
    aim(game, 0)
    game.input({ kind: 'fire', at_ms: 0, pressed: true })
    game.input({ kind: 'fire', at_ms: 1, pressed: false })
    game.input({ kind: 'fire', at_ms: 49, pressed: true })
    game.input({ kind: 'fire', at_ms: 49.1, pressed: false })
    aim(game, 50)
    game.input({ kind: 'fire', at_ms: 50, pressed: true })
    game.input({ kind: 'fire', at_ms: 51, pressed: false })
    aim(game, 59999)
    game.input({ kind: 'fire', at_ms: 59999, pressed: true })
    game.input({ kind: 'fire', at_ms: 59999.1, pressed: false })
    game.input({ kind: 'look', at_ms: 60000, dx: 1, dy: 1 })
    game.input({ kind: 'fire', at_ms: 60000, pressed: true })
    const raw = game.finish()
    expect(raw).toEqual({ kind: 'multi_target', shots: [{ at_ms: 0, hit: true }, { at_ms: 50, hit: true }, { at_ms: 59999, hit: true }] })
    expect(validateRaw(fixtureV5Config('sixshot'), raw)).toEqual(raw)
    expect(scene(game).targets.every(target => target.state === 'hidden')).toBe(true)
    const final = scene(game)
    game.input({ kind: 'look', at_ms: 500, dx: 1, dy: 1 })
    game.advanceTo(80000)
    expect(scene(game)).toEqual(final)
    expect(game.finish()).toBe(raw)
    expect(Object.isFrozen(raw)).toBe(true)
    if (raw.kind !== 'multi_target') throw new Error('raw')
    expect(Object.isFrozen(raw.shots[0])).toBe(true)
  })

  it('rejects historical and non-grid configs rather than changing their behavior', () => {
    expect(() => createMultiTargetV5Engine(fixtureV4Config('pc-fps', 'gridshot'), 1, settings, viewport)).toThrow(/v5|配置/)
    expect(() => createMultiTargetV5Engine(fixtureV5Config('spheretrack90'), 1, settings, viewport)).toThrow(/配置/)
  })
})
