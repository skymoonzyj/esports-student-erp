import { describe, expect, it } from 'vitest'
import { taskConfigSchema, validateRaw } from '../contracts'
import type { GameEngine, InputAction, MobaSceneSnapshot, Vector2 } from '../engineRuntime/types'
import { fixtureConfig, fixtureSettings } from '../testSupport/fixtures'
import { TouchInputRouter, type TouchLayout } from '../input/touch'
import { createSkillAimEngine } from './skillAim'

function scene(game: GameEngine): MobaSceneSnapshot {
  const value = game.scene()
  if (value.kind !== 'moba') throw new Error('scene')
  return value
}
function raw(game: GameEngine) {
  const value = game.finish()
  if (value.kind !== 'skill_aim') throw new Error('raw')
  return value
}
function create(seed = 4096) { return createSkillAimEngine(fixtureConfig('mobile-moba', 'skill-aim'), seed, fixtureSettings('mobile-moba')) }
function release(game: GameEngine, at_ms: number, target: Vector2) {
  game.input({ kind: 'skill', at_ms, ...target, release: false })
  game.input({ kind: 'skill', at_ms, ...target, release: true })
}
// Independent moving-target intercept: solve |relativePosition + velocity*t| = projectileSpeed*t.
function leadShot(position: Vector2, target: Vector2, velocity: Vector2): Vector2 {
  const x = target.x - position.x, y = target.y - position.y
  const a = velocity.x ** 2 + velocity.y ** 2 - 600 ** 2
  const b = 2 * (x * velocity.x + y * velocity.y)
  const c = x * x + y * y
  const time = (-b - Math.sqrt(b * b - 4 * a * c)) / (2 * a)
  return { x: target.x + velocity.x * time, y: target.y + velocity.y * time }
}

describe('mobile skill projectiles', () => {
  it('requires a nonzero finite release, cancels without firing, and cannot use a stale release after cancellation', () => {
    const game = create()
    const start = scene(game)
    game.input({ kind: 'skill', at_ms: 0, ...start.player.position, release: false })
    game.input({ kind: 'skill', at_ms: 1, ...start.player.position, release: true })
    game.input({ kind: 'skill', at_ms: 2, x: Number.NaN, y: 1, release: true })
    expect(raw(game).trials[0].released).toBe(false)
    game.input({ kind: 'skill', at_ms: 3, ...start.targets[0].position, release: false })
    game.input({ kind: 'skill', at_ms: 4, ...start.targets[0].position, release: false, cancel: true })
    game.input({ kind: 'skill', at_ms: 5, ...start.targets[0].position, release: true })
    expect(raw(game).trials[0].released).toBe(false)
    release(game, 10, start.targets[0].position)
    expect(raw(game).trials[0].released).toBe(true)
    expect(raw(game).trials[0].release_latency_ms).toBe(10)
    expect(raw(game).trials[0].hit).toBe(false)
  })

  it('does not award an instantaneous aim-at-release hit when the moving target escapes the actual projectile', () => {
    const game = create()
    const target = scene(game).targets[0].position
    release(game, 0, target)
    expect(raw(game).trials[0]).toMatchObject({ released: true, hit: false, angle_error_deg: 0, hit_latency_ms: null })
    game.advanceTo(1000)
    expect(raw(game).trials[0].hit).toBe(false)
  })

  it('hits a moving target by leading its future position and records the later collision time', () => {
    const game = create()
    const before = scene(game)
    game.advanceTo(10)
    const current = scene(game)
    const target = current.targets[0]
    const velocity = { x: (target.position.x - before.targets[0].position.x) / .01, y: 0 }
    expect(Math.abs(velocity.x)).toBeCloseTo(80, 7)
    const aim = leadShot(current.player.position, target.position, velocity)
    const dx = aim.x - current.player.position.x, dy = aim.y - current.player.position.y
    const initialCrossTrack = Math.abs((target.position.x - current.player.position.x) * dy - (target.position.y - current.player.position.y) * dx) / Math.hypot(dx, dy)
    expect(initialCrossTrack).toBeGreaterThan(target.radius + 10)
    release(game, 10, aim)
    expect(raw(game).trials[0].hit).toBe(false)
    game.advanceTo(900)
    expect(raw(game).trials[0].hit).toBe(true)
    expect(raw(game).trials[0].hit_latency_ms).toBeGreaterThan(10)
    expect(raw(game).trials[0].hit_latency_ms).toBeLessThan(900)
    expect(() => validateRaw(fixtureConfig('mobile-moba', 'skill-aim'), raw(game))).not.toThrow()
  })

  it('accepts only the first valid release in each of exactly 30 rounds and excludes end-boundary inputs', () => {
    const game = create(123)
    for (let round = 0; round < 30; round++) {
      const time = round * 2000
      game.advanceTo(time)
      release(game, time + 10, { x: -1000, y: -1000 })
      const first = scene(game).projectiles.find(p => p.type === 'skill' && p.state === 'active')!
      release(game, time + 20, scene(game).targets[0].position)
      expect(scene(game).projectiles[0].origin).toEqual(first.origin)
      expect(scene(game).projectiles[0].target).toEqual(first.target)
      expect(raw(game).trials[round].release_latency_ms).toBe(10)
    }
    game.advanceTo(60000)
    const before = raw(game)
    release(game, 60000, { x: 0, y: 0 })
    expect(raw(game)).toEqual(before)
    expect(before.trials).toHaveLength(30)
    expect(before.trials.every(t => t.released)).toBe(true)
    expect(() => validateRaw(fixtureConfig('mobile-moba', 'skill-aim'), before)).not.toThrow()
  })

  it('assigns a held gesture to the round of its valid release, including a new gesture after the prior round fired', () => {
    const game = create()
    game.input({ kind: 'skill', at_ms: 1990, x: 0, y: 0, release: false })
    game.input({ kind: 'skill', at_ms: 2010, x: 0, y: 0, release: true })
    expect(raw(game).trials[0].released).toBe(false)
    expect(raw(game).trials[1]).toMatchObject({ released: true, release_latency_ms: 10 })
    game.input({ kind: 'skill', at_ms: 3990, x: 0, y: 0, release: false })
    game.input({ kind: 'skill', at_ms: 4010, x: 0, y: 0, release: true })
    expect(raw(game).trials[2]).toMatchObject({ released: true, release_latency_ms: 10 })
  })

  it('expires the projectile at max distance, and a late miss does not carry into the next round', () => {
    const game = create()
    const start = scene(game).player.position
    release(game, 0, { x: start.x, y: start.y - 100 })
    game.advanceTo(900)
    const projectile = scene(game).projectiles[0]
    expect(Math.hypot(projectile.position.x - start.x, projectile.position.y - start.y)).toBeCloseTo(500, 8)
    expect(projectile.state).toBe('expired')
    game.advanceTo(2000)
    release(game, 3999, scene(game).targets[0].position)
    game.advanceTo(4000)
    expect(raw(game).trials[1]).toMatchObject({ released: true, hit: false, release_latency_ms: 1999 })
    expect(raw(game).trials[2].released).toBe(false)
    expect(scene(game).projectiles).toHaveLength(0)
  })

  it.each([1, 2, 3, 4, 5])('scales only target size and speed at practice level %i', difficulty => {
    const config = fixtureConfig('mobile-moba', 'skill-aim')
    if (config.params.kind !== 'skill_aim') throw new Error('config')
    const radius = 22 * [1.5, 1.25, 1, .85, .7][difficulty - 1]
    const speed = 80 * [.6, .8, 1, 1.15, 1.3][difficulty - 1]
    const practice = taskConfigSchema.parse({ ...config, difficulty, params: { ...config.params, target_radius: radius, target_speed: speed } })
    const game = createSkillAimEngine(practice, 123, fixtureSettings('mobile-moba'))
    const before = scene(game)
    release(game, 0, { x: before.player.position.x, y: -1000 })
    game.advanceTo(100)
    const after = scene(game)
    expect(after.targets[0].radius).toBe(radius)
    expect(Math.abs(after.targets[0].position.x - before.targets[0].position.x)).toBeCloseTo(speed / 10, 8)
    expect(Math.hypot(after.projectiles[0].position.x - before.player.position.x, after.projectiles[0].position.y - before.player.position.y)).toBeCloseTo(60, 8)
    expect(after.projectiles[0].radius).toBe(10)
    expect(after.minimap.visible).toBe(false)
  })

  it('routes current-player-relative drag and multiple fingers through the existing single input router', () => {
    const game = create()
    const actions: InputAction[] = []
    const layout: TouchLayout = {
      bounds: { x: 0, y: 0, width: 1000, height: 562.5 }, logical_size: { width: 1000, height: 562.5 }, sensitivity: 1,
      controls: fixtureSettings('mobile-moba').controls,
      get_skill_origin: () => scene(game).player.position,
    }
    const router = new TouchInputRouter(layout, action => { actions.push(action); game.input(action) })
    router.down({ pointerId: 1, x: 180, y: 421.875, at_ms: 0 })
    router.down({ pointerId: 2, x: 700, y: 365.625, at_ms: 0, isPrimary: false })
    game.advanceTo(100)
    const player = scene(game).player.position
    router.up({ pointerId: 2, x: 800, y: 365.625, at_ms: 100 })
    expect(actions.filter(a => a.kind === 'skill' && a.release)).toEqual([{ kind: 'skill', at_ms: 100, x: player.x + 100, y: player.y, release: true }])
    expect(raw(game).trials[0].released).toBe(true)
    expect(router.activeCount).toBe(1)
    router.cancel(110)
    expect(router.activeCount).toBe(0)
    game.advanceTo(2000)
    router.down({ pointerId: 3, x: 700, y: 365.625, at_ms: 2000 })
    router.cancel(2010)
    router.up({ pointerId: 3, x: 800, y: 365.625, at_ms: 2020 })
    expect(raw(game).trials[1].released).toBe(false)
  })

  it('reflects the whole moving target at world edges without changing speed', () => {
    const game = create(123)
    game.input({ kind: 'point', at_ms: 0, x: 1000, y: 0, button: 2 })
    game.advanceTo(4000)
    expect(scene(game).player.position).toEqual({ x: 988, y: 12 })
    let previous = scene(game).targets[0].position.x
    let signs: number[] = []
    let bounced = false
    for (let time = 4010; time < 60000; time += 10) {
      game.advanceTo(time)
      const target = scene(game).targets[0]
      expect(target.position.x).toBeGreaterThanOrEqual(22)
      expect(target.position.x).toBeLessThanOrEqual(978)
      if (time % 2000 === 0) signs = []
      else {
        const distance = target.position.x - previous
        expect(Math.abs(distance)).toBeLessThanOrEqual(.8 + 1e-8)
        if (Math.abs(distance) > .79) signs.push(Math.sign(distance))
        if (signs.includes(-1) && signs.includes(1)) bounced = true
      }
      previous = target.position.x
    }
    expect(bounced).toBe(true)
  })

  it('reproduces actual moving-target hit results across rendering cadences', () => {
    const run = (hz: number) => {
      const game = create()
      const start = scene(game)
      game.advanceTo(10)
      const current = scene(game)
      release(game, 10, leadShot(current.player.position, current.targets[0].position, { x: (current.targets[0].position.x - start.targets[0].position.x) / .01, y: 0 }))
      for (let i = 1; i <= hz * 60; i++) game.advanceTo(i * 1000 / hz)
      return raw(game)
    }
    const a = run(30), b = run(59), c = run(120)
    expect(a.trials[0].hit).toBe(true)
    expect(a.trials[0].hit_latency_ms).toBeCloseTo(b.trials[0].hit_latency_ms!, 6)
    expect(b.trials[0].hit_latency_ms).toBeCloseTo(c.trials[0].hit_latency_ms!, 6)
    expect(a.trials.slice(1)).toEqual(b.trials.slice(1))
    expect(b.trials.slice(1)).toEqual(c.trials.slice(1))
    expect(run(30)).toEqual(a)
  })
})
