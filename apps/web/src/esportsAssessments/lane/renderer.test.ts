import { describe, expect, it, vi } from 'vitest'
import type * as ThreeModule from 'three'
import type { Mesh } from 'three'
import type { MobaSceneSnapshot } from '../engineRuntime/types'
import type { LaneUnitSnapshot } from './types'
import { createLaneRenderer, createLaneScene } from './renderer'

const gpu = vi.hoisted(() => ({ sizes: [] as number[][], ratios: [] as number[], disposed: 0, renders: 0 }))
vi.mock('three', async importOriginal => {
  const actual = await importOriginal<typeof ThreeModule>()
  return { ...actual, WebGLRenderer: class {
    shadowMap = { enabled: false }
    setPixelRatio(value: number) { gpu.ratios.push(value) }
    setSize(width: number, height: number) { gpu.sizes.push([width, height]) }
    render() { gpu.renders++ }
    dispose() { gpu.disposed++ }
  } }
})
const soldier: LaneUnitSnapshot = { id: 'enemy-0', team: 'enemy', kind: 'melee', position: { x: 500, y: 280 }, facing_rad: Math.PI, health: 200, max_health: 400, state: 'moving', attack_progress: 0, hit_at_ms: null }
export function snapshot(units: readonly LaneUnitSnapshot[] = [soldier]): MobaSceneSnapshot {
  return { kind: 'moba', at_ms: 120, logical_size: { width: 1000, height: 562.5 }, player: { id: 'hero', position: { x: 400, y: 420 }, radius: 18, facing_rad: 0 }, targets: [], projectiles: [], minimap: { visible: false, bounds: { x: 0, y: 0, width: 0, height: 0 }, event: null }, lane: { attack_mode: 'melee', units, projectiles: [{ id: 'arrow-1', team: 'ally', position: { x: 450, y: 280 }, target: { x: 500, y: 280 } }], selected_target_id: 'enemy-0', move_destination: { x: 600, y: 420 }, wave: 1, wave_count: 10, last_hits: 0, enemy_deaths: 0, missed: 0 } }
}

describe('lane scene reconciliation', () => {
  it('preserves live model identities and deletes only disappeared entities', () => {
    const view = createLaneScene()
    view.update(snapshot([soldier, { ...soldier, id: 'enemy-1' }]))
    const kept = view.units.get('enemy-0')!
    const removed = view.units.get('enemy-1')!
    const geometry = (kept.getObjectByName('torso') as Mesh).geometry
    const disposal = vi.spyOn(geometry, 'dispose')
    view.update(snapshot([{ ...soldier, position: { x: 530, y: 285 } }]))
    expect(view.units.get('enemy-0')).toBe(kept)
    expect(kept.position.x).toBe(530)
    expect(removed.parent).toBeNull()
    expect(disposal).not.toHaveBeenCalled()
    expect(kept.getObjectByName('selection')!.visible).toBe(true)
    expect(view.projectiles.get('arrow-1')!.position.x).toBe(450)
    view.dispose()
    expect(disposal).toHaveBeenCalledTimes(1)
    expect(view.scene.children).toHaveLength(0)
  })
  it('rejects nonfinite entity state before corrupting the live scene', () => {
    const view = createLaneScene()
    view.update(snapshot())
    expect(() => view.update(snapshot([{ ...soldier, health: NaN }]))).toThrow(/无效/)
    expect(view.units.get('enemy-0')!.position.x).toBe(500)
    view.dispose()
  })
  it('resizes DPR1/2 without moving the logical camera and ignores disposed renders', () => {
    const renderer = createLaneRenderer(document.createElement('canvas'))
    renderer.resize(1000, 562.5, 1); renderer.resize(800, 450, 2)
    expect(gpu.sizes.slice(-2)).toEqual([[1000, 562.5], [800, 450]])
    expect(gpu.ratios.slice(-2)).toEqual([1, 2])
    for (const dimensions of [[0, 100, 1], [100, NaN, 1], [100, 100, Infinity]]) expect(() => renderer.resize(...dimensions as [number, number, number])).toThrow(/无效/)
    renderer.render(snapshot())
    const renders = gpu.renders
    renderer.dispose(); renderer.dispose(); renderer.render(snapshot())
    expect(gpu.renders).toBe(renders)
  })
  it('does not reallocate an unchanged drawing buffer for successive scene frames', () => {
    const renderer = createLaneRenderer(document.createElement('canvas'))
    const sizeCount = gpu.sizes.length
    const ratioCount = gpu.ratios.length
    renderer.resize(800, 450, 1)
    renderer.render(snapshot())
    renderer.resize(800, 450, 1)
    renderer.render({ ...snapshot(), at_ms: 140 })
    expect(gpu.sizes.length - sizeCount).toBe(1)
    expect(gpu.ratios.length - ratioCount).toBe(1)
    renderer.resize(900, 506.25, 1)
    expect(gpu.sizes.length - sizeCount).toBe(2)
    expect(gpu.ratios.length - ratioCount).toBe(1)
    renderer.resize(900, 506.25, 2)
    expect(gpu.sizes.length - sizeCount).toBe(3)
    expect(gpu.ratios.length - ratioCount).toBe(2)
    renderer.dispose()
  })
  it('keeps the same GPU buffer through tower charge, release and hero respawn frames', () => {
    const renderer = createLaneRenderer(document.createElement('canvas'))
    const sizeCount = gpu.sizes.length
    const ratioCount = gpu.ratios.length
    for (const [index, state] of (['idle', 'windup', 'attacking', 'idle'] as const).entries()) {
      const source = snapshot()
      renderer.resize(1000, 562.5, 2)
      renderer.render({ ...source, at_ms: index * 200, lane: { ...source.lane!, wave_count: 15, towers: [{ id: 'ally-front', team: 'ally', position: { x: 280, y: 400 }, facing_rad: index / 2, state, attack_progress: .3, target_id: 'enemy-0' }], hero_status: { health: index === 2 ? 0 : 1000, max_health: 1000, deaths: index >= 2 ? 1 : 0, respawn_at_ms: index === 2 ? 5400 : null } } })
    }
    expect(gpu.sizes.length - sizeCount).toBe(1)
    expect(gpu.ratios.length - ratioCount).toBe(1)
    renderer.dispose()
  })
})
