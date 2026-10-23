import { useCallback, useEffect, useEffectEvent, useRef, type CanvasHTMLAttributes, type ReactNode, type Ref } from 'react'
import type { MobaSceneSnapshot, Size } from '../engineRuntime/types'
import { createMobaRenderer, type MobaRenderer } from './renderer'

export type MobaStageInputHandlers = Pick<CanvasHTMLAttributes<HTMLCanvasElement>,
  'onPointerDown' | 'onPointerMove' | 'onPointerUp' | 'onPointerCancel' | 'onLostPointerCapture' |
  'onMouseDown' | 'onMouseMove' | 'onMouseUp' | 'onContextMenu'>

export interface MobaStageError {
  readonly code: 'canvas-unavailable' | 'canvas-render-failed'
  readonly message: string
}

export interface MobaStageProps {
  readonly scene: MobaSceneSnapshot | null
  /** Actual contained game rectangle, not an outer letterboxed container. */
  readonly viewport: Size
  readonly dpr: number
  readonly canvasRef: Ref<HTMLCanvasElement>
  readonly onReady?: () => void
  readonly onError: (error: MobaStageError) => void
  readonly inputHandlers?: MobaStageInputHandlers
  readonly className?: string
  readonly children?: ReactNode
}

export function MobaStage({ scene, viewport, dpr, canvasRef, onReady, onError, inputHandlers, className, children }: MobaStageProps) {
  const canvas = useRef<HTMLCanvasElement | null>(null)
  const renderer = useRef<MobaRenderer | null>(null)
  const ready = useRef(false)
  const reportError = useEffectEvent((code: MobaStageError['code'], error: unknown) => onError({ code, message: error instanceof Error ? error.message : String(error) }))
  const reportReady = useEffectEvent(() => onReady?.())
  const attachCanvas = useCallback((element: HTMLCanvasElement | null) => {
    canvas.current = element
    if (typeof canvasRef === 'function') return canvasRef(element)
    if (canvasRef) canvasRef.current = element
  }, [canvasRef])

  useEffect(() => {
    try { renderer.current = createMobaRenderer(canvas.current!) }
    catch (error) { reportError('canvas-unavailable', error) }
    return () => { renderer.current?.dispose(); renderer.current = null; ready.current = false }
  }, [])

  useEffect(() => {
    if (!renderer.current) return
    try {
      renderer.current.resize(viewport.width, viewport.height, dpr)
      renderer.current.render(scene)
      if (!ready.current) { ready.current = true; reportReady() }
    } catch (error) { reportError('canvas-render-failed', error) }
  }, [scene, viewport.width, viewport.height, dpr])

  return <div className={className} style={{ position: 'relative', width: viewport.width, height: viewport.height }}>
    <canvas ref={attachCanvas} aria-label="MOBA 测评场景" style={{ display: 'block', touchAction: 'none' }} {...inputHandlers} />
    {children}
  </div>
}

export default MobaStage
