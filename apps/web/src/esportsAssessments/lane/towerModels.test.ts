import { describe, expect, it, vi } from 'vitest'
import { Mesh, Vector3, type MeshStandardMaterial } from 'three'
import { createLaneResources } from './models'
import { laneWorldToScreen } from './projection'
import type { LaneTowerSnapshot } from './types'
import { createLaneTowerModel, updateLaneTowerModel } from './towerModels'

const towers: readonly LaneTowerSnapshot[] = [
  { id: 'ally-rear', team: 'ally', position: { x: 70, y: 162.5 }, facing_rad: 0, state: 'idle', attack_progress: 0, target_id: null },
  { id: 'ally-front', team: 'ally', position: { x: 280, y: 400 }, facing_rad: 0, state: 'idle', attack_progress: 0, target_id: null },
  { id: 'enemy-front', team: 'enemy', position: { x: 720, y: 162.5 }, facing_rad: Math.PI, state: 'idle', attack_progress: 0, target_id: null },
  { id: 'enemy-rear', team: 'enemy', position: { x: 930, y: 400 }, facing_rad: Math.PI, state: 'idle', attack_progress: 0, target_id: null },
]

describe('original faction towers', () => {
  it('places four solid original towers within the existing camera with opposing weapons and distinguishable ranks', () => {
    const resources = createLaneResources()
    const models = towers.map(tower => createLaneTowerModel(tower, resources))
    for (const [index, model] of models.entries()) {
      expect(model.position.toArray()).toEqual([towers[index].position.x, 0, towers[index].position.y])
      expect(model.getObjectByName('tower-weapon')!.rotation.y).toBe(-towers[index].facing_rad)
      expect(model.getObjectByName('health-bar')).toBeUndefined()
      expect(model.getObjectByName('rear-crown')?.visible ?? false).toBe(towers[index].id.endsWith('rear'))
      model.updateMatrixWorld(true)
      let solidVertices = 0
      model.getObjectByName('tower-body')!.traverse(object => {
        if (!(object instanceof Mesh)) return
        const positions = object.geometry.getAttribute('position')
        solidVertices += positions.count
        for (let vertex = 0; vertex < positions.count; vertex++) {
          const world = new Vector3().fromBufferAttribute(positions, vertex).applyMatrix4(object.matrixWorld)
          const screen = laneWorldToScreen({ x: world.x, y: world.z }, world.y)
          expect(screen.x).toBeGreaterThan(0); expect(screen.x).toBeLessThan(1000)
          expect(screen.y).toBeGreaterThan(0); expect(screen.y).toBeLessThan(562.5)
        }
      })
      expect(solidVertices).toBeGreaterThan(100)
    }
    const material = (index: number) => (models[index].getObjectByName('tower-banner') as Mesh).material as MeshStandardMaterial
    expect(material(0).color.getHex()).not.toBe(material(2).color.getHex())
    resources.dispose()
  })

  it('turns the weapon and displays charging and release feedback without replacing geometry', () => {
    const resources = createLaneResources()
    const model = createLaneTowerModel(towers[0], resources)
    const barrel = model.getObjectByName('tower-barrel') as Mesh
    const geometry = barrel.geometry
    expect(model.getObjectByName('tower-charge')!.visible).toBe(false)
    updateLaneTowerModel(model, { ...towers[0], facing_rad: Math.PI / 2, state: 'windup', attack_progress: .8 })
    expect(model.getObjectByName('tower-weapon')!.rotation.y).toBe(-Math.PI / 2)
    expect(model.getObjectByName('tower-charge')!.visible).toBe(true)
    const chargeSize = model.getObjectByName('tower-charge')!.scale.x
    updateLaneTowerModel(model, { ...towers[0], state: 'windup', attack_progress: .2 })
    expect(model.getObjectByName('tower-charge')!.scale.x).toBeLessThan(chargeSize)
    updateLaneTowerModel(model, { ...towers[0], state: 'attacking', attack_progress: .1 })
    expect(model.getObjectByName('tower-charge')!.visible).toBe(false)
    expect(model.getObjectByName('tower-muzzle-flash')!.visible).toBe(true)
    updateLaneTowerModel(model, towers[0])
    expect(model.getObjectByName('tower-muzzle-flash')!.visible).toBe(false)
    expect(barrel.geometry).toBe(geometry)
    resources.dispose()
  })

  it('shares tower resources and releases each pooled geometry once', () => {
    const resources = createLaneResources()
    const first = createLaneTowerModel(towers[0], resources)
    const second = createLaneTowerModel(towers[2], resources)
    const geometry = (first.getObjectByName('tower-barrel') as Mesh).geometry
    expect((second.getObjectByName('tower-barrel') as Mesh).geometry).toBe(geometry)
    const dispose = vi.spyOn(geometry, 'dispose')
    resources.dispose(); resources.dispose()
    expect(dispose).toHaveBeenCalledTimes(1)
  })
})
