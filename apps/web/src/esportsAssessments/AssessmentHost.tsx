import { useCallback, useEffect, useEffectEvent, useMemo, useRef, useState, type MouseEvent, type PointerEvent } from 'react'
import { flushSync } from 'react-dom'
import { gradeLabels, previewTaskConfig, validateSettings, type ControlSettings, type EnvironmentSnapshot, type TaskConfig } from './contracts'
import { checkCapabilities, capabilityReasonLabels } from './engineRuntime/capabilities'
import { SimulationClock } from './engineRuntime/clock'
import { sameStageGeometry } from './engineRuntime/geometry'
import { FrameQualityTracker } from './engineRuntime/quality'
import type { GameEngine, GameFactory, Rect, SceneSnapshot, Size } from './engineRuntime/types'
import { FpsStage } from './fps/FpsStage'
import { MobaStage } from './moba/MobaStage'
import { DesktopInputRouter, requestDesktopPointerLock } from './input/desktop'
import { TouchInputRouter } from './input/touch'
import { TouchFpsControls } from './input/TouchFpsControls'
import { TouchMobaControls } from './input/TouchMobaControls'
import { ControlSettingsPanel } from './settings/ControlSettingsPanel'
import { defaultControlSettings, validateLayout } from './settings/layout'
import { activeMobileFpsControls } from './suites/mobileFps'
import { assessmentTaskLabel, type HostPhase } from './hostState'
import type { AssessmentSessionController } from './useAssessmentSession'
import { Metrics, QualityNotice } from './reportPresentation'
import { AssessmentNavigationGuard } from './AssessmentNavigationGuard'
import { MobaInstructions } from './MobaInstructions'
import { MobaFeedback } from './MobaFeedback'
import './reports.css'

const TRIAL_SEED = 0x74726961
const activePhases: HostPhase[] = ['trial', 'starting', 'countdown', 'running']
type Runtime = { engine: GameEngine; clock: SimulationClock; quality: FrameQualityTracker; startedAt: number; observedMs: number; bounds: Rect; router: TouchInputRouter | DesktopInputRouter }

function MovementFeedback({ scene }: { scene: SceneSnapshot | null }) {
  const feedback = scene?.kind === 'fps' ? scene.movement_feedback : undefined
  if (!feedback) return null
  const state = feedback.state === 'ready' ? '等待有效移动'
    : feedback.state === 'moving' ? `已连续有效移动 ${feedback.qualifying_ms} ms`
      : feedback.state === 'stationary-hit' ? '本次命中无效：命中前未满足移动条件'
        : '本次为有效移动命中'
  return <section aria-label="移动射击反馈" className="assessmentMobaFeedback assessmentMovementFeedback" title={`移动中命中才有效；${state}`}>
    <span className="assessmentMobaFeedbackState">移动中命中才有效</span>
    <span className="assessmentMobaFeedbackOutcome">{state}</span>
  </section>
}

export interface AssessmentHostProps {
  config: TaskConfig
  factory: GameFactory
  session: AssessmentSessionController
  mode?: 'formal' | 'practice'
  allowStart?: boolean
  preparationNotice?: string
  onBack: () => void
}

export function AssessmentHost({ config, factory, session, mode = 'formal', allowStart = true, preparationNotice, onBack }: AssessmentHostProps) {
  const mobile = config.suite_id.startsWith('mobile')
  const fps = config.suite_id.endsWith('fps')
  const movingHitFeedback = config.engine_version === 'mobile-fps-v3' && config.test_id === 'move-fire'
  const directTouchMap = ['mobile-moba-v2', 'mobile-moba-v3'].includes(config.engine_version) && config.test_id === 'map-awareness'
  const lockedMouse = config.suite_id === 'pc-fps'
  const host = useRef<HTMLElement | null>(null)
  const canvas = useRef<HTMLCanvasElement | null>(null)
  const container = useRef<HTMLDivElement | null>(null)
  const [viewport, setViewport] = useState<Size>({ width: 800, height: 450 })
  const [settings, setSettings] = useState(() => defaultControlSettings(config.suite_id))
  const [phase, setPhase] = useState<HostPhase>('intro')
  const phaseRef = useRef<HostPhase>('intro')
  const [scene, setScene] = useState<SceneSnapshot | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [exitUnresolved, setExitUnresolved] = useState(false)
  const leaving = useRef(false)
  const [observed, setObserved] = useState<EnvironmentSnapshot | null>(null)
  const [trialDone, setTrialDone] = useState(false)
  const [mobileImmersive, setMobileImmersive] = useState(false)
  const [remaining, setRemaining] = useState(0)
  const [runTiming, setRunTiming] = useState<{ start: number; deadline: number; finish?: number } | null>(null)
  const [stageReady, setStageReady] = useState(false)
  const stageFailed = useRef(false)
  const runtime = useRef<Runtime | null>(null)
  const allocatedBounds = useRef<Rect | null>(null)
  const countdownUntil = useRef(0)
  const countdownClock = useRef<SimulationClock | null>(null)
  const frameId = useRef(0)
  const generation = useRef(0)
  const mounted = useRef(true)
  const lockRequest = useRef<AbortController | null>(null)
  const lockAllowed = useRef(false)
  const rawConfirmed = useRef(false)
  const rawRequested = useRef(false)
  const inputKind = useRef<'mouse' | 'touch' | 'unknown'>('unknown')
  const pressedButton = useRef<{ target: EventTarget; phase: HostPhase } | null>(null)
  const captured = useRef(new Set<number>())
  const latest = useRef({ session, onBack, settings, viewport })
  latest.current = { session, onBack, settings, viewport }
  const controls = useMemo(() => config.suite_id === 'mobile-fps' ? activeMobileFpsControls(config, settings)
    : ['mobile-moba-v2', 'mobile-moba-v3'].includes(config.engine_version) && config.test_id === 'map-awareness' ? []
    : settings.controls.filter(control => control.id === 'move' || (control.id === 'skill' ? config.test_id === 'skill-aim' : ['move-attack', 'map-awareness'].includes(config.test_id))), [config, settings])

  const transition = (next: HostPhase) => { phaseRef.current = next; pressedButton.current = null; setPhase(next) }
  const exitLock = () => { lockAllowed.current = false; lockRequest.current?.abort(); if (canvas.current && document.pointerLockElement === canvas.current) document.exitPointerLock?.() }
  const stop = () => {
    cancelAnimationFrame(frameId.current)
    const active = runtime.current
    runtime.current = null
    if (active) active.router.cancel(Math.max(active.engine.scene().at_ms, performance.now() - active.startedAt))
    for (const id of captured.current) { try { canvas.current?.releasePointerCapture(id) } catch { /* already released */ } }
    captured.current.clear()
    pressedButton.current = null
  }
  const interrupt = (reason: string) => {
    const previous = phaseRef.current
    if (!activePhases.includes(previous)) return
    generation.current += 1
    transition('interrupted')
    setRunTiming(null)
    stop(); exitLock(); setNotice(reason)
    if (previous !== 'trial') void latest.current.session.interrupt()
  }
  const onReady = useCallback(() => { if (mounted.current && !stageFailed.current) setStageReady(true) }, [])
  const onStageError = (error: { message: string }) => {
    stageFailed.current = true; setStageReady(false)
    interrupt(error.message); setNotice(error.message)
  }
  const bounds = (): Rect => {
    const rect = canvas.current?.getBoundingClientRect()
    return { x: rect?.x ?? 0, y: rect?.y ?? 0, width: rect?.width ?? viewport.width, height: rect?.height ?? viewport.height }
  }
  function fitViewport() {
    const width = Math.max(1, Math.min(container.current?.getBoundingClientRect().width ?? 1000, (window.innerHeight - (mobile ? 72 : 180)) * 16 / 9))
    setViewport({ width, height: width * 9 / 16 })
  }
  function probe(): EnvironmentSnapshot {
    const rect = bounds()
    const result: EnvironmentSnapshot = {
      input_kind: inputKind.current,
      is_phone: /iPhone|Android.*Mobile/i.test(navigator.userAgent),
      touch_capable: navigator.maxTouchPoints > 0 || 'ontouchstart' in window,
      width_css_px: rect.width, height_css_px: rect.height, device_pixel_ratio: window.devicePixelRatio || 1,
      pointer_lock_supported: typeof canvas.current?.requestPointerLock === 'function',
      pointer_lock_active: document.pointerLockElement === canvas.current && canvas.current !== null,
      raw_mouse_requested: rawRequested.current, raw_mouse_confirmed: rawConfirmed.current,
      webgl2_supported: fps ? Boolean(canvas.current?.getContext('webgl2')) : false,
    }
    setObserved(result)
    return result
  }
  function validate(env: EnvironmentSnapshot, value: ControlSettings): boolean {
    if (mobile && window.innerWidth <= window.innerHeight) { setNotice('请将手机手动旋转为横屏。'); return false }
    const checked = checkCapabilities(config.suite_id, env)
    if (!checked.allowed) { setNotice(checked.reasons.map(reason => capabilityReasonLabels[reason]).join('；')); return false }
    try {
      validateSettings(config, value, env)
      const preview = factory.create(config, TRIAL_SEED, value, bounds()).scene()
      const errors = validateLayout(controls, bounds(), safety(preview))
      if (errors.length) throw new Error(errors.join('；'))
      return true
    } catch (error) { setNotice(error instanceof Error ? error.message : '控件布局无效'); return false }
  }
  function safety(snapshot = scene) {
    const rect = bounds()
    const map = snapshot?.kind === 'moba' && snapshot.minimap.visible ? snapshot.minimap.bounds : null
    return { usable: rect, reserved: map ? [{ x: rect.x + map.x / 1000 * rect.width, y: rect.y + map.y / 562.5 * rect.height, width: map.width / 1000 * rect.width, height: map.height / 562.5 * rect.height }] : [] }
  }
  function createRuntime(task: TaskConfig, seed: number, value: ControlSettings, startedAt: number, trial: boolean) {
    setRunTiming(trial ? null : { start: startedAt, deadline: startedAt + 60000 })
    const rect = bounds()
    const engine = factory.create(task, seed, value, rect)
    const logical = engine.scene().kind === 'moba' ? { width: 1000, height: 562.5 } : rect
    const emit = (action: Parameters<GameEngine['input']>[0]) => engine.input(action)
    const router = mobile ? new TouchInputRouter({ bounds: rect, logical_size: logical, sensitivity: value.sensitivity, controls,
      ...(fps ? { aim_area: rect } : {}),
      get_skill_origin: () => { const currentScene = engine.scene(); return currentScene.kind === 'moba' ? currentScene.player.position : { x: 0, y: 0 } },
    }, emit, {
      capture(id) { try { canvas.current?.setPointerCapture(id); captured.current.add(id) } catch { /* unsupported capture still receives local events */ } },
      release(id) { captured.current.delete(id); try { canvas.current?.releasePointerCapture(id) } catch { /* already released */ } },
    }) : new DesktopInputRouter({ suite_id: lockedMouse ? 'pc-fps' : 'pc-moba', bounds: rect, logical_size: logical, sensitivity: value.sensitivity }, emit)
    runtime.current = { engine, router, clock: new SimulationClock(startedAt, trial ? 20000 : 60000), quality: new FrameQualityTracker(), startedAt, observedMs: 0, bounds: rect }
    setScene(engine.scene())
  }
  function endTrial() {
    stop(); transition('preflight'); setTrialDone(true); exitLock(); setNotice('试练结束。确认设置后开始，正式计时为 60 秒。')
  }
  const tick = useEffectEvent(() => {
    // Allocation and input use performance.now(). RAF's shared paint timestamp can
    // precede allocation; mixing it in here falsely trips the backwards-clock guard.
    const now = performance.now()
    let countdownCrossingGap: number | undefined
    if (phaseRef.current === 'countdown') {
      if (!allocatedBounds.current || !sameStageGeometry(allocatedBounds.current, bounds())) { interrupt('实际场地尺寸已变化，本项已中断。'); return }
      const countdownFrame = countdownClock.current?.frame(now)
      if (countdownFrame?.interrupted) { interrupt(countdownFrame.frame_gap_ms > 500 ? '倒计时帧间隙超过 500ms，本项已中断。' : '计时时间异常，本项已中断，请重新开始。'); return }
      setRemaining(Math.max(0, Math.ceil((countdownUntil.current - now) / 1000)))
      if (now >= countdownUntil.current) {
        const attempt = latest.current.session.serverView
        // The server attempt is stored before countdown; no speculative raw/config is used.
        if (!attempt) { interrupt('尝试状态丢失，请重新对账。'); return }
        stop()
        // A late RAF must not move the fixed 60-second window past the lease.
        createRuntime(attempt.config, attempt.seed, attempt.settings, countdownUntil.current, false)
        countdownCrossingGap = countdownFrame?.frame_gap_ms
        transition('running')
      }
    }
    if (['trial', 'running'].includes(phaseRef.current)) {
      const active = runtime.current
      if (!active) return
      const rect = bounds()
      if (!sameStageGeometry(active.bounds, rect)) { interrupt('实际场地尺寸已变化，本项已中断。'); return }
      const frame = active.clock.frame(now)
      if (frame.interrupted) { interrupt(frame.frame_gap_ms > 500 ? '帧间隙超过 500ms，本项已中断。' : '计时时间异常，本项已中断，请重新开始。'); return }
      const realGap = countdownCrossingGap ?? frame.frame_gap_ms
      const overlap = Math.min(realGap, Math.max(0, frame.elapsed_ms - active.observedMs))
      active.quality.recordFrameGap(realGap, overlap)
      active.observedMs = frame.elapsed_ms
      for (const time of frame.tick_times_ms) if (time > active.engine.scene().at_ms) active.engine.advanceTo(time)
      setScene(active.engine.scene())
      setRemaining(Math.ceil(((phaseRef.current === 'trial' ? 20000 : 60000) - frame.elapsed_ms) / 1000))
      if (frame.finished) {
        if (phaseRef.current === 'trial') { endTrial(); return }
        const observedFinish = performance.now()
        setRunTiming({ start: active.startedAt, deadline: active.startedAt + 60000, finish: observedFinish })
        const raw = active.engine.finish()
        const quality = active.quality.snapshot(60000)
        stop(); transition('pending'); exitLock()
        void latest.current.session.submit(raw, quality)
        return
      }
    }
    frameId.current = requestAnimationFrame(tick)
  })
  function beginLoop() { cancelAnimationFrame(frameId.current); frameId.current = requestAnimationFrame(tick) }
  function gestureLock(wantRaw: boolean): Promise<boolean> {
    rawRequested.current = lockedMouse && wantRaw
    if (!lockedMouse) return Promise.resolve(true)
    const element = canvas.current
    if (!element || typeof element.requestPointerLock !== 'function') return Promise.resolve(false)
    if (document.pointerLockElement === element) return Promise.resolve(true)
    lockRequest.current?.abort()
    const abort = new AbortController()
    lockRequest.current = abort
    lockAllowed.current = true
    // The browser call occurs synchronously in this initiating click, before any network await.
    return requestDesktopPointerLock({ requestPointerLock(options) {
      const request = element.requestPointerLock(options)
      if (request && typeof request.then === 'function') return request.then(() => {
        if (abort.signal.aborted || !mounted.current || !lockAllowed.current) { if (document.pointerLockElement === element) document.exitPointerLock?.() }
      })
      return request
    } }, wantRaw, { events: document, isActive: () => document.pointerLockElement === element, signal: abort.signal }).then(result => {
      if (abort.signal.aborted || !mounted.current || !lockAllowed.current) return false
      rawConfirmed.current = result.raw_mouse_confirmed
      return result.pointer_lock_active
    })
  }
  function guarded(event: MouseEvent<HTMLButtonElement>, action: () => void) {
    if (event.detail !== 0 && (pressedButton.current?.target !== event.currentTarget || pressedButton.current.phase !== phaseRef.current)) return
    pressedButton.current = null
    if (inputKind.current === 'unknown' && event.detail === 0) inputKind.current = mobile ? 'unknown' : 'mouse'
    action()
  }
  function buttonPress(event: PointerEvent<HTMLButtonElement>) {
    inputKind.current = event.pointerType === 'touch' ? 'touch' : event.pointerType === 'mouse' ? 'mouse' : 'unknown'
    pressedButton.current = { target: event.currentTarget, phase: phaseRef.current }
  }
  async function prepare(start: boolean) {
    if (!allowStart || activePhases.includes(phaseRef.current) || !stageReady || session.busy || !session.ready || session.revoked) return
    leaving.current = false
    setExitUnresolved(false)
    const token = ++generation.current
    const owner = session.accountId
    setRunTiming(null)
    const chosen = { ...settings }
    const requested = gestureLock(chosen.mouse_mode === 'raw')
    if (mobile) {
      // Commit the final scene layout before probing/freezing its dimensions.
      // Keep it until this keyed Host unmounts, including settings and results.
      flushSync(() => setMobileImmersive(true))
      flushSync(fitViewport)
      if (host.current) host.current.scrollTop = 0
    }
    setNotice(null)
    const hasLock = await requested
    if (!mounted.current || token !== generation.current || owner !== latest.current.session.accountId) return
    const env = probe()
    const frozen = { ...chosen, mouse_mode: lockedMouse ? rawConfirmed.current ? 'raw' as const : 'relative' as const : chosen.mouse_mode }
    if (!hasLock || !validate(env, frozen)) { exitLock(); transition('preflight'); return }
    if (!start) {
      createRuntime(previewTaskConfig(config, config.difficulty), TRIAL_SEED, frozen, performance.now(), true)
      transition('trial'); beginLoop(); return
    }
    allocatedBounds.current = envToRect(env)
    transition('starting')
    const result = await latest.current.session.start({ test_id: config.test_id, settings: frozen, environment: env, difficulty: config.difficulty })
    if (!mounted.current || token !== generation.current || owner !== latest.current.session.accountId) {
      // Never issue an old account's interruption with a new account's credentials.
      if (mounted.current && owner === latest.current.session.accountId && result && !leaving.current) void latest.current.session.interrupt()
      return
    }
    if (!result) { transition('preflight'); exitLock(); return }
    if (result.leaseMs < 63000 || result.attempt.state !== 'running' || (lockedMouse && document.pointerLockElement !== canvas.current) || !sameStageGeometry(allocatedBounds.current, bounds()) || !sameStageGeometry(allocatedBounds.current, envToRect(result.attempt.environment))) {
      transition('interrupted'); exitLock(); setNotice(result.leaseMs < 63000 ? '服务器占用时间不足 63 秒，请对账后重新开始。' : '启动期间设备状态变化，请对账后重新开始。')
      void latest.current.session.interrupt(); return
    }
    setSettings(result.attempt.settings)
    countdownUntil.current = performance.now() + 3000
    countdownClock.current = new SimulationClock(countdownUntil.current - 3000, 3000)
    transition('countdown'); setRemaining(3); beginLoop()
  }
  function route(event: PointerEvent<HTMLCanvasElement>, kind: 'down' | 'move' | 'up') {
    if (!['trial', 'running'].includes(phaseRef.current)) return
    if (!mobile && !fps && kind !== 'move' && event.button !== 0 && event.button !== 2) return
    const active = runtime.current
    if (!active) return
    const time = performance.now() - active.startedAt
    if (time < 0 || time >= (phaseRef.current === 'trial' ? 20000 : 60000)) return
    if (event.pointerType === 'mouse' || event.pointerType === 'touch') active.quality.observeInput(event.pointerType)
    if (mobile !== (event.pointerType === 'touch')) return
    // Advance to actual input time BEFORE get_skill_origin, including movement during a held joystick.
    active.engine.advanceTo(time)
    const rect = bounds()
    const point = { pointerId: event.pointerId, x: event.clientX - rect.x + active.bounds.x, y: event.clientY - rect.y + active.bounds.y, at_ms: time, button: event.button }
    if (active.router instanceof TouchInputRouter) active.router[kind](point)
    else if (kind !== 'move') active.router[kind](point)
    setScene(active.engine.scene())
  }
  const inputHandlers = {
    onPointerDown: (event: PointerEvent<HTMLCanvasElement>) => route(event, 'down'),
    onPointerMove: (event: PointerEvent<HTMLCanvasElement>) => route(event, 'move'),
    onPointerUp: (event: PointerEvent<HTMLCanvasElement>) => route(event, 'up'),
    onPointerCancel: () => interrupt('触点已取消，本项已中断。'),
    onLostPointerCapture: (event: PointerEvent<HTMLCanvasElement>) => { if (captured.current.has(event.pointerId)) interrupt('触点捕获丢失，本项已中断。') },
    onMouseMove: (event: MouseEvent<HTMLCanvasElement>) => {
      const active = runtime.current
      if (!lockedMouse || !active || !['trial', 'running'].includes(phaseRef.current) || document.pointerLockElement !== canvas.current) return
      const time = performance.now() - active.startedAt
      if (time >= (phaseRef.current === 'trial' ? 20000 : 60000) || time < 0) return
      active.quality.observeInput('mouse')
      if (active.router instanceof DesktopInputRouter) active.router.move({ movementX: event.movementX, movementY: event.movementY, at_ms: time })
    },
    onContextMenu: (event: MouseEvent<HTMLCanvasElement>) => { if (fps || (config.suite_id === 'pc-moba' && activePhases.includes(phaseRef.current))) event.preventDefault() },
  }
  useEffect(() => {
    const stage = canvas.current
    if (config.suite_id !== 'pc-moba' || !stage) return
    // Capture native defaults on both halves of a click. Some browsers navigate
    // on mouseup/auxclick, not pointerdown; never cancel the game event itself.
    const prevent = (event: Event) => {
      if (activePhases.includes(phaseRef.current) && (event as globalThis.MouseEvent).button !== 0) event.preventDefault()
    }
    const events = ['pointerdown', 'pointerup', 'mousedown', 'mouseup', 'auxclick', 'contextmenu']
    for (const name of events) stage.addEventListener(name, prevent, { capture: true, passive: false })
    return () => { for (const name of events) stage.removeEventListener(name, prevent, true) }
  }, [config.suite_id])
  const lifecycle = useEffectEvent((reason: string) => interrupt(reason))
  useEffect(() => {
    if (session.serverView && ['expired', 'interrupted'].includes(session.serverView.state) && !activePhases.includes(phaseRef.current)) {
      phaseRef.current = 'interrupted'; pressedButton.current = null; setPhase('interrupted')
    }
  }, [session.serverView?.state, session.serverView?.id])
  useEffect(() => {
    mounted.current = true
    const resize = () => {
      if (activePhases.includes(phaseRef.current)) {
        const frozenBounds = runtime.current?.bounds ?? allocatedBounds.current
        if (frozenBounds && !sameStageGeometry(frozenBounds, bounds())) lifecycle('实际场地尺寸已变化，本项已中断。')
        return
      }
      fitViewport()
    }
    const hidden = () => { if (document.hidden) lifecycle('页面进入后台，本项已中断。') }
    const blur = () => lifecycle('窗口失去焦点，本项已中断。')
    const rotate = () => lifecycle('屏幕方向变化，本项已中断。')
    const lock = () => {
      if (canvas.current && document.pointerLockElement === canvas.current) {
        if (!lockAllowed.current) document.exitPointerLock?.()
      } else if (lockedMouse && activePhases.includes(phaseRef.current)) lifecycle('鼠标锁定已退出，本项已中断。')
    }
    resize()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(resize)
    if (container.current) observer?.observe(container.current)
    window.addEventListener('resize', resize); window.addEventListener('blur', blur); window.addEventListener('orientationchange', rotate)
    document.addEventListener('visibilitychange', hidden); document.addEventListener('pointerlockchange', lock)
    return () => {
      mounted.current = false; generation.current += 1; stop(); exitLock(); observer?.disconnect()
      window.removeEventListener('resize', resize); window.removeEventListener('blur', blur); window.removeEventListener('orientationchange', rotate)
      document.removeEventListener('visibilitychange', hidden); document.removeEventListener('pointerlockchange', lock)
    }
    // One host owns one lifecycle. Parent keys it by account/route/test.
  }, [])
  const persisted = session.saved !== null
  const recovery = !activePhases.includes(phase) && phase !== 'pending' && session.serverView && ['running', 'awaiting_submission'].includes(session.serverView.state)
  async function leave() {
    const owner = latest.current.session.accountId
    const previous = phaseRef.current
    leaving.current = true
    generation.current += 1
    stop(); exitLock()
    if (activePhases.includes(previous)) { transition('interrupted'); setRunTiming(null) }
    setExitUnresolved(true)
    const allowed = previous === 'trial' || await latest.current.session.leave()
    if (!mounted.current || latest.current.session.accountId !== owner) return false
    setExitUnresolved(!allowed)
    if (!allowed) setNotice('退出未完成，请保留本页并重新对账。')
    return allowed
  }
  const buttonProps = { onPointerDown: buttonPress, onPointerCancel: () => { pressedButton.current = null } }
  return <section ref={host} aria-label="测试场景" data-host-phase={persisted ? 'saved' : phase} data-run-start-ms={runTiming?.start} data-run-deadline-ms={runTiming?.deadline} data-run-finish-ms={runTiming?.finish} className={`assessmentHost${mobileImmersive ? ' assessmentHostImmersive' : ''}`}>
    <AssessmentNavigationGuard active={!persisted && (activePhases.includes(phase) || phase === 'pending' || Boolean(session.pending) || Boolean(recovery) || exitUnresolved)} pending={Boolean(session.pending)} volatile={session.volatile} onLeave={leave} />
    <header className={`assessmentHostToolbar${movingHitFeedback ? ' assessmentHostToolbarMovement' : ''}`}>{mobile && !fps ? <div className="assessmentMobaMobileTitle"><h2>{assessmentTaskLabel(config)}</h2><MobaFeedback scene={scene?.kind === 'moba' ? scene : null} compact /></div> : <h2>{assessmentTaskLabel(config)}</h2>}{movingHitFeedback && <MovementFeedback scene={scene} />}<span aria-live="polite">{phase === 'countdown' ? `倒计时 ${remaining}` : phase === 'running' || phase === 'trial' ? `剩余 ${remaining} 秒` : mode === 'practice' ? `练习难度 ${config.difficulty}` : '正式固定难度'}</span>
      <button {...buttonProps} type="button" onClick={event => guarded(event, () => { if (activePhases.includes(phaseRef.current)) interrupt('已主动退出，本项已中断。'); else { exitLock(); onBack() } })}>{activePhases.includes(phase) ? '退出本项' : '返回项目列表'}</button></header>
    {!fps && !activePhases.includes(phase) && !persisted && !recovery && !session.pending && phase !== 'pending' && <MobaInstructions config={config} />}
    <div ref={container} className="assessmentStageContainer"><div style={{ position: 'relative', width: viewport.width, height: viewport.height, maxWidth: '100%' }}>
      {fps ? <FpsStage scene={scene?.kind === 'fps' ? scene : null} viewport={viewport} dpr={window.devicePixelRatio || 1} canvasRef={canvas} onReady={onReady} onError={onStageError} inputHandlers={inputHandlers} />
        : <MobaStage scene={scene?.kind === 'moba' ? scene : null} viewport={viewport} dpr={window.devicePixelRatio || 1} canvasRef={canvas} onReady={onReady} onError={onStageError} inputHandlers={inputHandlers} />}
      {mobile && (fps ? <TouchFpsControls controls={controls} /> : <TouchMobaControls controls={controls} disabled={!['trial', 'running'].includes(phase)} />)}
    </div></div>
    {!fps && !mobile && <MobaFeedback scene={scene?.kind === 'moba' ? scene : null} />}
    {(notice || session.error) && <p role="alert">{session.error ?? notice}</p>}
    {!allowStart && <p role="status">此专项暂未开放新的尝试；已有结果可以继续对账保存。</p>}
    {session.volatile && <p role="alert">结果暂存仅在当前页面有效，刷新或关闭页面后可能无法恢复。可以在本页继续在线保存。</p>}
    {persisted && session.saved ? <section className="assessmentReports" aria-label="结果已保存"><h3>结果已保存</h3><p>{mode === 'practice' ? '练习只保留原始指标，不计入正式进度，不生成正式评分或报告。' : '本项已由服务器确认。'}</p>
      <Metrics metrics={session.saved.scored.metrics} emptyLabel={config.report_version === `${config.suite_id}-v2` ? '—' : undefined} /><QualityNotice flags={session.saved.scored.quality_flags} />
      {session.saved.mode === 'formal' && <p>{session.saved.scored.quality_flags.length ? '试算分／需复测：' : '本项评分：'}{session.saved.scored.score} 分 · {gradeLabels[session.saved.scored.grade]}</p>}
      <button {...buttonProps} onClick={event => guarded(event, onBack)}>{mode === 'practice' ? '重新练习' : '继续下一项'}</button></section>
      : recovery || session.pending || phase === 'pending' ? <section aria-label="恢复与保存"><p>{session.pending ? '有完整待保存结果，请使用原操作键手动重试。' : '发现未完成尝试，不能恢复或拼接计时。请对账后结束未完成尝试。'}</p>
        <button {...buttonProps} disabled={session.busy} onClick={event => guarded(event, () => void session.reconcile())}>重新对账</button>
        {session.pending && <button {...buttonProps} disabled={session.busy} onClick={event => guarded(event, () => void session.retry())}>手动重试保存</button>}
        <button {...buttonProps} disabled={session.busy} onClick={event => guarded(event, () => void session.interrupt())}>结束未完成尝试并重新开始</button></section>
      : !activePhases.includes(phase) && !session.revoked && !session.canRestart ? <button {...buttonProps} onClick={event => guarded(event, onBack)}>返回项目列表重新开始</button>
      : !activePhases.includes(phase) && !session.revoked ? <>
        {preparationNotice && <p role="note">{preparationNotice}</p>}
        <section aria-label="设备检查"><p>内部训练参考；浏览器能力检查不是设备认证或防作弊证明。手机请横屏，PC FPS 使用鼠标锁定。</p>
          {mobile && window.innerWidth <= window.innerHeight && <p data-capability-reason="landscape">请将手机手动旋转为横屏。</p>}
          {fps && <p>{lockedMouse ? '移动鼠标瞄准，左键射击。按 ESC 退出锁定会中断本项。' : '在场景空白区域滑动瞄准，点开火按钮；移动射击时同时使用左侧摇杆。'}</p>}
          {config.catalog_version === `${config.suite_id}-v3` && config.test_id === 'switching' && <p>首次命中后第二靶出现；第二靶的切换用时从首次命中时刻开始，先完成两靶组再逐步提速。</p>}
          {config.params.kind === 'move_fire_v3' && <p>移动中命中才有效：命中前须以至少 {config.params.min_moving_speed_m_s} m/s（满速的一半）持续 {config.params.min_moving_ms} 毫秒，并在轮末到达检查点。</p>}
          {config.test_id === 'tracking' && <p>跟踪项目无需开火，只测量准星覆盖目标的真实时长。</p>}
          {config.test_id === 'recognition' && <p>三角形「可射击」目标可以攻击，方形「禁止射击」目标不要攻击。</p>}
          {observed && <><ul>{checkCapabilities(config.suite_id, observed).reasons.map(reason => <li key={reason} data-capability-reason={reason}>{capabilityReasonLabels[reason]}</li>)}</ul><details><summary>实际设备检查信息</summary><pre>{JSON.stringify(observed, null, 2)}</pre></details></>}
        </section>
        {directTouchMap ? <p>本项直接点选，无需摇杆或攻击按钮设置。</p> : <ControlSettingsPanel suite={config.suite_id} value={settings} bounds={bounds()} safety={safety()} disabled={session.busy} onChange={value => { setSettings(value); setTrialDone(false) }} />}
        {!trialDone ? <button {...buttonProps} disabled={!allowStart || !stageReady || !session.ready || session.busy} onClick={event => guarded(event, () => void prepare(false))}>检查设备并进入试练</button>
          : <><button {...buttonProps} disabled={!allowStart || !stageReady || !session.ready || session.busy} onClick={event => guarded(event, () => void prepare(true))}>{mode === 'formal' ? '开始正式测试' : '开始练习'}</button><button {...buttonProps} disabled={!allowStart || !stageReady || session.busy} onClick={event => guarded(event, () => void prepare(false))}>再次试练</button></>}
      </> : null}
    {phase === 'trial' && <button {...buttonProps} onClick={event => guarded(event, endTrial)}>结束试练</button>}
    {!session.ready && <p role="status">正在查询服务器恢复状态…</p>}
    {session.error && !session.revoked && !session.pending && !recovery && <button {...buttonProps} onClick={event => guarded(event, () => void session.reconcile())}>重新对账</button>}
  </section>
}
function envToRect(environment: EnvironmentSnapshot): Rect { return { x: 0, y: 0, width: environment.width_css_px, height: environment.height_css_px } }
export default AssessmentHost
