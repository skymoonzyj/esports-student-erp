import { Color, DirectionalLight, HemisphereLight, Raycaster, Scene, Vector2 as ThreeVector2, WebGLRenderer, type Group, type Mesh } from 'three'
import type { MobaSceneSnapshot, Vector2 } from '../engineRuntime/types'
import { createLaneResources, createLaneUnitModel, laneMaterial, lanePrimitive, updateLaneUnitModel } from './models'
import { createLaneCamera } from './projection'
import { createLaneTowerModel, updateLaneTowerModel } from './towerModels'

export interface LaneRenderer {
  resize(width: number, height: number, dpr: number): void
  render(snapshot: MobaSceneSnapshot | null): void
  pick(point: Vector2): string | null | undefined
  dispose(): void
}

/** Pure Three scene construction: no canvas, input, timers or GPU dependency. */
export function createLaneScene() {
  const scene = new Scene(); scene.background = new Color(0x182c32)
  const camera = createLaneCamera()
  const resources = createLaneResources()
  const units = new Map<string, Group>()
  const towers = new Map<string, Group>()
  const projectiles = new Map<string, Mesh>()
  const raycaster = new Raycaster()
  const targetable = new Set<string>()
  let disposed = false
  scene.add(new HemisphereLight(0xe2f5eb, 0x304549, 2.4))
  const sun = new DirectionalLight(0xffe7b1, 3.1); sun.position.set(200, 600, 350); scene.add(sun)
  function prop(name: string, shape: Parameters<typeof lanePrimitive>[1], color: number, position: number[], scale: number[], flat = false) {
    const mesh = lanePrimitive(resources, shape, laneMaterial(resources, color, flat))
    mesh.name = name; mesh.position.set(position[0], position[1], position[2]); mesh.scale.set(scale[0], scale[1], scale[2]); scene.add(mesh)
    return mesh
  }
  prop('meadow', 'box', 0x35594d, [500, -5, 281.25], [1040, 8, 720])
  prop('road-border', 'box', 0x64766a, [500, -.5, 281.25], [1000, 1, 148])
  prop('lane-road', 'box', 0x8b8b76, [500, .1, 281.25], [1000, 1, 130])
  for (let index = 0; index < 20; index++) {
    for (const z of [254, 305]) {
      const slab = prop('road-stone', 'box', (index + z) % 3 ? 0x989883 : 0x7f8578, [index * 52 + 24, .9, z], [47, 1.4, 44])
      slab.rotation.y = Math.sin(index * 7) * .035
    }
  }
  // Decorative landmarks stay away from both the lane and hero's starting area.
  for (let index = 0; index < 12; index++) {
    const x = 35 + index * 84
    for (const z of [92 + (index % 3) * 13, 536 + (index % 2) * 10]) {
      prop('tree-trunk', 'cylinder', 0x584b3d, [x, 10, z], [5, 22, 5])
      prop('tree-crown', 'cone', index % 2 ? 0x2e6156 : 0x427161, [x, 34, z], [24, 40, 24])
      prop('tree-crown-top', 'cone', 0x527e65, [x, 51, z], [17, 30, 17])
    }
  }
  for (const [x, color] of [[48, 0x53bed0], [952, 0xe57a68]]) {
    prop('faction-plinth', 'cylinder', 0x758578, [x, 3, 185], [20, 6, 20])
    prop('banner-pole', 'cylinder', 0xc4c8ab, [x, 28, 185], [1.8, 48, 1.8])
    prop('banner', 'box', color, [x + 10, 42, 185], [20, 17, 1.8])
  }
  const moveMarker = prop('move-destination', 'ring', 0xa2f8d0, [0, 1.5, 0], [13, 13, 1], true)
  moveMarker.rotation.x = -Math.PI / 2; moveMarker.visible = false

  return {
    scene, camera, units, towers, projectiles,
    pick(point: Vector2): string | null | undefined {
      if (disposed || !Number.isFinite(point.x) || !Number.isFinite(point.y) || point.x < 0 || point.x > 1000 || point.y < 0 || point.y > 562.5) return undefined
      scene.updateMatrixWorld(true)
      raycaster.setFromCamera(new ThreeVector2(point.x / 500 - 1, 1 - point.y / 281.25), camera)
      // Only solid unit/tower bodies participate. HUD bars, shadows, rings, particles
      // and scenery are not target silhouettes. Frontmost body wins, including
      // allies/hero/corpses that block enemies behind it. Exact depth ties use ID.
      const hits = [...units, ...towers].flatMap(([id, model]) => {
        const body = model.getObjectByName(model.userData.kind === 'tower' ? 'tower-body' : 'body')!
        if (!model.visible || !body.visible) return []
        const hit = raycaster.intersectObject(body, true).find(hit => {
          for (let object: typeof hit.object | null = hit.object; object; object = object.parent) if (!object.visible) return false
          return true
        })
        return hit ? [{ id, distance: hit.distance }] : []
      }).sort((a, b) => a.distance - b.distance || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
      const first = hits[0]
      return first ? targetable.has(first.id) ? first.id : null : undefined
    },
    update(snapshot: MobaSceneSnapshot | null) {
      if (disposed) return
      const lane = snapshot?.lane
      if (lane) {
        const numbers = [snapshot.at_ms, ...lane.units.flatMap(unit => [unit.position.x, unit.position.y, unit.facing_rad, unit.health, unit.max_health, unit.attack_progress, ...(unit.hit_at_ms === null ? [] : [unit.hit_at_ms])]), ...lane.projectiles.flatMap(projectile => [projectile.position.x, projectile.position.y, projectile.target.x, projectile.target.y]), ...(lane.towers ?? []).flatMap(tower => [tower.position.x, tower.position.y, tower.facing_rad, tower.attack_progress]), ...(lane.hero_status ? [lane.hero_status.health, lane.hero_status.max_health, lane.hero_status.deaths, ...(lane.hero_status.respawn_at_ms === null ? [] : [lane.hero_status.respawn_at_ms])] : []), ...(lane.move_destination ? [lane.move_destination.x, lane.move_destination.y] : [])]
        if (!numbers.every(Number.isFinite)) throw new Error('无效的兵线场景数值')
      }
      const nextIds = new Set(lane?.units.map(unit => unit.id))
      targetable.clear()
      for (const [id, model] of units) {
        if (!nextIds.has(id)) { scene.remove(model); units.delete(id) }
      }
      for (const source of lane?.units ?? []) {
        const unit = source.kind === 'hero' && lane?.hero_status ? { ...source, health: lane.hero_status.health, max_health: lane.hero_status.max_health, ...(lane.hero_status.health <= 0 ? { state: 'dying' as const } : {}) } : source
        if (unit.team === 'enemy' && unit.health > 0 && unit.state !== 'dying') targetable.add(unit.id)
        let model = units.get(unit.id)
        if (!model) { model = createLaneUnitModel(unit, lane!.attack_mode, resources); units.set(unit.id, model); scene.add(model) }
        updateLaneUnitModel(model, unit, snapshot!.at_ms)
        model.getObjectByName('health-bar')!.quaternion.copy(camera.quaternion)
        model.getObjectByName('hit-flash')!.quaternion.copy(camera.quaternion)
        model.getObjectByName('selection')!.visible = unit.id === lane!.selected_target_id && unit.state !== 'dying'
      }
      const nextTowers = new Set(lane?.towers?.map(tower => tower.id))
      for (const [id, model] of towers) {
        if (!nextTowers.has(id)) { scene.remove(model); towers.delete(id) }
      }
      for (const tower of lane?.towers ?? []) {
        let model = towers.get(tower.id)
        if (!model) { model = createLaneTowerModel(tower, resources); towers.set(tower.id, model); scene.add(model) }
        updateLaneTowerModel(model, tower)
      }
      const nextProjectiles = new Set(lane?.projectiles.map(value => value.id))
      for (const [id, projectile] of projectiles) {
        if (!nextProjectiles.has(id)) { scene.remove(projectile); projectiles.delete(id) }
      }
      for (const projectile of lane?.projectiles ?? []) {
        let mesh = projectiles.get(projectile.id)
        const tower = projectile.kind === 'tower'
        if (mesh && mesh.userData.kind !== projectile.kind) { scene.remove(mesh); projectiles.delete(projectile.id); mesh = undefined }
        if (!mesh) {
          mesh = lanePrimitive(resources, tower ? 'sphere' : 'cone', laneMaterial(resources, tower ? projectile.team === 'ally' ? 0xa9f3ff : 0xffbf6b : projectile.team === 'ally' ? 0xfde8a3 : 0xff9880, true))
          mesh.userData.kind = projectile.kind
          mesh.scale.set(tower ? 6 : 2.8, tower ? 18 : 15, tower ? 6 : 2.8); projectiles.set(projectile.id, mesh); scene.add(mesh)
        }
        mesh.position.set(projectile.position.x, tower ? 32 : 15, projectile.position.y)
        mesh.rotation.set(0, -Math.atan2(projectile.target.y - projectile.position.y, projectile.target.x - projectile.position.x), -Math.PI / 2)
      }
      moveMarker.visible = !!lane?.move_destination
      if (lane?.move_destination) {
        moveMarker.position.set(lane.move_destination.x, 1.5, lane.move_destination.y)
        const pulse = 12 + Math.sin(snapshot!.at_ms / 140) * 2
        moveMarker.scale.set(pulse, pulse, 1)
      }
    },
    dispose() {
      if (disposed) return
      disposed = true
      resources.dispose(); scene.clear(); units.clear(); towers.clear(); projectiles.clear(); targetable.clear()
    },
  }
}

export function createLaneRenderer(canvas: HTMLCanvasElement): LaneRenderer {
  const renderer = new WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: 'high-performance' })
  const view = createLaneScene()
  let disposed = false
  let drawingBuffer: { width: number; height: number; dpr: number } | null = null
  return {
    resize(width, height, dpr) {
      if (disposed) return
      if (![width, height, dpr].every(value => Number.isFinite(value) && value > 0)) throw new Error('无效的兵线画布尺寸或像素密度')
      if (drawingBuffer?.width === width && drawingBuffer.height === height && drawingBuffer.dpr === dpr) return
      if (drawingBuffer?.dpr !== dpr) renderer.setPixelRatio(dpr)
      renderer.setSize(width, height, false)
      drawingBuffer = { width, height, dpr }
    },
    render(snapshot) {
      if (disposed) return
      view.update(snapshot); renderer.render(view.scene, view.camera)
    },
    pick(point) { return view.pick(point) },
    dispose() {
      if (disposed) return
      disposed = true
      view.dispose(); renderer.dispose()
    },
  }
}
