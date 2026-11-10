import { describe, expect, it, vi } from 'vitest'
import { Box3, Mesh, Vector3, type MeshStandardMaterial } from 'three'
import type { LaneUnitSnapshot } from './types'
import { createLaneResources, createLaneUnitModel, updateLaneUnitModel } from './models'
import { laneScreenToWorld, laneWorldToScreen } from './projection'

const unit: LaneUnitSnapshot = { id: 'enemy-0', team: 'enemy', kind: 'melee', position: { x: 500, y: 281.25 }, facing_rad: 0, health: 100, max_health: 400, state: 'idle', attack_progress: 0, hit_at_ms: null }

describe('original lane humanoids', () => {
  it('builds articulated bodies with distinct weapons and faction silhouettes', () => {
    const resources = createLaneResources()
    const melee = createLaneUnitModel(unit, 'melee', resources)
    const ranged = createLaneUnitModel({ ...unit, kind: 'ranged', team: 'ally' }, 'ranged', resources)
    for (const name of ['head', 'torso', 'left-leg', 'right-leg', 'left-arm', 'right-arm', 'sword', 'shield']) expect(melee.getObjectByName(name)).toBeDefined()
    expect(ranged.getObjectByName('bow')).toBeDefined()
    const enemyMaterial = (melee.getObjectByName('torso') as Mesh).material as MeshStandardMaterial
    const allyMaterial = (ranged.getObjectByName('torso') as Mesh).material as MeshStandardMaterial
    expect(enemyMaterial.color.getHex()).not.toBe(allyMaterial.color.getHex())
    resources.dispose()
  })
  it('keeps every minion body vertex inside its 26-unit foot click radius', () => {
    const resources = createLaneResources()
    for (const kind of ['melee', 'ranged'] as const) {
      const model = createLaneUnitModel({ ...unit, kind }, 'melee', resources)
      updateLaneUnitModel(model, { ...unit, kind }, 0)
      model.updateMatrixWorld(true)
      const body = model.getObjectByName('body')!
      const box = new Box3().setFromObject(body)
      expect(box.max.y).toBeLessThanOrEqual(30)
      body.traverse(object => {
        if (!(object instanceof Mesh)) return
        const position = object.geometry.getAttribute('position')
        for (let index = 0; index < position.count; index++) {
          const vertex = new Vector3().fromBufferAttribute(position, index).applyMatrix4(object.matrixWorld)
          const clicked = laneScreenToWorld(laneWorldToScreen({ x: vertex.x, y: vertex.z }, vertex.y))!
          expect(Math.hypot(clicked.x - 500, clicked.y - 281.25)).toBeLessThanOrEqual(26)
        }
      })
    }
    resources.dispose()
  })
  it('keeps moving, winding-up and releasing weapons clickable at representative facings', () => {
    const resources = createLaneResources()
    for (const team of ['ally', 'enemy'] as const) {
      for (const kind of ['melee', 'ranged'] as const) {
        const model = createLaneUnitModel({ ...unit, team, kind }, 'melee', resources)
        for (const state of ['moving', 'windup', 'attacking'] as const) {
          for (const attack_progress of [0, .25, .5, .75, 1]) {
            for (const facing_rad of [0, Math.PI / 4, Math.PI / 2, 3 * Math.PI / 4, Math.PI, -3 * Math.PI / 4, -Math.PI / 2, -Math.PI / 4]) {
              updateLaneUnitModel(model, { ...unit, team, kind, state, attack_progress, facing_rad }, 120)
              model.updateMatrixWorld(true)
              const body = model.getObjectByName('body')!
              expect(new Box3().setFromObject(body).max.y).toBeLessThanOrEqual(30)
              let maximumClickDistance = 0
              let outermostPart = ''
              body.traverse(object => {
                if (!(object instanceof Mesh)) return
                const positions = object.geometry.getAttribute('position')
                for (let index = 0; index < positions.count; index++) {
                  const vertex = new Vector3().fromBufferAttribute(positions, index).applyMatrix4(object.matrixWorld)
                  const clicked = laneScreenToWorld(laneWorldToScreen({ x: vertex.x, y: vertex.z }, vertex.y))!
                  const distance = Math.hypot(clicked.x - 500, clicked.y - 281.25)
                  if (distance > maximumClickDistance) { maximumClickDistance = distance; outermostPart = object.name }
                }
              })
              expect(maximumClickDistance, `${team} ${kind} ${state} progress=${attack_progress} facing=${facing_rad} part=${outermostPart}`).toBeLessThanOrEqual(26)
            }
          }
        }
      }
    }
    resources.dispose()
  })
  it('animates limbs, windup, hit feedback and death without replacing geometry', () => {
    const resources = createLaneResources()
    const model = createLaneUnitModel(unit, 'melee', resources)
    const leg = model.getObjectByName('left-leg')!
    updateLaneUnitModel(model, { ...unit, state: 'moving' }, 100)
    const walking = leg.rotation.z
    updateLaneUnitModel(model, { ...unit, state: 'moving' }, 250)
    expect(leg.rotation.z).not.toBe(walking)
    updateLaneUnitModel(model, { ...unit, state: 'windup', attack_progress: .8, hit_at_ms: 200 }, 250)
    expect(model.getObjectByName('right-arm')!.rotation.z).not.toBe(0)
    expect(model.getObjectByName('hit-flash')!.visible).toBe(true)
    expect(model.getObjectByName('health-fill')!.scale.x).toBeCloseTo(.25)
    updateLaneUnitModel(model, { ...unit, state: 'dying' }, 300)
    updateLaneUnitModel(model, { ...unit, state: 'dying' }, 550)
    expect(model.getObjectByName('body')!.rotation.z).toBeGreaterThan(.5)
    resources.dispose()
  })
  it('shares resources across units and disposes each exactly once', () => {
    const resources = createLaneResources()
    const first = createLaneUnitModel(unit, 'melee', resources)
    const second = createLaneUnitModel({ ...unit, id: 'enemy-1' }, 'melee', resources)
    const geometry = (first.getObjectByName('torso') as Mesh).geometry
    expect((second.getObjectByName('torso') as Mesh).geometry).toBe(geometry)
    const dispose = vi.spyOn(geometry, 'dispose')
    resources.dispose(); resources.dispose()
    expect(dispose).toHaveBeenCalledTimes(1)
  })
  it('hides a dead hero and restores the same upright healthy model on a new life', () => {
    const resources = createLaneResources()
    const hero: LaneUnitSnapshot = { ...unit, id: 'hero', team: 'ally', kind: 'hero', health: 1000, max_health: 1000 }
    const model = createLaneUnitModel(hero, 'melee', resources)
    updateLaneUnitModel(model, { ...hero, health: 0, state: 'dying' }, 500)
    updateLaneUnitModel(model, { ...hero, health: 0, state: 'dying' }, 1000)
    expect(model.visible).toBe(false)
    updateLaneUnitModel(model, { ...hero, position: { x: 180, y: 420 } }, 5500)
    expect(model.visible).toBe(true)
    expect(model.getObjectByName('body')!.rotation.z).toBe(0)
    expect(model.getObjectByName('body')!.position.y).toBe(0)
    expect(model.getObjectByName('health-bar')!.visible).toBe(true)
    expect(model.getObjectByName('health-fill')!.scale.x).toBe(1)
    expect(model.position.toArray()).toEqual([180, 0, 420])
    resources.dispose()
  })
})
