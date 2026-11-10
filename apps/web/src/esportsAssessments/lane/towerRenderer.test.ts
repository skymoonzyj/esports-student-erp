import { describe, expect, it, vi } from 'vitest'
import { Vector3, type Mesh } from 'three'
import type { MobaSceneSnapshot } from '../engineRuntime/types'
import type { LaneSceneState, LaneTowerSnapshot, LaneUnitSnapshot } from './types'
import { createLaneScene } from './renderer'
import { laneWorldToScreen } from './projection'

const towers: readonly LaneTowerSnapshot[] = [
  { id: 'ally-rear', team: 'ally', position: { x: 70, y: 162.5 }, facing_rad: 0, state: 'idle', attack_progress: 0, target_id: null },
  { id: 'ally-front', team: 'ally', position: { x: 280, y: 400 }, facing_rad: 0, state: 'idle', attack_progress: 0, target_id: null },
  { id: 'enemy-front', team: 'enemy', position: { x: 720, y: 162.5 }, facing_rad: Math.PI, state: 'idle', attack_progress: 0, target_id: null },
  { id: 'enemy-rear', team: 'enemy', position: { x: 930, y: 400 }, facing_rad: Math.PI, state: 'idle', attack_progress: 0, target_id: null },
]
const soldier: LaneUnitSnapshot = { id: 'enemy-0', team: 'enemy', kind: 'melee', position: { x: 280, y: 400 }, facing_rad: 0, health: 400, max_health: 400, state: 'idle', attack_progress: 0, hit_at_ms: null }
function snapshot(overrides: Partial<LaneSceneState> = {}): MobaSceneSnapshot {
  return { kind: 'moba', at_ms: 100, logical_size: { width: 1000, height: 562.5 }, player: { id: 'hero', position: { x: 180, y: 420 }, radius: 18, facing_rad: 0 }, targets: [], projectiles: [], minimap: { visible: false, bounds: { x: 0, y: 0, width: 0, height: 0 }, event: null }, lane: { attack_mode: 'melee', units: [], projectiles: [], selected_target_id: null, move_destination: null, wave: 1, wave_count: 15, last_hits: 0, enemy_deaths: 0, missed: 0, towers, ...overrides } }
}

describe('real tower scene integration', () => {
  it('reconciles exactly four towers, reuses geometry, and removes all towers for v4/null scenes', () => {
    const view = createLaneScene()
    view.update(snapshot())
    expect(view.towers.size).toBe(4)
    const first = view.towers.get('ally-front')!
    const geometry = (first.getObjectByName('tower-column') as Mesh).geometry
    const dispose = vi.spyOn(geometry, 'dispose')
    view.update(snapshot({ towers: towers.map(tower => ({ ...tower, state: 'windup', attack_progress: .5 })) }))
    expect(view.towers.get('ally-front')).toBe(first)
    expect(first.getObjectByName('tower-charge')!.visible).toBe(true)
    expect(dispose).not.toHaveBeenCalled()
    view.update(snapshot({ towers: undefined, wave_count: 10 }))
    expect(view.towers.size).toBe(0)
    expect(first.parent).toBeNull()
    view.update(snapshot()); view.update(null)
    expect(view.towers.size).toBe(0)
    view.dispose(); view.dispose()
    expect(dispose).toHaveBeenCalledTimes(1)
  })

  it('blocks enemy picking with the actual nearest tower body but permits an enemy in front', () => {
    const view = createLaneScene()
    view.update(snapshot({ units: [soldier] }))
    const point = laneWorldToScreen(soldier.position, 23)
    expect(view.pick(point)).toBeNull()
    // Same projected silhouette, physically nearer the unchanged camera.
    view.units.get(soldier.id)!.position.add(new Vector3(0, 86.6025403784, 50))
    expect(view.pick(point)).toBe(soldier.id)
    view.update(snapshot({ units: [soldier], towers: undefined, wave_count: 10 }))
    expect(view.pick(point)).toBe(soldier.id)
    view.dispose()
  })

  it('does not treat the foundation, charge sphere, or muzzle flash as occluders', () => {
    const view = createLaneScene()
    view.update(snapshot({ towers: [{ ...towers[1], state: 'windup', attack_progress: 1 }] }))
    expect(view.towers.get('ally-front')!.getObjectByName('tower-charge')!.visible).toBe(true)
    expect(view.pick(laneWorldToScreen({ x: 305, y: 400 }, 2))).toBeUndefined()
    expect(view.pick(laneWorldToScreen({ x: 320, y: 400 }, 60))).toBeUndefined()
    view.update(snapshot({ towers: [{ ...towers[1], state: 'attacking', attack_progress: 0 }] }))
    expect(view.pick(laneWorldToScreen({ x: 323, y: 400 }, 60))).toBeUndefined()
    view.dispose()
  })

  it('renders tower missiles as larger distinct geometry and never includes them in picking', () => {
    const view = createLaneScene()
    const projectile = { id: 'tower-shot', team: 'enemy' as const, position: { x: 500, y: 300 }, target: { x: 600, y: 300 }, kind: 'tower' as const }
    view.update(snapshot({ projectiles: [projectile, { ...projectile, id: 'arrow', kind: undefined }] }))
    const missile = view.projectiles.get('tower-shot')!
    const arrow = view.projectiles.get('arrow')!
    expect(missile.geometry).not.toBe(arrow.geometry)
    expect(missile.scale.x).toBeGreaterThan(arrow.scale.x)
    expect(missile.position.y).toBeGreaterThan(arrow.position.y)
    expect(view.pick(laneWorldToScreen(projectile.position, missile.position.y))).toBeUndefined()
    view.update(snapshot({ projectiles: [{ ...projectile, position: { x: 520, y: 300 } }] }))
    expect(view.projectiles.get('tower-shot')).toBe(missile)
    expect(missile.position.x).toBe(520)
    view.dispose()
  })

  it('uses hero life status to hide a dead body without blocking enemies, then restores health and pose', () => {
    const view = createLaneScene()
    const hero: LaneUnitSnapshot = { ...soldier, id: 'hero', team: 'ally', kind: 'hero', position: { x: 500, y: 300 }, health: 1000, max_health: 1000 }
    const enemy = { ...soldier, position: hero.position }
    view.update(snapshot({ units: [hero, enemy], hero_status: { health: 0, max_health: 1000, deaths: 1, respawn_at_ms: 5100 } }))
    expect(view.units.get('hero')!.visible).toBe(false)
    expect(view.pick(laneWorldToScreen(enemy.position, 23))).toBe(enemy.id)
    view.update(snapshot({ units: [hero, enemy], hero_status: { health: 640, max_health: 1000, deaths: 1, respawn_at_ms: null } }))
    expect(view.units.get('hero')!.visible).toBe(true)
    expect(view.units.get('hero')!.getObjectByName('health-fill')!.scale.x).toBe(.64)
    view.dispose()
  })

  it('rejects nonfinite tower and life state atomically before altering scene objects', () => {
    const view = createLaneScene()
    view.update(snapshot())
    expect(() => view.update(snapshot({ towers: [{ ...towers[0], position: { x: NaN, y: 20 } }] }))).toThrow(/无效/)
    expect(() => view.update(snapshot({ hero_status: { health: NaN, max_health: 1000, deaths: 0, respawn_at_ms: null } }))).toThrow(/无效/)
    expect(view.towers.size).toBe(4)
    expect(view.towers.get('ally-rear')!.position.x).toBe(70)
    view.dispose()
  })
})
