import { Group } from 'three'
import { laneMaterial, lanePrimitive, type LaneResources } from './models'
import type { LaneTowerSnapshot } from './types'

/** Original low-poly watchtowers; only the solid body participates in picking. */
export function createLaneTowerModel(tower: LaneTowerSnapshot, resources: LaneResources): Group {
  const root = new Group(); root.name = tower.id; root.userData.kind = 'tower'
  const body = new Group(); body.name = 'tower-body'; root.add(body)
  const stone = laneMaterial(resources, tower.team === 'ally' ? 0x698b91 : 0x8d727a)
  const dark = laneMaterial(resources, tower.team === 'ally' ? 0x244758 : 0x592e45)
  const faction = laneMaterial(resources, tower.team === 'ally' ? 0x52d7e4 : 0xf2856d)
  const metal = laneMaterial(resources, 0xd2c39a)
  function part(parent: Group, name: string, shape: Parameters<typeof lanePrimitive>[1], material: ReturnType<typeof laneMaterial>, position: number[], scale: number[]) {
    const mesh = lanePrimitive(resources, shape, material)
    mesh.name = name; mesh.position.set(position[0], position[1], position[2]); mesh.scale.set(scale[0], scale[1], scale[2]); parent.add(mesh)
    return mesh
  }
  // Broad, flat foundation is decorative: clicking its edge remains ground input.
  part(root, 'tower-foundation', 'cylinder', dark, [0, 2, 0], [29, 4, 29])
  const ring = part(root, 'tower-faction-ring', 'ring', laneMaterial(resources, tower.team === 'ally' ? 0x53bdce : 0xd76d62, true), [0, 4.1, 0], [27, 27, 1]); ring.rotation.x = -Math.PI / 2
  part(body, 'tower-pedestal', 'cylinder', stone, [0, 9, 0], [21, 14, 21])
  part(body, 'tower-column', 'box', stone, [0, 31, 0], [23, 38, 23])
  part(body, 'tower-banner', 'box', faction, [0, 33, 12], [11, 24, 1.5])
  part(body, 'tower-capital', 'box', dark, [0, 50, 0], [33, 7, 33])
  const crown = new Group(); crown.name = 'rear-crown'; crown.visible = tower.id.endsWith('rear'); body.add(crown)
  for (const x of [-13, 13]) part(crown, 'tower-crown-prong', 'cone', metal, [x, 61, -12], [5, 17, 5])
  const weapon = new Group(); weapon.name = 'tower-weapon'; weapon.position.y = 59; body.add(weapon)
  part(weapon, 'tower-mount', 'sphere', dark, [0, 0, 0], [13, 11, 13])
  part(weapon, 'tower-barrel', 'box', metal, [15, 1, 0], [30, 10, 10])
  part(weapon, 'tower-muzzle', 'box', dark, [31, 1, 0], [4, 13, 13])
  // Feedback shares weapon facing but deliberately lives outside tower-body.
  const feedback = new Group(); feedback.name = 'tower-feedback'; feedback.position.y = 60; root.add(feedback)
  part(feedback, 'tower-charge', 'sphere', laneMaterial(resources, tower.team === 'ally' ? 0xb5faff : 0xffc790, true), [35, 0, 0], [1, 1, 1])
  part(feedback, 'tower-muzzle-flash', 'sphere', laneMaterial(resources, 0xffefa6, true), [37, 0, 0], [8, 8, 8])
  updateLaneTowerModel(root, tower)
  return root
}

export function updateLaneTowerModel(model: Group, tower: LaneTowerSnapshot): void {
  model.position.set(tower.position.x, 0, tower.position.y)
  const weapon = model.getObjectByName('tower-weapon')!
  const feedback = model.getObjectByName('tower-feedback')!
  weapon.rotation.y = feedback.rotation.y = -tower.facing_rad
  const progress = Math.max(0, Math.min(1, tower.attack_progress))
  weapon.position.x = tower.state === 'attacking' ? -Math.cos(tower.facing_rad) * 3 * (1 - progress) : 0
  weapon.position.z = tower.state === 'attacking' ? -Math.sin(tower.facing_rad) * 3 * (1 - progress) : 0
  const charge = model.getObjectByName('tower-charge')!
  charge.visible = tower.state === 'windup'; charge.scale.setScalar(2 + progress * 5)
  const flash = model.getObjectByName('tower-muzzle-flash')!
  flash.visible = tower.state === 'attacking' && progress < .65
  flash.scale.setScalar(8 * (1 - progress * .7))
}
