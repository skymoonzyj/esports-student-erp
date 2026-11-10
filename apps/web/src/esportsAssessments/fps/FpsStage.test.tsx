import { createRef } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { FpsSceneSnapshot } from '../engineRuntime/types'

const backend = vi.hoisted(() => ({ create: vi.fn(), render: vi.fn(), resize: vi.fn(), dispose: vi.fn(), gate: undefined as undefined | Promise<void> }))
const scene: FpsSceneSnapshot = { kind: 'fps', at_ms: 0, camera: { yaw_deg: 0, pitch_deg: 0, position: { x: 0, y: 1.6, z: 0 } }, targets: [], checkpoints: [] }
const viewport = { width: 800, height: 450 }

describe('FpsStage lazy lifecycle', () => {
  beforeEach(() => {
    vi.resetModules(); vi.clearAllMocks(); backend.gate = undefined
    backend.create.mockReturnValue({ render: backend.render, resize: backend.resize, dispose: backend.dispose })
    vi.doMock('./renderer', async () => {
      if (backend.gate) await backend.gate
      return { createFpsRenderer: backend.create }
    })
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as WebGL2RenderingContext)
  })

  it('keeps the same exposed canvas from preflight to running and forwards input', async () => {
    const { FpsStage } = await import('./FpsStage')
    const ref = createRef<HTMLCanvasElement>(), input = vi.fn(), ready = vi.fn()
    const props = { viewport, dpr: 1, canvasRef: ref, onReady: ready, onError: vi.fn(), inputHandlers: { onMouseDown: input } }
    const view = render(<FpsStage {...props} scene={null} />)
    const canvas = ref.current
    await waitFor(() => expect(ready).toHaveBeenCalledTimes(1))
    view.rerender(<FpsStage {...props} scene={scene} />)
    expect(ref.current).toBe(canvas)
    fireEvent.mouseDown(canvas!, { button: 0 })
    expect(input).toHaveBeenCalledTimes(1)
    expect(backend.render).toHaveBeenCalledWith(scene)
    view.unmount()
    expect(backend.dispose).toHaveBeenCalledTimes(1)
    expect(ref.current).toBeNull()
  })

  it('does not create resources after leaving while dynamic import is pending', async () => {
    let resolve!: () => void
    backend.gate = new Promise<void>(done => { resolve = done })
    const { FpsStage } = await import('./FpsStage')
    const ready = vi.fn()
    const view = render(<FpsStage scene={scene} viewport={viewport} dpr={1} canvasRef={createRef()} onReady={ready} onError={vi.fn()} />)
    await act(async () => { await Promise.resolve() })
    expect(backend.create).not.toHaveBeenCalled()
    view.unmount()
    await act(async () => { resolve(); await backend.gate })
    expect(backend.create).not.toHaveBeenCalled()
    expect(ready).not.toHaveBeenCalled()
  })

  it('reports missing WebGL2 separately from context loss and keeps the canvas', async () => {
    const { FpsStage } = await import('./FpsStage')
    vi.mocked(HTMLCanvasElement.prototype.getContext).mockReturnValue(null)
    const onError = vi.fn(), ref = createRef<HTMLCanvasElement>()
    const view = render(<FpsStage scene={null} viewport={viewport} dpr={1} canvasRef={ref} onError={onError} />)
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ code: 'webgl2-unavailable' }))
    expect(screen.getByRole('alert')).toHaveTextContent('WebGL2')
    expect(ref.current).not.toBeNull()
    view.unmount()
  })

  it('stops rendering on context loss and reports an interruption without remounting canvas', async () => {
    const { FpsStage } = await import('./FpsStage')
    const onError = vi.fn(), ref = createRef<HTMLCanvasElement>()
    const view = render(<FpsStage scene={scene} viewport={viewport} dpr={1} canvasRef={ref} onError={onError} />)
    await waitFor(() => expect(backend.create).toHaveBeenCalledTimes(1))
    const canvas = ref.current
    fireEvent(canvas!, new Event('webglcontextlost', { cancelable: true }))
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ code: 'context-lost' }))
    const count = backend.render.mock.calls.length
    view.rerender(<FpsStage scene={{ ...scene, at_ms: 100 }} viewport={viewport} dpr={1} canvasRef={ref} onError={onError} />)
    expect(backend.render).toHaveBeenCalledTimes(count)
    expect(ref.current).toBe(canvas)
    view.unmount()
    expect(backend.dispose).toHaveBeenCalledTimes(1)
  })

  it('shows scene-only checkpoint direction and arrival without intercepting controls', async () => {
    const { FpsStage } = await import('./FpsStage')
    const props = { viewport, dpr: 1, canvasRef: createRef<HTMLCanvasElement>(), onError: vi.fn() }
    const checkpoint = { id: 'destination', position: { x: -1.2, y: 0, z: 0 }, radius: .35, state: 'active' as const }
    const view = render(<FpsStage {...props} scene={{ ...scene, checkpoints: [checkpoint] }} />)
    const cue = screen.getByLabelText('移动检查点提示')
    expect(cue).toHaveTextContent('左侧')
    expect(cue).toHaveTextContent('1.20 m')
    expect(cue).toHaveStyle({ pointerEvents: 'none' })
    view.rerender(<FpsStage {...props} scene={{ ...scene, checkpoints: [{ ...checkpoint, position: { x: 1.2, y: 0, z: 0 } }] }} />)
    expect(cue).toHaveTextContent('右侧')
    view.rerender(<FpsStage {...props} scene={{ ...scene, checkpoints: [{ ...checkpoint, state: 'reached' }] }} />)
    expect(cue).toHaveTextContent('已到位')
    view.unmount()
  })

  it('shows observed click counts, accuracy and run time without a client final score', async () => {
    const { FpsStage } = await import('./FpsStage')
    const props = { viewport, dpr: 1, canvasRef: createRef<HTMLCanvasElement>(), onError: vi.fn() }
    const view = render(<FpsStage {...props} scene={{ ...scene, at_ms: 12500, feedback: { kind: 'multi_target', shots: 4, hits: 3 } }} />)
    const hud = screen.getByLabelText('FPS 实时反馈')
    expect(hud).toHaveTextContent('12.5 / 60.0 秒')
    expect(hud).toHaveTextContent('命中 3 · 射击 4 · 准确率 75%')
    expect(hud).not.toHaveTextContent('得分')
    expect(hud).toHaveStyle({ pointerEvents: 'none' })
    view.rerender(<FpsStage {...props} scene={{ ...scene, feedback: { kind: 'spheretrack90', covered: false } }} />)
    expect(hud).toHaveTextContent('离靶')
    expect(hud).toHaveStyle({ color: '#ff675e' })
    view.rerender(<FpsStage {...props} scene={{ ...scene, feedback: { kind: 'spheretrack90', covered: true } }} />)
    expect(hud).toHaveTextContent('瞄准中')
    expect(hud).toHaveStyle({ color: '#7cf5c7' })
    view.unmount()
  })

  it('uses the actual runtime duration for trial feedback rather than the formal rule duration', async () => {
    const { FpsStage } = await import('./FpsStage')
    const props = { viewport, dpr: 1, canvasRef: createRef<HTMLCanvasElement>(), onError: vi.fn() }
    const current = { ...scene, at_ms: 12500, feedback: { kind: 'multi_target' as const, shots: 4, hits: 3 } }
    const view = render(<FpsStage {...props} durationMs={20000} scene={current} />)
    const hud = screen.getByLabelText('FPS 实时反馈')
    expect(hud).toHaveTextContent('12.5 / 20.0 秒')
    expect(hud).not.toHaveTextContent('60.0 秒')
    view.rerender(<FpsStage {...props} durationMs={60000} scene={current} />)
    expect(hud).toHaveTextContent('12.5 / 60.0 秒')
    view.unmount()
  })

  it('can keep shooting feedback outside the canvas without moving the canvas or hiding its crosshair', async () => {
    const { FpsStage } = await import('./FpsStage')
    const props = { viewport, dpr: 1, canvasRef: createRef<HTMLCanvasElement>(), onError: vi.fn() }
    const shooting = { ...scene, feedback: { kind: 'multi_target' as const, shots: 4, hits: 3 } }
    const view = render(<FpsStage {...props} scene={shooting} feedbackOutsideCanvas />)
    expect(screen.queryByLabelText('FPS 实时反馈')).toBeNull()
    expect(screen.getByText('+')).toBeInTheDocument()
    expect(props.canvasRef.current?.parentElement).toHaveStyle({ width: '800px', height: '450px' })
    view.rerender(<FpsStage {...props} scene={shooting} />)
    expect(screen.getByLabelText('FPS 实时反馈')).toHaveStyle({ position: 'absolute', top: '12px', left: '12px' })
    view.unmount()
  })
})
