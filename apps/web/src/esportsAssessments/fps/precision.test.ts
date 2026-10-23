import { describe, expect, it } from 'vitest'
import { taskConfigSchema, validateRaw, type TaskConfig } from '../contracts'
import { fixtureConfig, fixtureSettings } from '../testSupport/fixtures'
import { createFpsEngine } from './index'
import { angularDistanceDeg, vectorToDirection } from './math'

const viewport = { width: 844, height: 450 }
function practice(suite: string, difficulty: number): TaskConfig {
  const config = fixtureConfig(suite, 'precision')
  const [size, distance] = [[1.5, .7], [1.25, .85], [1, 1], [.85, 1.1], [.7, 1.2]][difficulty - 1]
  if (config.params.kind !== 'aim') throw new Error('fixture')
  return taskConfigSchema.parse({ ...config, difficulty, params: { ...config.params, target_diameter_deg: config.params.target_diameter_deg * size, angle_distances_deg: config.params.angle_distances_deg.map(x => x * distance) } })
}

describe('precision engine', () => {
  it.each(['pc-fps', 'mobile-fps'])('counts all 30 opportunities for %s at all practice levels', suite => {
    for (let level = 1; level <= 5; level++) {
      const config = practice(suite, level)
      const game = createFpsEngine(config, 123, fixtureSettings(suite), viewport)
      game.advanceTo(60000)
      const raw = game.finish()
      if (raw.kind !== 'aim') throw new Error('wrong raw kind')
      expect(raw.trials).toHaveLength(30)
      expect(raw.trials.every(t => !t.hit && t.latency_ms === 2000)).toBe(true)
      expect(raw.shots).toBe(0)
      expect(validateRaw(config, raw)).toEqual(raw)
      expect(game.finish()).toBe(raw)
    }
  })

  it('does not advance to a new target after a mid-frame hit and excludes final input', () => {
    const game = createFpsEngine(fixtureConfig('pc-fps', 'precision'), 123, fixtureSettings('pc-fps'), viewport)
    const scene = game.scene()
    if (scene.kind !== 'fps') throw new Error('scene')
    const target = vectorToDirection({ x: scene.targets[0].position.x, y: scene.targets[0].position.y - scene.camera.position.y, z: scene.targets[0].position.z })
    game.input({ kind: 'look', at_ms: 7, dx: target.yaw_deg, dy: -target.pitch_deg })
    game.input({ kind: 'fire', at_ms: 7, pressed: true })
    game.input({ kind: 'fire', at_ms: 8, pressed: false })
    game.advanceTo(1999)
    expect(game.scene().targets[0].id).toBe(scene.targets[0].id)
    game.input({ kind: 'fire', at_ms: 60000, pressed: true })
    const raw = game.finish()
    if (raw.kind !== 'aim') throw new Error('raw')
    expect(raw.trials.filter(t => t.hit)).toEqual([{ hit: true, latency_ms: 7 }])
    expect(raw.shots).toBe(1)
  })

  it('preserves seeded exact angular bins and never recenters a deliberately turned camera', () => {
    const config = fixtureConfig('pc-fps', 'precision')
    const game = createFpsEngine(config, 0, fixtureSettings('pc-fps'), viewport)
    let previous = { yaw_deg: 0, pitch_deg: 0 }
    const distances: number[] = []
    game.input({ kind: 'look', at_ms: 0, dx: 170, dy: -70 })
    for (let round = 0; round < 30; round++) {
      game.advanceTo(round * 2000)
      const scene = game.scene()
      if (scene.kind !== 'fps') throw new Error('scene')
      const position = scene.targets[0].position
      const current = vectorToDirection({ x: position.x, y: position.y - 1.6, z: position.z })
      distances.push(Math.round(angularDistanceDeg(previous, current)))
      expect(scene.camera.yaw_deg).toBe(170)
      expect(scene.camera.pitch_deg).toBe(70)
      previous = current
    }
    for (const distance of [10, 20, 30]) expect(distances.filter(x => x === distance)).toHaveLength(10)
  })
})
