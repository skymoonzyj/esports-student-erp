import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BufferGeometry, Material, Texture, type PerspectiveCamera, type Scene } from 'three'
import type * as Three from 'three'
import type { FpsSceneSnapshot } from '../engineRuntime/types'
import { createFpsRenderer } from './renderer'

const backend = vi.hoisted(() => ({ render: vi.fn(), resize: vi.fn(), dpr: vi.fn(), dispose: vi.fn(), loss: vi.fn() }))
vi.mock('three', async original => {
  const real = await original<typeof Three>()
  return { ...real, WebGLRenderer: class {
    render = backend.render; setSize = backend.resize; setPixelRatio = backend.dpr
    dispose = backend.dispose; forceContextLoss = backend.loss
  } }
})

const snapshot: FpsSceneSnapshot = {
  kind: 'fps', at_ms: 0,
  camera: { yaw_deg: 0, pitch_deg: 0, position: { x: 0, y: 1.6, z: 0 } },
  targets: [{ id: 'enemy', position: { x: 0, y: 1.6, z: -10 }, angular_diameter_deg: 4, type: 'enemy', state: 'highlighted' }], checkpoints: [],
}

describe('FPS renderer resource boundary (not a WebGL acceptance test)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(((kind: string) => kind === 'webgl2' ? {} : { clearRect() {}, fillText() {}, fillRect() {} }) as typeof HTMLCanvasElement.prototype.getContext)
  })

  it('derives vertical FOV from actual aspect and disposes owned scene/GPU resources exactly once', () => {
    const geometry = vi.spyOn(BufferGeometry.prototype, 'dispose')
    const material = vi.spyOn(Material.prototype, 'dispose')
    const texture = vi.spyOn(Texture.prototype, 'dispose')
    const renderer = createFpsRenderer(document.createElement('canvas'))
    renderer.resize(800, 450, 2)
    renderer.render(snapshot)
    const [scene, camera] = backend.render.mock.calls.at(-1) as [Scene, PerspectiveCamera]
    expect(camera.fov).toBeCloseTo(58.7155, 3)
    expect(camera.aspect).toBe(800 / 450)
    expect(scene.getObjectByName('enemy')).toBeDefined()
    expect(backend.resize).toHaveBeenLastCalledWith(800, 450, false)
    renderer.dispose()
    const counts = [geometry.mock.calls.length, material.mock.calls.length, texture.mock.calls.length]
    expect(counts.every(count => count > 0)).toBe(true)
    renderer.dispose()
    expect([geometry.mock.calls.length, material.mock.calls.length, texture.mock.calls.length]).toEqual(counts)
    expect(backend.dispose).toHaveBeenCalledTimes(1)
    expect(backend.loss).toHaveBeenCalledTimes(1)
  })

  it('draws enemy/friend shape and text cues locally and removes obsolete targets', () => {
    const labels: string[] = []
    vi.mocked(HTMLCanvasElement.prototype.getContext).mockImplementation(((kind: string) => kind === 'webgl2' ? {} : { clearRect() {}, fillText(text: string) { labels.push(text) }, fillRect() {} }) as typeof HTMLCanvasElement.prototype.getContext)
    const renderer = createFpsRenderer(document.createElement('canvas'))
    renderer.render({ ...snapshot, targets: [...snapshot.targets, { ...snapshot.targets[0], id: 'friend', type: 'friend' }] })
    const scene = backend.render.mock.calls.at(-1)?.[0] as Scene
    expect(labels).toEqual(expect.arrayContaining(['可射击', '禁止射击']))
    expect(scene.getObjectByName('enemy-symbol')?.userData.shape).toBe('triangle')
    expect(scene.getObjectByName('friend-symbol')?.userData.shape).toBe('square')
    renderer.render({ ...snapshot, targets: [] })
    expect(scene.getObjectByName('enemy')).toBeUndefined()
    expect(scene.getObjectByName('friend')).toBeUndefined()
    renderer.dispose()
  })

  it('rejects unavailable WebGL2 without falling back to scored canvas shooting', () => {
    vi.mocked(HTMLCanvasElement.prototype.getContext).mockReturnValue(null)
    expect(() => createFpsRenderer(document.createElement('canvas'))).toThrow(/WebGL2/)
    expect(backend.render).not.toHaveBeenCalled()
  })

  it('colors the tracking target red or green from measured on-target state', () => {
    const renderer = createFpsRenderer(document.createElement('canvas'))
    const tracking = { ...snapshot, targets: [{ ...snapshot.targets[0], type: 'neutral' as const }], feedback: { kind: 'spheretrack90' as const, covered: false } }
    renderer.render(tracking)
    const scene = backend.render.mock.calls.at(-1)?.[0] as Scene
    const target = scene.getObjectByName('enemy')!
    const fill = target.children[0] as Three.Mesh<Three.CircleGeometry, Three.MeshBasicMaterial>
    expect(fill.material.color.getHex()).toBe(0xff675e)
    renderer.render({ ...tracking, feedback: { kind: 'spheretrack90', covered: true } })
    expect(fill.material.color.getHex()).toBe(0x7cf5c7)
    renderer.dispose()
  })

  it('renders v4 angular targets as lit spheres with tangent-correct radius, leaving old disks frozen', () => {
    const renderer = createFpsRenderer(document.createElement('canvas'))
    renderer.render({ ...snapshot, targets: [{ ...snapshot.targets[0], type: 'neutral', shape: 'sphere' }] })
    const scene = backend.render.mock.calls.at(-1)?.[0] as Scene
    const sphere = scene.getObjectByName('enemy') as Three.Group
    expect((sphere.children[0] as Three.Mesh).geometry.type).toBe('SphereGeometry')
    expect((sphere.children[0] as Three.Mesh<Three.SphereGeometry, Three.MeshPhongMaterial>).material.type).toBe('MeshPhongMaterial')
    expect(sphere.scale.x).toBeCloseTo(10 * Math.sin(2 * Math.PI / 180), 10)
    renderer.render({ ...snapshot, targets: [{ ...snapshot.targets[0], id: 'old' }] })
    expect((scene.getObjectByName('old')!.children[0] as Three.Mesh).geometry.type).toBe('CircleGeometry')
    renderer.dispose()
  })
})
