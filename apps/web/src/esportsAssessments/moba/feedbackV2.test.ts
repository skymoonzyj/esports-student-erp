import { describe, expect, it } from 'vitest'
import { previewTaskConfig, type TaskConfig } from '../contracts'
import type { GameEngine, MobaSceneSnapshot, Vector2 } from '../engineRuntime/types'
import { fixtureConfig, fixtureCurrentConfig, fixtureSettings } from '../testSupport/fixtures'
import { createDodgeEngine } from './dodge'
import { createLastHitEngine } from './lastHit'
import { createMapEngine } from './mapAwareness'
import { createRhythmEngine } from './rhythm'
import { createSkillAimEngine } from './skillAim'

type FeedbackScene = MobaSceneSnapshot & {
  feedback: {
    version: 'v2'
    task_id: string
    round: { current: number; total: number; status: 'active' | 'success' | 'failure' }
    progress?: 'attack-ready' | 'attack-windup' | 'attack-completed' | 'attack-cancelled'
    outcomes: readonly { objective: string; round: number; result: 'success' | 'failure'; reason?: string }[]
  }
}

function scene(game: GameEngine): FeedbackScene {
  const value = game.scene()
  if (value.kind !== 'moba') throw new Error('MOBA scene required')
  return value as FeedbackScene
}

function current(suite: 'pc-moba' | 'mobile-moba', test: string, difficulty = 3): TaskConfig {
  return previewTaskConfig(fixtureCurrentConfig(suite, test), difficulty)
}

function leadShot(position: Vector2, target: Vector2, velocity: Vector2): Vector2 {
  const x = target.x - position.x, y = target.y - position.y
  const a = velocity.x ** 2 + velocity.y ** 2 - 600 ** 2
  const b = 2 * (x * velocity.x + y * velocity.y)
  const c = x * x + y * y
  const time = (-b - Math.sqrt(b * b - 4 * a * c)) / (2 * a)
  return { x: target.x + velocity.x * time, y: target.y + velocity.y * time }
}

describe('MOBA v2 input compatibility', () => {
  it.each([1, 2, 3, 4, 5])('treats left and right target commands equivalently at PC difficulty %i', difficulty => {
    const lastHit = (button: 0 | 2) => {
      const game = createLastHitEngine(current('pc-moba', 'last-hit', difficulty), 123, fixtureSettings('pc-moba'))
      game.input({ kind: 'point', at_ms: 0, ...scene(game).targets[0].position, button })
      game.advanceTo(250)
      return game.finish()
    }
    const rhythm = (button: 0 | 2) => {
      const game = createRhythmEngine(current('pc-moba', 'orb-walk', difficulty), 123, fixtureSettings('pc-moba'))
      game.input({ kind: 'point', at_ms: 0, ...scene(game).targets.find(target => target.type === 'enemy')!.position, button })
      game.advanceTo(250)
      return game.finish()
    }
    const dodge = (button: 0 | 2) => {
      const game = createDodgeEngine(current('pc-moba', 'dodge', difficulty), 123, fixtureSettings('pc-moba'))
      game.input({ kind: 'point', at_ms: 0, x: 900, y: 281.25, button })
      game.advanceTo(100)
      return scene(game).player.position
    }

    expect(lastHit(0)).toEqual(lastHit(2))
    expect(rhythm(0)).toEqual(rhythm(2))
    expect(dodge(0)).toEqual(dodge(2))
  })

  it('keeps v1 left clicks inert outside the existing map click behavior', () => {
    const lastHit = createLastHitEngine(fixtureConfig('pc-moba', 'last-hit'), 123, fixtureSettings('pc-moba'))
    lastHit.input({ kind: 'point', at_ms: 0, ...scene(lastHit).targets[0].position, button: 0 })
    lastHit.advanceTo(250)
    const lastHitRaw = lastHit.finish()
    if (lastHitRaw.kind !== 'last_hit') throw new Error('last-hit raw required')
    expect(lastHitRaw.waves[0].early).toBe(0)

    const rhythm = createRhythmEngine(fixtureConfig('pc-moba', 'orb-walk'), 123, fixtureSettings('pc-moba'))
    rhythm.input({ kind: 'point', at_ms: 0, ...scene(rhythm).targets.find(target => target.type === 'enemy')!.position, button: 0 })
    rhythm.advanceTo(250)
    const rhythmRaw = rhythm.finish()
    if (rhythmRaw.kind !== 'rhythm') throw new Error('rhythm raw required')
    expect(rhythmRaw.rounds[0].attacked).toBe(false)

    const dodge = createDodgeEngine(fixtureConfig('pc-moba', 'dodge'), 123, fixtureSettings('pc-moba'))
    dodge.input({ kind: 'point', at_ms: 0, x: 900, y: 281.25, button: 0 })
    dodge.advanceTo(100)
    expect(scene(dodge).player.position).toEqual({ x: 500, y: 281.25 })

    const map = createMapEngine(fixtureConfig('pc-moba', 'map-awareness'), 123, fixtureSettings('pc-moba'))
    map.input({ kind: 'point', at_ms: 0, ...scene(map).targets[0].position, button: 0 })
    const mapRaw = map.finish()
    if (mapRaw.kind !== 'map') throw new Error('map raw required')
    expect(mapRaw.main_hits[0]).toBe(true)
  })

  it('keeps raw scoring and geometry identical under the same legacy inputs', () => {
    const compare = (legacy: GameEngine, current: GameEngine, act: (game: GameEngine) => void) => {
      act(legacy)
      act(current)
      const legacyScene = scene(legacy)
      const currentScene = scene(current)
      const currentGeometry = structuredClone(currentScene)
      Reflect.deleteProperty(currentGeometry, 'feedback')
      expect(currentGeometry).toEqual(legacyScene)
      expect(current.finish()).toEqual(legacy.finish())
    }

    compare(
      createLastHitEngine(fixtureConfig('pc-moba', 'last-hit'), 321, fixtureSettings('pc-moba')),
      createLastHitEngine(current('pc-moba', 'last-hit'), 321, fixtureSettings('pc-moba')),
      game => {
        game.input({ kind: 'point', at_ms: 0, ...scene(game).targets[0].position, button: 2 })
        game.input({ kind: 'point', at_ms: 500, x: 900, y: 400, button: 2 })
        game.advanceTo(4000)
      },
    )
    compare(
      createRhythmEngine(fixtureConfig('pc-moba', 'orb-walk'), 321, fixtureSettings('pc-moba')),
      createRhythmEngine(current('pc-moba', 'orb-walk'), 321, fixtureSettings('pc-moba')),
      game => {
        const initial = scene(game)
        game.input({ kind: 'point', at_ms: 0, ...initial.targets.find(target => target.type === 'enemy')!.position, button: 2 })
        game.input({ kind: 'point', at_ms: 250, ...initial.targets.find(target => target.type === 'checkpoint')!.position, button: 2 })
        game.advanceTo(2000)
      },
    )
    compare(
      createDodgeEngine(fixtureConfig('pc-moba', 'dodge'), 321, fixtureSettings('pc-moba')),
      createDodgeEngine(current('pc-moba', 'dodge'), 321, fixtureSettings('pc-moba')),
      game => {
        game.input({ kind: 'point', at_ms: 0, x: 900, y: 281.25, button: 2 })
        game.advanceTo(2000)
      },
    )
    compare(
      createMapEngine(fixtureConfig('pc-moba', 'map-awareness'), 321, fixtureSettings('pc-moba')),
      createMapEngine(current('pc-moba', 'map-awareness'), 321, fixtureSettings('pc-moba')),
      game => {
        game.input({ kind: 'point', at_ms: 0, ...scene(game).targets[0].position, button: 0 })
        game.advanceTo(3000)
        game.input({ kind: 'point', at_ms: 3000, ...scene(game).minimap.event!.position, button: 0 })
      },
    )
    compare(
      createRhythmEngine(fixtureConfig('mobile-moba', 'move-attack'), 321, fixtureSettings('mobile-moba')),
      createRhythmEngine(current('mobile-moba', 'move-attack'), 321, fixtureSettings('mobile-moba')),
      game => {
        game.input({ kind: 'fire', at_ms: 0, pressed: true })
        game.advanceTo(300)
      },
    )
    compare(
      createSkillAimEngine(fixtureConfig('mobile-moba', 'skill-aim'), 321, fixtureSettings('mobile-moba')),
      createSkillAimEngine(current('mobile-moba', 'skill-aim'), 321, fixtureSettings('mobile-moba')),
      game => {
        const target = scene(game).targets[0].position
        game.input({ kind: 'skill', at_ms: 0, ...target, release: false })
        game.input({ kind: 'skill', at_ms: 0, ...target, release: true })
        game.advanceTo(900)
      },
    )
  })
})

describe('MOBA v2 observed feedback', () => {
  it.each([
    ['pc-moba', 'last-hit', createLastHitEngine],
    ['pc-moba', 'orb-walk', createRhythmEngine],
    ['pc-moba', 'dodge', createDodgeEngine],
    ['pc-moba', 'map-awareness', createMapEngine],
    ['mobile-moba', 'skill-aim', createSkillAimEngine],
    ['mobile-moba', 'move-attack', createRhythmEngine],
  ] as const)('adds pure feedback only to current %s %s snapshots', (suite, test, create) => {
    const game = create(current(suite, test), 123, fixtureSettings(suite))
    const beforeRaw = game.finish()
    const first = scene(game)
    const second = scene(game)
    expect(first.feedback).toMatchObject({ version: 'v2', task_id: test, round: { current: 1, status: 'active' }, outcomes: [] })
    expect(second).toEqual(first)
    expect(game.finish()).toEqual(beforeRaw)
    expect(first.at_ms).toBe(0)

    const legacy = create(fixtureConfig(suite, test), 123, fixtureSettings(suite))
    expect(legacy.scene()).not.toHaveProperty('feedback')
  })

  it('reports last-hit attack progress and only observed early/success outcomes', () => {
    const early = createLastHitEngine(current('pc-moba', 'last-hit'), 123, fixtureSettings('pc-moba'))
    early.input({ kind: 'point', at_ms: 0, ...scene(early).targets[0].position, button: 0 })
    expect(scene(early).feedback.progress).toBe('attack-windup')
    early.advanceTo(249)
    expect(scene(early).feedback.outcomes).toEqual([])
    early.advanceTo(250)
    expect(scene(early).feedback).toMatchObject({ round: { status: 'active' }, progress: 'attack-completed', outcomes: [{ objective: 'last-hit', round: 1, result: 'failure', reason: 'early' }] })

    const winner = createLastHitEngine(current('pc-moba', 'last-hit'), 123, fixtureSettings('pc-moba'))
    for (const time of [2000, 2250, 2500, 2750, 3000]) {
      winner.advanceTo(time)
      if (scene(winner).targets[0].health_ratio! <= .1) {
        winner.input({ kind: 'point', at_ms: time, ...scene(winner).targets[0].position, button: 0 })
        winner.advanceTo(time + 250)
        break
      }
    }
    expect(scene(winner).feedback).toMatchObject({ round: { status: 'success' }, outcomes: [{ objective: 'last-hit', round: 1, result: 'success' }] })
  })

  it('confirms rhythm success only at round end and reports moving mobile attacks as cancelled', () => {
    const game = createRhythmEngine(current('pc-moba', 'orb-walk'), 123, fixtureSettings('pc-moba'))
    const start = scene(game)
    const enemy = start.targets.find(target => target.type === 'enemy')!
    const checkpoint = start.targets.find(target => target.type === 'checkpoint')!
    game.input({ kind: 'point', at_ms: 0, ...enemy.position, button: 0 })
    expect(scene(game).feedback.progress).toBe('attack-windup')
    game.advanceTo(250)
    expect(scene(game).feedback).toMatchObject({ round: { current: 1, status: 'active' }, progress: 'attack-completed' })
    game.input({ kind: 'point', at_ms: 250, ...checkpoint.position, button: 2 })
    game.advanceTo(1999)
    expect(scene(game).feedback.round.status).toBe('active')
    game.advanceTo(2000)
    expect(scene(game).feedback).toMatchObject({ round: { current: 2, status: 'active' } })
    expect(scene(game).feedback.outcomes).toEqual([
      { objective: 'attack', round: 1, result: 'success' },
      { objective: 'checkpoint', round: 1, result: 'success' },
    ])

    const mobile = createRhythmEngine(current('mobile-moba', 'move-attack'), 123, fixtureSettings('mobile-moba'))
    mobile.input({ kind: 'move', at_ms: 0, x: 1, y: 0 })
    mobile.input({ kind: 'fire', at_ms: 100, pressed: true })
    expect(scene(mobile).feedback).toMatchObject({ progress: 'attack-cancelled', outcomes: [{ objective: 'attack', round: 1, result: 'failure', reason: 'cancelled' }] })
  })

  it('reports dodge hits immediately and successful dodges only after the round ends', () => {
    const hit = createDodgeEngine(current('pc-moba', 'dodge'), 123, fixtureSettings('pc-moba'))
    hit.advanceTo(1200)
    expect(scene(hit).feedback).toMatchObject({ round: { current: 1, status: 'failure' }, outcomes: [{ objective: 'dodge', round: 1, result: 'failure', reason: 'hit' }] })

    const dodge = createDodgeEngine(current('pc-moba', 'dodge'), 123, fixtureSettings('pc-moba'))
    const threat = scene(dodge).projectiles[0]
    const dx = (threat.target.x - threat.origin.x) / 240, dy = (threat.target.y - threat.origin.y) / 240
    dodge.input({ kind: 'move', at_ms: 0, x: -dy, y: dx })
    dodge.advanceTo(2000)
    expect(scene(dodge).feedback.outcomes).toEqual([{ objective: 'dodge', round: 1, result: 'success' }])
  })

  it('reports main-map and minimap outcomes without changing map left-click behavior', () => {
    const game = createMapEngine(current('pc-moba', 'map-awareness'), 123, fixtureSettings('pc-moba'))
    game.input({ kind: 'point', at_ms: 0, ...scene(game).targets[0].position, button: 0 })
    expect(scene(game).feedback).toMatchObject({ round: { status: 'success' }, outcomes: [{ objective: 'main-target', round: 1, result: 'success' }] })
    game.advanceTo(3000)
    const event = scene(game).minimap.event!
    const wrong = { x: event.position.x === 865 ? 955 : 865, y: event.position.y }
    game.input({ kind: 'point', at_ms: 3000, ...wrong, button: 0 })
    expect(scene(game).feedback.outcomes).toContainEqual({ objective: 'map-event', round: 1, result: 'failure', reason: 'wrong' })
  })

  it('reports skill cancellation, expiry, and a real moving-target hit without auto aim', () => {
    const cancelled = createSkillAimEngine(current('mobile-moba', 'skill-aim'), 123, fixtureSettings('mobile-moba'))
    const initial = scene(cancelled)
    cancelled.input({ kind: 'skill', at_ms: 0, ...initial.targets[0].position, release: false })
    cancelled.input({ kind: 'skill', at_ms: 1, ...initial.targets[0].position, release: false, cancel: true })
    expect(scene(cancelled).feedback.outcomes).toEqual([{ objective: 'skill-aim', round: 1, result: 'failure', reason: 'cancelled' }])

    const missed = createSkillAimEngine(current('mobile-moba', 'skill-aim'), 123, fixtureSettings('mobile-moba'))
    const player = scene(missed).player.position
    missed.input({ kind: 'skill', at_ms: 0, x: player.x, y: -1000, release: false })
    missed.input({ kind: 'skill', at_ms: 0, x: player.x, y: -1000, release: true })
    missed.advanceTo(900)
    expect(scene(missed).feedback).toMatchObject({ round: { status: 'failure' }, outcomes: [{ objective: 'skill-aim', round: 1, result: 'failure', reason: 'missed' }] })

    const hit = createSkillAimEngine(current('mobile-moba', 'skill-aim'), 123, fixtureSettings('mobile-moba'))
    const before = scene(hit)
    hit.advanceTo(10)
    const at = scene(hit)
    const aim = leadShot(at.player.position, at.targets[0].position, { x: (at.targets[0].position.x - before.targets[0].position.x) / .01, y: 0 })
    hit.input({ kind: 'skill', at_ms: 10, ...aim, release: false })
    hit.input({ kind: 'skill', at_ms: 10, ...aim, release: true })
    hit.advanceTo(900)
    expect(scene(hit).feedback).toMatchObject({ round: { status: 'success' }, outcomes: [{ objective: 'skill-aim', round: 1, result: 'success' }] })
    hit.input({ kind: 'skill', at_ms: 901, ...aim, release: false })
    hit.input({ kind: 'skill', at_ms: 902, ...aim, release: false, cancel: true })
    expect(scene(hit).feedback.outcomes).toEqual([{ objective: 'skill-aim', round: 1, result: 'success' }])
  })

  it('does not consume schedule randomness or alter raw results when scenes are sampled densely', () => {
    const create = () => createRhythmEngine(current('pc-moba', 'orb-walk'), 987, fixtureSettings('pc-moba'))
    const sparse = create(), dense = create()
    for (let round = 0; round < 30; round++) {
      const at = round * 2000
      sparse.advanceTo(at); dense.advanceTo(at)
      const sparseTarget = scene(sparse).targets.find(target => target.type === 'enemy')!
      const denseTarget = scene(dense).targets.find(target => target.type === 'enemy')!
      sparse.input({ kind: 'point', at_ms: at, ...sparseTarget.position, button: 2 })
      dense.input({ kind: 'point', at_ms: at, ...denseTarget.position, button: 2 })
      for (let time = at; time < at + 2000; time += 1000 / 144) { dense.advanceTo(time); dense.scene() }
      sparse.advanceTo(at + 2000); dense.advanceTo(at + 2000)
      expect(dense.scene()).toEqual(sparse.scene())
    }
    expect(dense.finish()).toEqual(sparse.finish())
  })
})
