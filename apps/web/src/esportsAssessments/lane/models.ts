import { BoxGeometry, CircleGeometry, ConeGeometry, CylinderGeometry, DoubleSide, Group, Mesh, MeshBasicMaterial, MeshStandardMaterial, PlaneGeometry, RingGeometry, SphereGeometry, TorusGeometry, type BufferGeometry, type Material } from 'three'
import type { LaneUnitSnapshot } from './types'

/** All geometry and materials are pooled, including scenery and effects. */
export function createLaneResources() {
  const geometries = new Map<string, BufferGeometry>()
  const materials = new Map<string, Material>()
  let disposed = false
  return {
    geometry(key: string, build: () => BufferGeometry) {
      let value = geometries.get(key)
      if (!value) { value = build(); geometries.set(key, value) }
      return value
    },
    material(key: string, build: () => Material) {
      let value = materials.get(key)
      if (!value) { value = build(); materials.set(key, value) }
      return value
    },
    dispose() {
      if (disposed) return
      disposed = true
      geometries.forEach(value => value.dispose()); materials.forEach(value => value.dispose())
      geometries.clear(); materials.clear()
    },
  }
}
export type LaneResources = ReturnType<typeof createLaneResources>

export function laneMaterial(resources: LaneResources, color: number, unlit = false): Material {
  return resources.material(`${color}:${unlit}`, () => unlit ? new MeshBasicMaterial({ color, side: DoubleSide }) : new MeshStandardMaterial({ color, flatShading: true, roughness: .88, metalness: .12 }))
}

export function lanePrimitive(resources: LaneResources, shape: 'box' | 'sphere' | 'cone' | 'cylinder' | 'circle' | 'ring' | 'plane' | 'bow', material: Material): Mesh {
  const geometry = resources.geometry(shape, () => {
    switch (shape) {
      case 'box': return new BoxGeometry(1, 1, 1)
      case 'sphere': return new SphereGeometry(1, 8, 6)
      case 'cone': return new ConeGeometry(1, 1, 5)
      case 'cylinder': return new CylinderGeometry(1, 1, 1, 8)
      case 'circle': return new CircleGeometry(1, 20)
      case 'ring': return new RingGeometry(.83, 1, 32)
      case 'plane': return new PlaneGeometry(1, 1)
      case 'bow': return new TorusGeometry(1, .13, 4, 12, Math.PI * 1.4)
    }
  })
  return new Mesh(geometry, material)
}

export function createLaneUnitModel(unit: LaneUnitSnapshot, mode: 'melee' | 'ranged', resources: LaneResources): Group {
  const root = new Group()
  root.name = unit.id
  root.userData.kind = unit.kind
  const body = new Group(); body.name = 'body'; root.add(body)
  body.scale.setScalar(unit.kind === 'hero' ? 1.4 : 1)
  const armor = laneMaterial(resources, unit.kind === 'hero' ? 0xe4bb65 : unit.team === 'ally' ? 0x48b7cf : 0xd96c63)
  const dark = laneMaterial(resources, unit.team === 'ally' ? 0x183e54 : 0x582c42)
  const skin = laneMaterial(resources, 0xf4cc9d)
  const metal = laneMaterial(resources, 0xd6e2e5)
  const leather = laneMaterial(resources, 0x684b36)
  function part(parent: Group, name: string, shape: Parameters<typeof lanePrimitive>[1], material: Material, position: number[], scale: number[]) {
    const mesh = lanePrimitive(resources, shape, material)
    mesh.name = name; mesh.position.set(position[0], position[1], position[2]); mesh.scale.set(scale[0], scale[1], scale[2]); parent.add(mesh)
    return mesh
  }
  part(body, 'torso', 'box', armor, [0, 15, 0], [8, 10, 10])
  part(body, 'belt', 'box', leather, [0, 10.5, 0], [9, 2, 10.5])
  part(body, 'head', 'sphere', skin, [0, 23, 0], [4.5, 4.5, 4.5])
  if (unit.team === 'enemy') {
    part(body, 'helmet', 'cone', dark, [0, 26, 0], [5.8, 6, 5.8])
  } else {
    part(body, 'helmet', 'box', dark, [-1, 26, 0], [6, 4, 9.5])
    part(body, 'crest', 'box', armor, [-1, 28.5, 0], [7, 2, 2])
  }
  for (const [side, z] of [['left', 3], ['right', -3]] as const) {
    const limb = new Group(); limb.name = `${side}-leg`; limb.position.set(0, 10, z); body.add(limb)
    part(limb, `${side}-greave`, 'box', dark, [0, -4, 0], [3.6, 8, 3.6])
    part(limb, `${side}-boot`, 'box', leather, [1.3, -8.5, 0], [6, 3, 4.5])
  }
  for (const [side, z] of [['left', 7], ['right', -7]] as const) {
    const arm = new Group(); arm.name = `${side}-arm`; arm.position.set(0, 19, z); body.add(arm)
    part(arm, `${side}-pauldron`, 'sphere', armor, [0, 0, 0], [4, 3.5, 3.5])
    part(arm, `${side}-forearm`, 'box', skin, [0, -4.5, 0], [3, 7, 3])
  }
  const right = body.getObjectByName('right-arm') as Group
  const left = body.getObjectByName('left-arm') as Group
  const hero = unit.kind === 'hero'
  const ranged = unit.kind === 'ranged' || unit.kind === 'hero' && mode === 'ranged'
  if (ranged) {
    const bowSize = hero ? 7 : 5.5
    const bowX = hero ? 4 : 2
    const bow = part(right, 'bow', 'bow', leather, [bowX, -4, -1], [bowSize, bowSize, bowSize]); bow.rotation.z = -Math.PI * .2
    part(right, 'bowstring', 'box', metal, [bowX, -4, -1], [.6, hero ? 12 : 10, .6])
    part(body, 'quiver', 'cylinder', leather, [-5.5, 16, 2], [2.4, 10, 2.4])
  } else {
    // Keep the released swing inside the 26-unit foot-selection circle after
    // camera backprojection, including when the raised blade faces the camera.
    const blade = part(right, 'sword', 'box', metal, [hero ? 7 : 3, -6, 0], [hero ? 14 : 8, 2, 1.5]); blade.rotation.z = -.28
    part(right, 'guard', 'box', armor, [1, -4, 0], [2, 5, 4])
    const shield = part(left, 'shield', unit.team === 'ally' ? 'cylinder' : 'box', dark, [1, -3.5, 2], [6, 2.4, 7]); shield.rotation.x = Math.PI / 2
    part(left, 'shield-boss', 'sphere', armor, [1, -3.5, 4], [2, 2, 1])
  }
  const shadow = part(root, 'shadow', 'circle', laneMaterial(resources, 0x18302e, true), [0, .25, 0], [12, 8, 1]); shadow.rotation.x = -Math.PI / 2
  const selection = part(root, 'selection', 'ring', laneMaterial(resources, 0xffe18c, true), [0, .5, 0], [18, 18, 1]); selection.rotation.x = -Math.PI / 2; selection.visible = false
  const health = new Group(); health.name = 'health-bar'; health.position.y = unit.kind === 'hero' ? 48 : 35; root.add(health)
  part(health, 'health-background', 'plane', laneMaterial(resources, 0x142429, true), [0, 0, 0], [26, 4, 1])
  const fill = new Group(); fill.name = 'health-fill'; fill.position.set(-12, 0, .02); health.add(fill)
  part(fill, 'health-color', 'plane', laneMaterial(resources, unit.team === 'ally' ? 0x62e4ad : 0xff8074, true), [12, 0, 0], [24, 2.4, 1])
  const flash = part(root, 'hit-flash', 'ring', laneMaterial(resources, 0xffe2a1, true), [0, 13, 0], [13, 13, 1]); flash.visible = false
  updateLaneUnitModel(root, unit, 0)
  return root
}

export function updateLaneUnitModel(model: Group, unit: LaneUnitSnapshot, atMs: number): void {
  model.position.set(unit.position.x, 0, unit.position.y)
  model.visible = !(unit.kind === 'hero' && unit.health <= 0)
  const body = model.getObjectByName('body')!
  body.rotation.set(0, -unit.facing_rad, 0)
  const gait = unit.state === 'moving' ? Math.sin(atMs / 90 + unit.position.x / 30) * .55 : 0
  model.getObjectByName('left-leg')!.rotation.z = gait
  model.getObjectByName('right-leg')!.rotation.z = -gait
  model.getObjectByName('left-arm')!.rotation.z = -gait * .6
  model.getObjectByName('right-arm')!.rotation.z = unit.state === 'windup' ? -.9 * unit.attack_progress : unit.state === 'attacking' ? .95 * (1 - unit.attack_progress) : gait * .6
  model.getObjectByName('health-fill')!.scale.x = Math.max(0, Math.min(1, unit.max_health > 0 ? unit.health / unit.max_health : 0))
  const hitAge = unit.hit_at_ms === null ? Infinity : atMs - unit.hit_at_ms
  const flash = model.getObjectByName('hit-flash')!
  flash.visible = hitAge >= 0 && hitAge < 160
  flash.scale.setScalar(8 + Math.max(0, Math.min(160, hitAge)) * .04)
  if (unit.state === 'dying') {
    model.userData.deathStartedAt ??= atMs
    const progress = Math.max(0, Math.min(1, (atMs - model.userData.deathStartedAt) / 350))
    body.rotation.z = progress * Math.PI * .48
    body.position.y = -progress * 3
    model.getObjectByName('health-bar')!.visible = false
    model.getObjectByName('shadow')!.scale.set(12 * (1 - progress * .6), 8 * (1 - progress * .6), 1)
  } else {
    delete model.userData.deathStartedAt
    body.rotation.z = 0; body.position.y = 0
    model.getObjectByName('health-bar')!.visible = true
    model.getObjectByName('shadow')!.scale.set(12, 8, 1)
  }
}
