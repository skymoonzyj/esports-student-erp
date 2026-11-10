import { createRef } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type * as ThreeModule from 'three'
import { LaneStage, type LaneStageError } from './LaneStage'
import type { LaneRenderer } from './renderer'
import { createLaneEngine } from './engine'
import { fixtureMobaV4Config } from '../testSupport/fixturesMobaV4'
import { fixtureSettings } from '../testSupport/fixtures'
import { laneWorldToScreen } from './projection'
import type { MobaSceneSnapshot } from '../engineRuntime/types'

// Only the GPU boundary is replaced: real stage, scene, resources and projection run.
const gpu = vi.hoisted(() => ({ events: [] as string[], failRender: false, failCreate: false }))
vi.mock('three', async importOriginal => {
  const actual = await importOriginal<typeof ThreeModule>()
  return { ...actual, WebGLRenderer: class {
    constructor() { if (gpu.failCreate) throw new Error('GPU unavailable') }
    shadowMap = { enabled: false }
    setPixelRatio() {}
    setSize() {}
    render() { if (gpu.failRender) throw new Error('GPU render failed'); gpu.events.push('render') }
    dispose() { gpu.events.push('dispose') }
  } }
})
afterEach(() => { cleanup(); gpu.events.length = 0; gpu.failRender = false; gpu.failCreate = false })

describe('LaneStage real renderer lifecycle', () => {
  it('exposes picking from the rendered frame and removes it on context loss/unmount', () => {
    const engine = createLaneEngine(fixtureMobaV4Config('melee'), 1, fixtureSettings('pc-moba'))
    engine.advanceTo(30600)
    const scene = engine.scene() as MobaSceneSnapshot
    const target = scene.lane!.units.find(unit => unit.id === 'enemy-1-2')!
    const pickerRef = createRef<LaneRenderer['pick']>()
    const canvasRef = createRef<HTMLCanvasElement>()
    const mounted = render(<LaneStage scene={scene} viewport={{ width: 800, height: 450 }} dpr={2} pickerRef={pickerRef} canvasRef={canvasRef} onError={() => {}} />)
    expect(pickerRef.current?.(laneWorldToScreen(target.position, 27))).toBe('enemy-1-2')
    fireEvent(canvasRef.current!, new Event('webglcontextlost', { cancelable: true }))
    expect(pickerRef.current).toBeNull()
    mounted.unmount()
    expect(pickerRef.current).toBeNull()
  })
  it('signals ready after a rendered frame and forwards input and ref', () => {
    const ref = createRef<HTMLCanvasElement>()
    const down = vi.fn()
    render(<LaneStage scene={null} viewport={{ width: 800, height: 450 }} dpr={2} canvasRef={ref} onReady={() => gpu.events.push('ready')} onError={() => gpu.events.push('error')} inputHandlers={{ onPointerDown: down }} />)
    expect(gpu.events).toEqual(['render', 'ready'])
    expect(ref.current).toBe(screen.getByLabelText('MOBA 兵线测评场景'))
    fireEvent.pointerDown(ref.current!)
    expect(down).toHaveBeenCalledTimes(1)
  })
  it('reports context loss before disposal and never quietly resumes', () => {
    const onError = vi.fn<(error: LaneStageError) => void>(() => { gpu.events.push('error') })
    const props = { scene: null, viewport: { width: 800, height: 450 }, dpr: 1, canvasRef: createRef<HTMLCanvasElement>(), onError }
    const mounted = render(<LaneStage {...props} />)
    gpu.events.length = 0
    const event = new Event('webglcontextlost', { cancelable: true })
    fireEvent(props.canvasRef.current!, event)
    expect(event.defaultPrevented).toBe(true)
    expect(onError.mock.calls[0][0].code).toBe('webgl-context-lost')
    expect(gpu.events).toEqual(['error', 'dispose'])
    expect(screen.getByRole('alert')).toHaveTextContent('中断')
    mounted.rerender(<LaneStage {...props} dpr={2} />)
    expect(gpu.events).toEqual(['error', 'dispose'])
  })
  it('reports constructor failure without a ready signal', () => {
    gpu.failCreate = true
    const ready = vi.fn(); const error = vi.fn()
    render(<LaneStage scene={null} viewport={{ width: 800, height: 450 }} dpr={1} canvasRef={createRef()} onReady={ready} onError={error} />)
    expect(error.mock.calls[0][0].code).toBe('canvas-unavailable')
    expect(ready).not.toHaveBeenCalled()
  })
  it('reports resize/render failure once and releases GPU on unmount', () => {
    gpu.failRender = true
    const error = vi.fn()
    const mounted = render(<LaneStage scene={null} viewport={{ width: 800, height: 450 }} dpr={1} canvasRef={createRef()} onError={error} />)
    expect(error.mock.calls[0][0].code).toBe('canvas-render-failed')
    mounted.unmount()
    expect(gpu.events).toEqual(['dispose'])
  })
})
