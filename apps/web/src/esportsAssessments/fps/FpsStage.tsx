import { useCallback, useEffect, useLayoutEffect, useRef, useState, type CanvasHTMLAttributes, type Ref } from 'react'
import type { FpsSceneSnapshot, Size } from '../engineRuntime/types'
import type { FpsRenderer } from './renderer'

export interface FpsStageError {
  readonly code: 'webgl2-unavailable' | 'context-lost' | 'renderer-load-failed'
  readonly message: string
}

export interface FpsStageProps {
  readonly scene: FpsSceneSnapshot | null
  /** Display-only runtime window; trial scenes are not full formal attempts. */
  readonly durationMs?: 20000 | 60000
  /** The Host reserves a stable toolbar slot for fixed-board feedback. */
  readonly feedbackOutsideCanvas?: boolean
  readonly viewport: Size
  readonly dpr: number
  readonly canvasRef: Ref<HTMLCanvasElement>
  readonly onReady?: () => void
  readonly onError: (error: FpsStageError) => void
  readonly inputHandlers?: Pick<CanvasHTMLAttributes<HTMLCanvasElement>, 'onPointerDown' | 'onPointerMove' | 'onPointerUp' | 'onPointerCancel' | 'onLostPointerCapture' | 'onMouseMove' | 'onMouseDown' | 'onMouseUp' | 'onContextMenu'>
  readonly className?: string
}

function checkpointHint(scene: FpsSceneSnapshot | null): string | null {
  const checkpoint = scene?.checkpoints.find(value => value.state !== 'hidden')
  if (!scene || !checkpoint) return null
  if (checkpoint.state === 'reached') return '移动检查点：已到位'
  const dx = checkpoint.position.x - scene.camera.position.x
  const dz = checkpoint.position.z - scene.camera.position.z
  const yaw = scene.camera.yaw_deg * Math.PI / 180
  const right = dx * Math.cos(yaw) + dz * Math.sin(yaw)
  const forward = dx * Math.sin(yaw) - dz * Math.cos(yaw)
  const direction = Math.abs(right) >= Math.abs(forward) ? right >= 0 ? '右侧 →' : '← 左侧' : forward >= 0 ? '前方 ↑' : '后方 ↓'
  return `移动检查点：${direction} · ${Math.hypot(dx, dz).toFixed(2)} m`
}

export function FpsFeedback({ scene, durationMs = 60000, outsideCanvas = false }: { scene: FpsSceneSnapshot | null; durationMs?: 20000 | 60000; outsideCanvas?: boolean }) {
  const feedback = scene?.feedback
  if (!feedback || !scene) return null
  return <p aria-label="FPS 实时反馈" style={{ ...(outsideCanvas ? { fontSize: '.75rem', lineHeight: '18px' } : { position: 'absolute' as const, top: 12, left: 12, padding: '6px 12px' }), color: feedback.kind === 'spheretrack90' && !feedback.covered ? '#ff675e' : '#7cf5c7', background: '#0b1220cc', margin: 0, pointerEvents: 'none' }}>
    {(scene.at_ms / 1000).toFixed(1)} / {(durationMs / 1000).toFixed(1)} 秒 · {feedback.kind === 'multi_target'
      ? `命中 ${feedback.hits} · 射击 ${feedback.shots} · 准确率 ${feedback.shots ? Math.round(feedback.hits / feedback.shots * 100) : 0}%`
      : feedback.covered ? '瞄准中' : '离靶'}
  </p>
}

export function FpsStage(props: FpsStageProps) {
  const canvas = useRef<HTMLCanvasElement | null>(null)
  const renderer = useRef<FpsRenderer | null>(null)
  const latest = useRef(props)
  const [error, setError] = useState<FpsStageError | null>(null)
  useLayoutEffect(() => { latest.current = props })
  const { canvasRef } = props
  const attach = useCallback((element: HTMLCanvasElement | null) => {
    canvas.current = element
    if (typeof canvasRef === 'function') return canvasRef(element)
    if (canvasRef) canvasRef.current = element
  }, [canvasRef])

  useEffect(() => {
    const element = canvas.current
    if (!element) return
    let cancelled = false
    let stopped = false
    const fail = (failure: FpsStageError) => {
      if (cancelled || stopped) return
      stopped = true
      setError(failure)
      latest.current.onError(failure)
    }
    const lost = (event: Event) => {
      event.preventDefault()
      fail({ code: 'context-lost', message: 'WebGL 上下文丢失，本次测试已中断，请重新开始' })
      const current = renderer.current
      renderer.current = null
      current?.dispose()
    }
    element.addEventListener('webglcontextlost', lost)
    if (!element.getContext('webgl2')) {
      fail({ code: 'webgl2-unavailable', message: '当前浏览器不支持 WebGL2，不能开始 FPS 测试' })
    } else {
      void import('./renderer').then(module => {
        if (cancelled || stopped) return
        const created = module.createFpsRenderer(element)
        if (cancelled || stopped) { created.dispose(); return }
        renderer.current = created
        const current = latest.current
        created.resize(current.viewport.width, current.viewport.height, current.dpr)
        if (current.scene) created.render(current.scene)
        current.onReady?.()
      }).catch(cause => {
        renderer.current?.dispose(); renderer.current = null
        fail({ code: 'renderer-load-failed', message: cause instanceof Error ? `FPS 场景加载失败：${cause.message}` : 'FPS 场景加载失败，请重新加载页面' })
      })
    }
    return () => {
      cancelled = true
      element.removeEventListener('webglcontextlost', lost)
      renderer.current?.dispose()
      renderer.current = null
    }
  }, [])

  useEffect(() => {
    const current = renderer.current
    if (!current) return
    current.resize(props.viewport.width, props.viewport.height, props.dpr)
    if (props.scene) current.render(props.scene)
  }, [props.scene, props.viewport.width, props.viewport.height, props.dpr])

  const checkpoint = checkpointHint(props.scene)
  return <div className={props.className} style={{ position: 'relative', width: props.viewport.width, height: props.viewport.height, maxWidth: '100%', background: '#0b1220' }}>
    <canvas ref={attach} {...props.inputHandlers} aria-label="FPS 测试场景" style={{ display: 'block', width: '100%', height: '100%', touchAction: 'none' }} />
    {props.scene && !error && <span aria-hidden="true" style={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)', color: '#fff', pointerEvents: 'none', font: '20px monospace' }}>+</span>}
    {checkpoint && !error && <p aria-label="移动检查点提示" style={{ position: 'absolute', top: 12, left: '50%', transform: 'translateX(-50%)', color: '#fff', background: '#0b1220cc', padding: '6px 12px', margin: 0, pointerEvents: 'none', whiteSpace: 'nowrap' }}>{checkpoint}</p>}
    {!props.feedbackOutsideCanvas && !error && <FpsFeedback scene={props.scene} durationMs={props.durationMs} />}
    {error && <p role="alert" style={{ position: 'absolute', inset: 16, color: '#fff', pointerEvents: 'none' }}>{error.message}</p>}
  </div>
}
