import { useCallback, useEffect, useEffectEvent, useRef, useState, type RefObject } from 'react'
import type { MobaStageProps } from '../moba/MobaStage'
import { createLaneRenderer, type LaneRenderer } from './renderer'

export interface LaneStageError {
  readonly code: 'canvas-unavailable' | 'canvas-render-failed' | 'webgl-context-lost'
  readonly message: string
}
export interface LaneStageProps extends Omit<MobaStageProps, 'onError'> {
  readonly onError: (error: LaneStageError) => void
  readonly pickerRef?: RefObject<LaneRenderer['pick'] | null>
}

export function LaneStage({ scene, viewport, dpr, canvasRef, pickerRef, onReady, onError, inputHandlers, className, children }: LaneStageProps) {
  const canvas = useRef<HTMLCanvasElement | null>(null)
  const renderer = useRef<LaneRenderer | null>(null)
  const stopped = useRef(false)
  const ready = useRef(false)
  const [error, setError] = useState<LaneStageError | null>(null)
  const report = useEffectEvent((failure: LaneStageError) => {
    if (stopped.current) return
    stopped.current = true
    setError(failure)
    // The Host must receive the interruption before renderer resources disappear.
    onError(failure)
  })
  const reportReady = useEffectEvent(() => onReady?.())
  const attachCanvas = useCallback((element: HTMLCanvasElement | null) => {
    canvas.current = element
    if (typeof canvasRef === 'function') return canvasRef(element)
    if (canvasRef) canvasRef.current = element
  }, [canvasRef])
  useEffect(() => {
    const element = canvas.current
    if (!element) return
    stopped.current = false; ready.current = false
    const lost = (event: Event) => {
      event.preventDefault()
      report({ code: 'webgl-context-lost', message: 'WebGL 上下文丢失，本次兵线测试已中断，请重新开始' })
      if (pickerRef) pickerRef.current = null
      renderer.current?.dispose(); renderer.current = null
    }
    element.addEventListener('webglcontextlost', lost)
    try {
      renderer.current = createLaneRenderer(element)
      if (pickerRef) pickerRef.current = point => renderer.current?.pick(point)
    }
    catch (cause) { report({ code: 'canvas-unavailable', message: cause instanceof Error ? cause.message : '无法创建 WebGL 兵线场景' }) }
    return () => {
      element.removeEventListener('webglcontextlost', lost)
      if (pickerRef) pickerRef.current = null
      renderer.current?.dispose(); renderer.current = null
    }
  }, [pickerRef])
  useEffect(() => {
    const current = renderer.current
    if (!current || stopped.current) return
    try {
      current.resize(viewport.width, viewport.height, dpr)
      current.render(scene)
      if (!ready.current) { ready.current = true; reportReady() }
    } catch (cause) {
      report({ code: 'canvas-render-failed', message: cause instanceof Error ? cause.message : '兵线场景绘制失败' })
    }
  }, [scene, viewport.width, viewport.height, dpr])
  return <div className={className} style={{ position: 'relative', width: viewport.width, height: viewport.height, background: '#182c32' }}>
    <canvas ref={attachCanvas} {...inputHandlers} aria-label="MOBA 兵线测评场景" style={{ display: 'block', width: '100%', height: '100%', touchAction: 'none' }} />
    {children}
    {error && <p role="alert" style={{ position: 'absolute', inset: 16, color: '#fff', pointerEvents: 'none' }}>{error.message}</p>}
  </div>
}
export default LaneStage
