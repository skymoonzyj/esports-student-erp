import { describe, expect, it } from 'vitest'
import { Vector3 } from 'three'
import { createLaneScene } from './renderer'
import { createLaneEngine } from './engine'
import { LaneSimulation } from './simulation'
import { laneScreenToWorld } from './projection'
import { fixtureMobaV4Config } from '../testSupport/fixturesMobaV4'
import { fixtureSettings } from '../testSupport/fixtures'
import type { MobaSceneSnapshot, Vector2 } from '../engineRuntime/types'
import type { LaneUnitSnapshot } from './types'

function screenPoint(view: ReturnType<typeof createLaneScene>, id: string, height = 27): Vector2 {
  view.scene.updateMatrixWorld(true)
  const point = view.units.get(id)!.localToWorld(new Vector3(0, height, 0)).project(view.camera)
  return { x: (point.x + 1) * 500, y: (1 - point.y) * 281.25 }
}

describe('visible lane body picking', () => {
  it.each(['melee', 'ranged'] as const)('selects the crowded helmet, not its nearer-foot neighbor (%s)', mode => {
    const engine = createLaneEngine(fixtureMobaV4Config(mode), 1, fixtureSettings('pc-moba'))
    engine.advanceTo(30600)
    const view = createLaneScene()
    view.update(engine.scene() as MobaSceneSnapshot)
    const point = screenPoint(view, 'enemy-1-2')
    const ground = laneScreenToWorld(point)!
    // Independent no-input real simulation reproduces the reported wrong neighbor.
    const legacyPick = new LaneSimulation(mode, 1)
    legacyPick.input({ kind: 'point', at_ms: 30600, ...ground, button: 0 })
    expect(legacyPick.snapshot().selected_target_id).toBe('enemy-1-1')
    const target_id = view.pick?.(point)
    engine.input({ kind: 'point', at_ms: 30600, ...ground, target_id, button: 0 })
    expect((engine.scene() as MobaSceneSnapshot).lane!.selected_target_id).toBe('enemy-1-2')
    view.dispose()
  })

  it('uses frontmost body depth, blocks behind friendly/dead bodies, and breaks equal-depth ties by ID', () => {
    const engine = createLaneEngine(fixtureMobaV4Config('melee'), 1, fixtureSettings('pc-moba'))
    const source = engine.scene() as MobaSceneSnapshot
    const soldier = source.lane!.units.find(unit => unit.id === 'enemy-0-0')!
    const view = createLaneScene()
    const at = { x: 500, y: 280 }
    const a: LaneUnitSnapshot = { ...soldier, id: 'a', position: at }
    const b: LaneUnitSnapshot = { ...a, id: 'b' }
    const update = (units: LaneUnitSnapshot[]) => view.update({ ...source, lane: { ...source.lane!, units } })
    update([b, a])
    const point = screenPoint(view, 'a')
    expect(view.pick?.(point)).toBe('a')
    update([a, b])
    expect(view.pick?.(point)).toBe('a')
    // Move the second actual body toward the camera along the same ray.
    view.units.get('b')!.position.add(new Vector3(0, 8.66025403784, 5))
    expect(view.pick?.(point)).toBe('b')
    for (const blocked of [{ team: 'ally' as const }, { health: 0, state: 'dying' as const }]) {
      update([a, { ...b, ...blocked }])
      view.units.get('b')!.position.add(new Vector3(0, 8.66025403784, 5))
      expect(view.pick?.(point)).toBeNull()
    }
    view.dispose()
  })

  it('ignores health-bar and effect geometry and leaves empty ground to foot picking/movement', () => {
    const engine = createLaneEngine(fixtureMobaV4Config('melee'), 1, fixtureSettings('pc-moba'))
    const view = createLaneScene()
    view.update(engine.scene() as MobaSceneSnapshot)
    const healthEdge = screenPoint(view, 'enemy-0-0', 35)
    expect(view.pick?.({ ...healthEdge, x: healthEdge.x + 12 })).toBeUndefined()
    expect(view.pick?.({ x: 400, y: 450 })).toBeUndefined()
    const ground = laneScreenToWorld({ x: 400, y: 450 })!
    engine.input({ kind: 'point', at_ms: 0, ...ground, target_id: view.pick?.({ x: 400, y: 450 }), button: 2 })
    expect((engine.scene() as MobaSceneSnapshot).lane!.move_destination).toEqual(ground)
    view.dispose()
  })

  it.each(['melee', 'ranged'] as const)('validates explicit target identity at input time and never retargets stale picks (%s)', mode => {
    const game = new LaneSimulation(mode, 1)
    game.input({ kind: 'point', at_ms: 0, x: 900, y: 247.25, target_id: 'enemy-0-1', button: 0 })
    expect(game.snapshot().selected_target_id).toBe('enemy-0-1')
    for (const target_id of ['missing', 'hero', 'ally-0-0']) {
      game.input({ kind: 'point', at_ms: 0, x: 900, y: 247.25, target_id, button: 0 })
      expect(game.snapshot().selected_target_id).toBe('enemy-0-1')
    }
    // A blocked model explicitly suppresses ground-neighbor selection.
    game.input({ kind: 'point', at_ms: 0, x: 900, y: 247.25, target_id: null, button: 2 })
    expect(game.snapshot().selected_target_id).toBeNull()
    expect(game.snapshot().move_destination).toEqual({ x: 900, y: 247.25 })
    const untouched = new LaneSimulation(mode, 1)
    let dead: LaneUnitSnapshot | undefined
    for (let at = 20; at < 30000 && !dead; at += 20) {
      untouched.advanceTo(at)
      dead = untouched.snapshot().units.find(unit => unit.team === 'enemy' && unit.health === 0)
    }
    expect(dead).toBeDefined()
    const alive = untouched.snapshot().units.find(unit => unit.team === 'enemy' && unit.health > 0)!
    untouched.input({ kind: 'point', at_ms: untouched.at_ms, ...alive.position, target_id: dead!.id, button: 0 })
    expect(untouched.snapshot().selected_target_id).toBeNull()
    expect(untouched.snapshot().move_destination).toBeNull()
  })
})
