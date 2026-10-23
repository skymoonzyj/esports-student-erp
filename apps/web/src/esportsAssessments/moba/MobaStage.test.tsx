import { createRef } from 'react'
import { fireEvent, render } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { fixtureConfig, fixtureSettings } from '../testSupport/fixtures'
import { createDodgeEngine } from './dodge'
import MobaStage from './MobaStage'

afterEach(() => vi.restoreAllMocks())

it('keeps the same canvas across null and running phases, renders synchronously without RAF, and forwards host input once', () => {
  const context = { setTransform: vi.fn(), clearRect: vi.fn(), fillRect: vi.fn(), strokeRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), arc: vi.fn(), fill: vi.fn(), stroke: vi.fn(), closePath: vi.fn(), fillText: vi.fn(), save: vi.fn(), restore: vi.fn(), rect: vi.fn(), clip: vi.fn(), setLineDash: vi.fn() }
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D)
  const raf = vi.spyOn(window, 'requestAnimationFrame')
  const ref = createRef<HTMLCanvasElement>()
  const ready = vi.fn(), error = vi.fn(), pointer = vi.fn()
  const props = { scene: null, viewport: { width: 800, height: 450 }, dpr: 1, canvasRef: ref, onReady: ready, onError: error, inputHandlers: { onPointerDown: pointer } }
  const view = render(<MobaStage {...props}><button>退出</button></MobaStage>)
  const canvas = ref.current!
  expect(canvas).toBeInstanceOf(HTMLCanvasElement)
  expect(canvas.width).toBe(800)
  expect(ready).toHaveBeenCalledTimes(1)
  const game = createDodgeEngine(fixtureConfig('pc-moba', 'dodge'), 123, fixtureSettings('pc-moba'))
  const scene = game.scene()
  if (scene.kind !== 'moba') throw new Error('scene')
  view.rerender(<MobaStage {...props} scene={scene} dpr={2}><button>退出</button></MobaStage>)
  expect(ref.current).toBe(canvas)
  expect(canvas.width).toBe(1600)
  fireEvent.pointerDown(canvas)
  expect(pointer).toHaveBeenCalledTimes(1)
  expect(raf).not.toHaveBeenCalled()
  view.rerender(<MobaStage {...props} />)
  expect(ref.current).toBe(canvas)
  expect(error).not.toHaveBeenCalled()
  view.unmount()
  expect(ref.current).toBeNull()
})

it('reports renderer capability failure instead of falsely reporting readiness', () => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
  const ready = vi.fn(), error = vi.fn()
  render(<MobaStage scene={null} viewport={{ width: 800, height: 450 }} dpr={1} canvasRef={createRef()} onReady={ready} onError={error} />)
  expect(error).toHaveBeenCalledWith(expect.objectContaining({ code: 'canvas-unavailable', message: expect.any(String) }))
  expect(ready).not.toHaveBeenCalled()
})
