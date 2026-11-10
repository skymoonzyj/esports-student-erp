import { CanvasTexture, CircleGeometry, Color, DirectionalLight, DoubleSide, GridHelper, Group, HemisphereLight, Mesh, MeshBasicMaterial, MeshPhongMaterial, PerspectiveCamera, PlaneGeometry, RingGeometry, Scene, SphereGeometry, WebGLRenderer, type Material } from 'three'
import type { FpsSceneSnapshot, FpsTargetSnapshot } from '../engineRuntime/types'
import { verticalFovDeg } from './math'

export interface FpsRenderer {
  render(scene: FpsSceneSnapshot): void
  resize(width: number, height: number, dpr: number): void
  dispose(): void
}

function disposeGroup(group: Group | Scene): void {
  group.traverse(object => {
    if (!('geometry' in object)) return
    const mesh = object as Mesh
    mesh.geometry.dispose()
    const materials: Material[] = Array.isArray(mesh.material) ? mesh.material : [mesh.material]
    for (const material of materials) {
      if (material instanceof MeshBasicMaterial) material.map?.dispose()
      material.dispose()
    }
  })
}

function targetMesh(target: FpsTargetSnapshot): Group {
  const group = new Group()
  group.name = target.id
  group.userData.type = target.type
  group.userData.shape = target.shape
  const color = target.type === 'friend' ? 0x40bbff : target.type === 'enemy' ? 0xff675e : 0x7cf5c7
  if (target.shape === 'sphere') {
    group.add(new Mesh(new SphereGeometry(1, 40, 28), new MeshPhongMaterial({ color, shininess: 45, specular: 0xa6e9ff })))
    return group
  }
  group.add(new Mesh(new CircleGeometry(1, 64), new MeshBasicMaterial({ color, side: DoubleSide })))
  const ring = new Mesh(new RingGeometry(.91, 1, 64), new MeshBasicMaterial({ color: 0xffffff, side: DoubleSide }))
  ring.position.z = .005
  group.add(ring)
  if (target.type !== 'neutral') {
    const symbol = new Mesh(target.type === 'enemy' ? new CircleGeometry(.38, 3) : new PlaneGeometry(.56, .56), new MeshBasicMaterial({ color: 0x101827, side: DoubleSide }))
    symbol.position.set(0, .3, .01)
    symbol.name = `${target.id}-symbol`
    symbol.userData.shape = target.type === 'enemy' ? 'triangle' : 'square'
    group.add(symbol)
    const canvas = document.createElement('canvas')
    canvas.width = 256; canvas.height = 80
    const context = canvas.getContext('2d')
    if (!context) { disposeGroup(group); throw new Error('无法创建敌我文字标识') }
    context.fillStyle = '#101827'; context.font = 'bold 48px sans-serif'; context.textAlign = 'center'; context.textBaseline = 'middle'
    context.fillText(target.type === 'enemy' ? '可射击' : '禁止射击', 128, 40)
    const label = new Mesh(new PlaneGeometry(1.5, .46), new MeshBasicMaterial({ map: new CanvasTexture(canvas), transparent: true, side: DoubleSide }))
    label.position.set(0, -.4, .012)
    group.add(label)
  }
  return group
}

/** Loaded only via FpsStage's dynamic import; no timers, input, engine or API ownership. */
export function createFpsRenderer(canvas: HTMLCanvasElement): FpsRenderer {
  const context = canvas.getContext('webgl2')
  if (!context) throw new Error('当前浏览器无法提供 WebGL2，不能开始 FPS 测试')
  const renderer = new WebGLRenderer({ canvas, context, antialias: true })
  const scene = new Scene()
  scene.background = new Color(0x0b1220)
  const camera = new PerspectiveCamera(60, 1, .01, 1000)
  camera.rotation.order = 'YXZ'
  const grid = new GridHelper(100, 40, 0x33465e, 0x1b2a3d)
  scene.add(grid)
  scene.add(new HemisphereLight(0xd8f5ff, 0x19283d, 1.6))
  const light = new DirectionalLight(0xffffff, 2)
  light.position.set(-3, 8, 5)
  scene.add(light)
  const targets = new Map<string, Group>()
  const checkpoints = new Map<string, Mesh>()
  let disposed = false
  return {
    resize(width, height, dpr) {
      if (disposed) return
      if (!Number.isFinite(dpr) || dpr <= 0) throw new Error('无效像素密度')
      camera.fov = verticalFovDeg({ width, height })
      camera.aspect = width / height
      camera.updateProjectionMatrix()
      renderer.setPixelRatio(dpr)
      renderer.setSize(width, height, false)
    },
    render(snapshot) {
      if (disposed) return
      camera.position.set(snapshot.camera.position.x, snapshot.camera.position.y, snapshot.camera.position.z)
      camera.rotation.set(snapshot.camera.pitch_deg * Math.PI / 180, -snapshot.camera.yaw_deg * Math.PI / 180, 0, 'YXZ')
      const visible = snapshot.targets.filter(target => target.state !== 'hidden' && target.state !== 'hit' && target.state !== 'missed')
      for (const [id, group] of targets) {
        const next = visible.find(target => target.id === id)
        if (!next || next.type !== group.userData.type || next.shape !== group.userData.shape) { disposeGroup(group); scene.remove(group); targets.delete(id) }
      }
      for (const target of visible) {
        let group = targets.get(target.id)
        if (!group) { group = targetMesh(target); targets.set(target.id, group); scene.add(group) }
        group.position.set(target.position.x, target.position.y, target.position.z)
        group.lookAt(camera.position)
        const halfAngle = target.angular_diameter_deg * Math.PI / 360
        const radius = group.position.distanceTo(camera.position) * (target.shape === 'sphere' ? Math.sin(halfAngle) : Math.tan(halfAngle))
        group.scale.setScalar(radius)
        if (snapshot.feedback?.kind === 'spheretrack90') {
          ;((group.children[0] as Mesh).material as MeshBasicMaterial).color.set(snapshot.feedback.covered ? 0x7cf5c7 : 0xff675e)
        }
        for (const child of group.children) {
          const material = (child as Mesh).material as MeshBasicMaterial
          material.opacity = target.state === 'highlighted' ? 1 : .35
          material.transparent = target.state !== 'highlighted' || !!material.map
        }
      }
      const checkpointIds = new Set(snapshot.checkpoints.filter(value => value.state !== 'hidden').map(value => value.id))
      for (const [id, mesh] of checkpoints) {
        if (!checkpointIds.has(id)) { mesh.geometry.dispose(); (mesh.material as Material).dispose(); scene.remove(mesh); checkpoints.delete(id) }
      }
      for (const checkpoint of snapshot.checkpoints) {
        if (checkpoint.state === 'hidden') continue
        let mesh = checkpoints.get(checkpoint.id)
        if (!mesh) { mesh = new Mesh(new RingGeometry(.85, 1, 48), new MeshBasicMaterial({ color: 0xfee680, side: DoubleSide })); checkpoints.set(checkpoint.id, mesh); scene.add(mesh) }
        mesh.position.set(checkpoint.position.x, .02, checkpoint.position.z)
        mesh.rotation.x = -Math.PI / 2
        mesh.scale.setScalar(checkpoint.radius)
        ;(mesh.material as MeshBasicMaterial).color.set(checkpoint.state === 'reached' ? 0x72f0b4 : 0xfee680)
      }
      renderer.render(scene, camera)
    },
    dispose() {
      if (disposed) return
      disposed = true
      disposeGroup(scene)
      scene.clear(); targets.clear(); checkpoints.clear()
      renderer.dispose()
      renderer.forceContextLoss()
    },
  }
}
