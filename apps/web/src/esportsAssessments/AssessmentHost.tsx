import { useCallback, useEffect, useEffectEvent, useMemo, useRef, useState, type MouseEvent, type PointerEvent } from 'react'
import { flushSync } from 'react-dom'
import { gradeLabels, isLaneTaskConfig, previewTaskConfig, validateSettings, type ControlSettings, type EnvironmentSnapshot, type TaskConfig } from './contracts'
import { checkCapabilities, capabilityReasonLabels } from './engineRuntime/capabilities'
import { SimulationClock } from './engineRuntime/clock'
import { sameStageGeometry } from './engineRuntime/geometry'
import { FrameQualityTracker } from './engineRuntime/quality'
import type { GameEngine, GameFactory, Rect, SceneSnapshot, Size } from './engineRuntime/types'
import { FpsFeedback, FpsStage } from './fps/FpsStage'
import { validateFpsViewport } from './fps'
import { isPcFpsTriadCatalog } from './versionRules'
import { MobaStage } from './moba/MobaStage'
import { LaneStage } from './lane/LaneStage'
import type { LaneRenderer } from './lane/renderer'
import { laneScreenToWorld } from './lane/projection'
import { LaneFeedback } from './lane/LaneFeedback'
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
type Runtime = { engine: GameEngine; clock: SimulationClock; quality: FrameQualityTracker; durationMs: number; startedAt: number; observedMs: number; bounds: Rect; router: TouchInputRouter | DesktopInputRouter }

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
  const lane = isLaneTaskConfig(config)
  const movingHitFeedback = config.engine_version === 'mobile-fps-v3' && config.test_id === 'move-fire'
  const directTouchMap = ['mobile-moba-v2', 'mobile-moba-v3'].includes(config.engine_version) && config.test_id === 'map-awareness'
  const lockedMouse = config.suite_id === 'pc-fps'
  const fixedBoardFeedback = config.catalog_version === 'pc-fps-v5' && config.params.kind === 'multi_target'
  const host = useRef<HTMLElement | null>(null)
  const canvas = useRef<HTMLCanvasElement | null>(null)
  const container = useRef<HTMLDivElement | null>(null)
  const [viewport, setViewport] = useState<Size>({ width: 800, height: 450 })
  const [settings, setSettings] = useState(() => defaultControlSettings(config.suite_id))
  const [phase, setPhase] = useState<HostPhase>('intro')
  const [sceneDurationMs, setSceneDurationMs] = useState<number>(config.duration_ms)
  const fpsDurationMs = sceneDurationMs === 20000 ? 20000 : 60000
  const phaseRef = useRef<HostPhase>('intro')
  const [scene, setScene] = useState<SceneSnapshot | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [exitUnresolved, setExitUnresolved] = useState(false)
  const leaving = useRef(false)
  const [observed, setObserved] = useState<EnvironmentSnapshot | null>(null)
  const [trialDone, setTrialDone] = useState(false)
  const [mobileImmersive, setMobileImmersive] = useState(false)
  const [fullscreen, setFullscreen] = useState(false)
  const [fullscreenPending, setFullscreenPending] = useState(false)
  const [preparing, setPreparing] = useState(false)
  const preparingRef = useRef(false)
  const fullscreenOwned = useRef(false)
  const fullscreenAllowed = useRef(false)
  const fullscreenRequestPending = useRef(false)
  const fullscreenOperation = useRef<number | null>(null)
  const fullscreenOperationSequence = useRef(0)
  const fullscreenLayoutPending = useRef(false)
  const fullscreenGeneration = useRef(0)
  const fullscreenLayoutGeneration = useRef(0)
  const fullscreenFrame = useRef(0)
  const [remaining, setRemaining] = useState(0)
  const [runTiming, setRunTiming] = useState<{ start: number; deadline: number; finish?: number } | null>(null)
  const [stageReady, setStageReady] = useState(false)
  const stageFailed = useRef(false)
  const runtime = useRef<Runtime | null>(null)
  const lanePicker = useRef<LaneRenderer['pick'] | null>(null)
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
    // PC FPS start() owns a busy allocation; its stale continuation interrupts
    // once after the reply, rather than losing an early call behind that busy gate.
    if (previous !== 'trial' && !(lockedMouse && previous === 'starting')) void latest.current.session.interrupt()
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
    // Fullscreen reserves 64px toolbar + 24px hint + 16px gaps + 16px padding.
    // Preparation/results live below this fixed stage; hiding them never resizes it.
    const reserve = document.fullscreenElement === host.current && lockedMouse ? 120 : mobile ? 72 : lane ? 240 : 180
    const width = Math.max(1, Math.min(container.current?.getBoundingClientRect().width ?? 1000, (window.innerHeight - reserve) * 16 / 9))
    setViewport({ width, height: width * 9 / 16 })
  }
  function updateFullscreenPending() {
    if (mounted.current) setFullscreenPending(fullscreenRequestPending.current || fullscreenLayoutPending.current)
  }
  function settleFullscreenLayout() {
    const token = ++fullscreenLayoutGeneration.current
    cancelAnimationFrame(fullscreenFrame.current)
    fullscreenLayoutPending.current = true
    updateFullscreenPending()
    let previous: Rect | null = null
    const sample = () => {
      if (!mounted.current || token !== fullscreenLayoutGeneration.current) return
      flushSync(fitViewport)
      const current = bounds()
      if (previous && sameStageGeometry(previous, current)) {
        fullscreenLayoutPending.current = false
        updateFullscreenPending()
        return
      }
      previous = current
      fullscreenFrame.current = requestAnimationFrame(sample)
    }
    fullscreenFrame.current = requestAnimationFrame(sample)
  }
  async function exitOwnedFullscreen(element: HTMLElement | null) {
    if (!element || document.fullscreenElement !== element) return
    try { await document.exitFullscreen?.() } catch { /* Browser may already be leaving fullscreen. */ }
  }
  function beginFullscreenOperation() {
    const operation = ++fullscreenOperationSequence.current
    fullscreenOperation.current = operation
    fullscreenRequestPending.current = true
    updateFullscreenPending()
    return operation
  }
  function finishFullscreenOperation(operation: number, owner: string) {
    // Lifecycle invalidation cannot strand a mounted Host, and an obsolete
    // completion cannot release a newer native operation's gate.
    if (fullscreenOperation.current !== operation) return
    fullscreenOperation.current = null
    fullscreenRequestPending.current = false
    if (mounted.current && owner === latest.current.session.accountId) settleFullscreenLayout()
  }
  function cleanupOwnedFullscreen(element: HTMLElement | null) {
    if (!element || document.fullscreenElement !== element || fullscreenRequestPending.current) return
    const owner = latest.current.session.accountId
    const operation = beginFullscreenOperation()
    void exitOwnedFullscreen(element).finally(() => finishFullscreenOperation(operation, owner))
  }
  function clearFullscreen() {
    if (!lockedMouse) return
    fullscreenAllowed.current = false
    fullscreenGeneration.current += 1
    // A native request cannot be cancelled. Its own continuation still owns the
    // operation gate and must finish any late acquisition cleanup before release.
    cleanupOwnedFullscreen(host.current)
    settleFullscreenLayout()
  }
  function syncFullscreen() {
    const own = document.fullscreenElement === host.current && host.current !== null
    if (!own && !fullscreenOwned.current) return
    if (own && !fullscreenAllowed.current) {
      cleanupOwnedFullscreen(host.current)
      return
    }
    const exited = fullscreenOwned.current && !own
    fullscreenOwned.current = own
    flushSync(() => setFullscreen(own))
    if (host.current) host.current.scrollTop = 0
    if (exited) {
      if (preparingRef.current) {
        generation.current += 1; exitLock(); transition('interrupted'); setNotice('全屏已退出，本项已中断。')
      } else interrupt('全屏已退出，本项已中断。')
    }
    settleFullscreenLayout()
  }
  async function toggleFullscreen() {
    const element = host.current
    if (!lockedMouse || !element || activePhases.includes(phaseRef.current) || preparingRef.current || fullscreenRequestPending.current || fullscreenLayoutPending.current) return
    if (typeof element.requestFullscreen !== 'function' || document.fullscreenEnabled === false) { setNotice('当前浏览器不支持全屏，仍可在窗口模式进行测试。'); return }
    const token = ++fullscreenGeneration.current
    const owner = latest.current.session.accountId
    fullscreenAllowed.current = true
    const operation = beginFullscreenOperation()
    setNotice(null)
    try {
      // Keep this native request in the click, separate from pointer lock and allocation.
      if (document.fullscreenElement === element) await document.exitFullscreen()
      else await element.requestFullscreen()
      if (!mounted.current || token !== fullscreenGeneration.current || owner !== latest.current.session.accountId) { await exitOwnedFullscreen(element); return }
      syncFullscreen()
    } catch {
      if (mounted.current && token === fullscreenGeneration.current && owner === latest.current.session.accountId) setNotice('全屏切换未成功，可继续在窗口模式测试，或手动重试。')
      else await exitOwnedFullscreen(element)
    } finally {
      finishFullscreenOperation(operation, owner)
    }
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
      webgl2_supported: fps || lane ? Boolean(canvas.current?.getContext('webgl2')) : false,
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
      if (lane && (env.width_css_px <= 0 || env.height_css_px <= 0 || Math.abs(env.width_css_px / env.height_css_px - 16 / 9) > 0.01)) throw new Error('兵线场地必须为有效的 16:9 视野。')
      if (fps) validateFpsViewport(config, { width: env.width_css_px, height: env.height_css_px })
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
    const durationMs = trial ? 20000 : task.duration_ms
    setSceneDurationMs(durationMs)
    setRemaining(durationMs / 1000)
    setRunTiming(trial ? null : { start: startedAt, deadline: startedAt + durationMs })
    const rect = bounds()
    const engine = factory.create(task, seed, value, rect)
    const logical = engine.scene().kind === 'moba' ? { width: 1000, height: 562.5 } : rect
    const emit = (action: Parameters<GameEngine['input']>[0]) => {
      if (isLaneTaskConfig(task) && action.kind === 'point') {
        const point = laneScreenToWorld(action)
        if (point) engine.input({ ...action, ...point, target_id: lanePicker.current?.(action) })
      } else engine.input(action)
    }
    const router = mobile ? new TouchInputRouter({ bounds: rect, logical_size: logical, sensitivity: value.sensitivity, controls,
      ...(fps ? { aim_area: rect } : {}),
      get_skill_origin: () => { const currentScene = engine.scene(); return currentScene.kind === 'moba' ? currentScene.player.position : { x: 0, y: 0 } },
    }, emit, {
      capture(id) { try { canvas.current?.setPointerCapture(id); captured.current.add(id) } catch { /* unsupported capture still receives local events */ } },
      release(id) { captured.current.delete(id); try { canvas.current?.releasePointerCapture(id) } catch { /* already released */ } },
    }) : new DesktopInputRouter({ suite_id: lockedMouse ? 'pc-fps' : 'pc-moba', bounds: rect, logical_size: logical, sensitivity: value.sensitivity, duration_ms: durationMs }, emit)
    runtime.current = { engine, router, clock: new SimulationClock(startedAt, durationMs), quality: new FrameQualityTracker(durationMs), durationMs, startedAt, observedMs: 0, bounds: rect }
    setScene(engine.scene())
  }
  function endTrial() {
    stop(); transition('preflight'); setTrialDone(true); exitLock(); setNotice(`试练结束。确认设置后开始，正式计时为 ${config.duration_ms / 1000} 秒。`)
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
      setRemaining(Math.ceil((active.durationMs - frame.elapsed_ms) / 1000))
      if (frame.finished) {
        if (phaseRef.current === 'trial') { endTrial(); return }
        const observedFinish = performance.now()
        setRunTiming({ start: active.startedAt, deadline: active.startedAt + active.durationMs, finish: observedFinish })
        const raw = active.engine.finish()
        const quality = active.quality.snapshot(active.durationMs)
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
    if (!allowStart || activePhases.includes(phaseRef.current) || preparingRef.current || fullscreenRequestPending.current || fullscreenLayoutPending.current || !stageReady || session.busy || !session.ready || session.revoked) return
    preparingRef.current = true; setPreparing(true)
    leaving.current = false
    setExitUnresolved(false)
    const token = ++generation.current
    const owner = session.accountId
    setRunTiming(null)
    const chosen = { ...settings }
    const requested = gestureLock(chosen.mouse_mode === 'raw')
    if (lane) {
      // The initiating button sits below the instructions. Focus the complete
      // stage while still preparing, before probing/freezing any geometry.
      flushSync(fitViewport)
      host.current?.scrollIntoView?.({ block: 'start', inline: 'nearest', behavior: 'instant' })
    }
    if (lockedMouse && document.fullscreenElement === host.current && host.current) host.current.scrollTop = 0
    if (mobile) {
      // Commit the final scene layout before probing/freezing its dimensions.
      // Keep it until this keyed Host unmounts, including settings and results.
      flushSync(() => setMobileImmersive(true))
      flushSync(fitViewport)
      if (host.current) host.current.scrollTop = 0
    }
    setNotice(null)
    const hasLock = await requested
    preparingRef.current = false
    if (mounted.current) setPreparing(false)
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
    const result = await latest.current.session.start({ test_id: config.test_id, settings: frozen, environment: env, difficulty: config.difficulty, ...(isLaneTaskConfig(config) ? { attack_mode: config.params.attack_mode } : {}) })
    if (!mounted.current || token !== generation.current || owner !== latest.current.session.accountId) {
      // Never issue an old account's interruption with a new account's credentials.
      if (mounted.current && owner === latest.current.session.accountId && result && !leaving.current) void latest.current.session.interrupt()
      return
    }
    if (!result) { transition('preflight'); exitLock(); return }
    const requiredLeaseMs = result.attempt.config.duration_ms + 3000
    if (result.leaseMs < requiredLeaseMs || result.attempt.state !== 'running' || (lockedMouse && document.pointerLockElement !== canvas.current) || !sameStageGeometry(allocatedBounds.current, bounds()) || !sameStageGeometry(allocatedBounds.current, envToRect(result.attempt.environment))) {
      transition('interrupted'); exitLock(); setNotice(result.leaseMs < requiredLeaseMs ? `服务器占用时间不足 ${requiredLeaseMs / 1000} 秒，请对账后重新开始。` : '启动期间设备状态变化，请对账后重新开始。')
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
    if (time < 0 || time >= active.durationMs) return
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
      if (time >= active.durationMs || time < 0) return
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
    const fullscreenElement = host.current
    const full = () => { if (lockedMouse) syncFullscreen() }
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
    document.addEventListener('fullscreenchange', full)
    return () => {
      mounted.current = false; generation.current += 1; stop(); exitLock(); observer?.disconnect()
      fullscreenAllowed.current = false; fullscreenGeneration.current += 1; fullscreenLayoutGeneration.current += 1
      cancelAnimationFrame(fullscreenFrame.current)
      document.removeEventListener('fullscreenchange', full)
      if (!fullscreenRequestPending.current) void exitOwnedFullscreen(fullscreenElement)
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
    stop(); exitLock(); clearFullscreen()
    if (activePhases.includes(previous)) { transition('interrupted'); setRunTiming(null) }
    setExitUnresolved(true)
    const allowed = previous === 'trial' || await latest.current.session.leave()
    if (!mounted.current || latest.current.session.accountId !== owner) return false
    setExitUnresolved(!allowed)
    if (!allowed) setNotice('退出未完成，请保留本页并重新对账。')
    return allowed
  }
  const buttonProps = { onPointerDown: buttonPress, onPointerCancel: () => { pressedButton.current = null } }
  return <section ref={host} aria-label="测试场景" data-host-phase={persisted ? 'saved' : phase} data-run-start-ms={runTiming?.start} data-run-deadline-ms={runTiming?.deadline} data-run-finish-ms={runTiming?.finish} className={`assessmentHost${mobileImmersive ? ' assessmentHostImmersive' : ''}${fullscreen ? ' assessmentHostFullscreen' : ''}`}>
    <AssessmentNavigationGuard active={!persisted && (activePhases.includes(phase) || phase === 'pending' || Boolean(session.pending) || Boolean(recovery) || exitUnresolved)} pending={Boolean(session.pending)} volatile={session.volatile} onLeave={leave} />
    <header style={fixedBoardFeedback ? { height: 64, minHeight: 64, flexWrap: 'nowrap' } : undefined} className={`assessmentHostToolbar${movingHitFeedback ? ' assessmentHostToolbarMovement' : ''}`}>{mobile && !fps ? <div className="assessmentMobaMobileTitle"><h2>{assessmentTaskLabel(config)}</h2><MobaFeedback scene={scene?.kind === 'moba' ? scene : null} compact /></div> : <h2>{assessmentTaskLabel(config)}</h2>}{movingHitFeedback && <MovementFeedback scene={scene} />}{fixedBoardFeedback && <div aria-label="固定靶区反馈栏" style={{ width: 220, height: 44, flexShrink: 0, display: 'grid', alignItems: 'center' }}><FpsFeedback scene={scene?.kind === 'fps' ? scene : null} durationMs={fpsDurationMs} outsideCanvas /></div>}<span aria-live="polite">{phase === 'countdown' ? `倒计时 ${remaining}` : phase === 'running' || phase === 'trial' ? lane ? `剩余 ${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}` : `剩余 ${remaining} 秒` : mode === 'practice' ? `练习难度 ${config.difficulty}` : '正式固定难度'}</span>
      {lockedMouse && <button {...buttonProps} type="button" disabled={activePhases.includes(phase) || preparing || fullscreenPending || document.fullscreenEnabled === false || typeof document.documentElement.requestFullscreen !== 'function'} onClick={event => guarded(event, () => void toggleFullscreen())}>{fullscreen ? '退出全屏' : '进入全屏'}</button>}
      {fullscreen && phase === 'trial' && <button {...buttonProps} type="button" onClick={event => guarded(event, endTrial)}>结束试练</button>}
      <button {...buttonProps} type="button" onClick={event => guarded(event, () => { if (activePhases.includes(phaseRef.current)) interrupt('已主动退出，本项已中断。'); else { exitLock(); clearFullscreen(); onBack() } })}>{activePhases.includes(phase) ? '退出本项' : '返回项目列表'}</button></header>
    {lockedMouse && <p className="assessmentFullscreenHint" role="note">{document.fullscreenEnabled === false || typeof document.documentElement.requestFullscreen !== 'function' ? '当前浏览器不支持全屏，仍可在窗口模式测试。' : fullscreenPending ? '正在切换显示模式，请等待场地尺寸稳定。' : '可在开始前选择全屏；Esc 退出全屏或鼠标锁定会中断当前测试。'}</p>}
    {!fps && !lane && !activePhases.includes(phase) && !persisted && !recovery && !session.pending && phase !== 'pending' && <MobaInstructions config={config} />}
    <div ref={container} className="assessmentStageContainer"><div style={{ position: 'relative', width: viewport.width, height: viewport.height, maxWidth: '100%' }}>
      {fps ? <FpsStage scene={scene?.kind === 'fps' ? scene : null} durationMs={fpsDurationMs} feedbackOutsideCanvas={fixedBoardFeedback} viewport={viewport} dpr={window.devicePixelRatio || 1} canvasRef={canvas} onReady={onReady} onError={onStageError} inputHandlers={inputHandlers} />
        : lane ? <LaneStage scene={scene?.kind === 'moba' ? scene : null} viewport={viewport} dpr={window.devicePixelRatio || 1} canvasRef={canvas} pickerRef={lanePicker} onReady={onReady} onError={onStageError} inputHandlers={inputHandlers} />
        : <MobaStage scene={scene?.kind === 'moba' ? scene : null} viewport={viewport} dpr={window.devicePixelRatio || 1} canvasRef={canvas} onReady={onReady} onError={onStageError} inputHandlers={inputHandlers} />}
      {mobile && (fps ? <TouchFpsControls controls={controls} /> : <TouchMobaControls controls={controls} disabled={!['trial', 'running'].includes(phase)} />)}
    </div></div>
    {lane ? <LaneFeedback lane={scene?.kind === 'moba' ? scene.lane : undefined} elapsedMs={scene?.at_ms ?? 0} waveCount={config.params.wave_count === 15 ? 15 : 10} trial={phase === 'trial'} remainingSeconds={phase === 'running' || phase === 'trial' ? remaining : config.duration_ms / 1000} /> : !fps && !mobile && <MobaFeedback scene={scene?.kind === 'moba' ? scene : null} />}
    {lane && !activePhases.includes(phase) && !persisted && !recovery && !session.pending && phase !== 'pending' && <MobaInstructions config={config} />}
    {(notice || session.error) && <p role="alert">{session.error ?? notice}</p>}
    {!allowStart && <p role="status">此专项暂未开放新的尝试；已有结果可以继续对账保存。</p>}
    {session.volatile && <p role="alert">结果暂存仅在当前页面有效，刷新或关闭页面后可能无法恢复。可以在本页继续在线保存。</p>}
    {persisted && session.saved ? <section className="assessmentReports" aria-label="结果已保存"><h3>结果已保存</h3><p>{mode === 'practice' ? '练习只保留原始指标，不计入正式进度，不生成正式评分或报告。' : '本项已由服务器确认。'}</p>
      {isPcFpsTriadCatalog(config.suite_id, config.catalog_version) && <p>ERP 暂定场景规则，不等同官方分数或职业能力结论。</p>}
      <Metrics metrics={session.saved.scored.metrics} emptyLabel={config.report_version === `${config.suite_id}-v2` ? '—' : undefined} /><QualityNotice flags={session.saved.scored.quality_flags} />
      {session.saved.mode === 'formal' && <p>{session.saved.scored.quality_flags.length ? '试算分／需复测：' : '本项评分：'}{session.saved.scored.score} 分 · {gradeLabels[session.saved.scored.grade]}</p>}
      <button {...buttonProps} onClick={event => guarded(event, onBack)}>{mode === 'practice' ? '重新练习' : '继续下一项'}</button></section>
      : recovery || session.pending || phase === 'pending' ? <section aria-label="恢复与保存"><p>{session.pending ? '有完整待保存结果，请使用原操作键手动重试。' : '发现未完成尝试，不能恢复或拼接计时。请对账后结束未完成尝试。'}</p>
        <button {...buttonProps} disabled={session.busy} onClick={event => guarded(event, () => void session.reconcile())}>重新对账</button>
        {session.pending && <button {...buttonProps} disabled={session.busy} onClick={event => guarded(event, () => void session.retry())}>手动重试保存</button>}
        <button {...buttonProps} disabled={session.busy} onClick={event => guarded(event, () => void session.interrupt())}>结束未完成尝试并重新开始</button></section>
      : !activePhases.includes(phase) && !session.revoked && !session.canRestart ? <>{mode === 'practice' && config.suite_id === 'pc-moba' && <p>本次练习已结束。请返回项目列表，选择当前近战／远程人物后新建练习；已保存成绩保持原版本和人物。</p>}<button {...buttonProps} onClick={event => guarded(event, onBack)}>返回项目列表重新开始</button></>
      : !activePhases.includes(phase) && !session.revoked ? <>
        {preparationNotice && <p role="note">{preparationNotice}</p>}
        <section aria-label="设备检查"><p>内部训练参考；浏览器能力检查不是设备认证或防作弊证明。手机请横屏，PC FPS 使用鼠标锁定。</p>
          {mobile && window.innerWidth <= window.innerHeight && <p data-capability-reason="landscape">请将手机手动旋转为横屏。</p>}
          {fps && <p>{lockedMouse ? config.params.kind === 'spheretrack90' ? '移动鼠标持续跟随目标，无需开火。按 ESC 退出锁定会中断本项。' : '移动鼠标瞄准，左键射击。按 ESC 退出锁定会中断本项。' : '在场景空白区域滑动瞄准，点开火按钮；移动射击时同时使用左侧摇杆。'}</p>}
          {config.params.kind === 'multi_target' && <p>同时保持 {config.params.target_count} 个目标；每次左键按下只打一枪，长按不连发。命中后补一个新目标，其余目标保留；未命中也计入射击次数。</p>}
          {config.catalog_version === 'pc-fps-v5' && config.params.kind === 'multi_target' && <p>目标固定在靶区内，视角到边缘会停止转动；反向移动立即响应。命中只替换被击中的目标，其余目标保持原位置。</p>}
          {isPcFpsTriadCatalog(config.suite_id, config.catalog_version) && <p>ERP 暂定场景规则，不等同官方分数或职业能力结论。</p>}
          {config.catalog_version === `${config.suite_id}-v3` && config.test_id === 'switching' && <p>首次命中后第二靶出现；第二靶的切换用时从首次命中时刻开始，先完成两靶组再逐步提速。</p>}
          {config.params.kind === 'move_fire_v3' && <p>移动中命中才有效：命中前须以至少 {config.params.min_moving_speed_m_s} m/s（满速的一半）持续 {config.params.min_moving_ms} 毫秒，并在轮末到达检查点。</p>}
          {(config.test_id === 'tracking' || config.test_id === 'spheretrack90') && <p>跟踪项目无需开火，只测量准星覆盖目标的真实时长。</p>}
          {config.test_id === 'recognition' && <p>三角形「可射击」目标可以攻击，方形「禁止射击」目标不要攻击。</p>}
          {observed && <><ul>{checkCapabilities(config.suite_id, observed).reasons.map(reason => <li key={reason} data-capability-reason={reason}>{capabilityReasonLabels[reason]}</li>)}</ul><details><summary>实际设备检查信息</summary><pre>{JSON.stringify(observed, null, 2)}</pre></details></>}
        </section>
        {directTouchMap ? <p>本项直接点选，无需摇杆或攻击按钮设置。</p> : <ControlSettingsPanel suite={config.suite_id} value={settings} bounds={bounds()} safety={safety()} disabled={!session.ready || session.busy} onChange={value => { setSettings(value); setTrialDone(false) }} />}
        {!trialDone ? <button {...buttonProps} disabled={!allowStart || !stageReady || !session.ready || session.busy || preparing || fullscreenPending} onClick={event => guarded(event, () => void prepare(false))}>检查设备并进入试练</button>
          : <><button {...buttonProps} disabled={!allowStart || !stageReady || !session.ready || session.busy || preparing || fullscreenPending} onClick={event => guarded(event, () => void prepare(true))}>{mode === 'formal' ? '开始正式测试' : '开始练习'}</button><button {...buttonProps} disabled={!allowStart || !stageReady || session.busy || preparing || fullscreenPending} onClick={event => guarded(event, () => void prepare(false))}>再次试练</button></>}
      </> : null}
    {phase === 'trial' && !fullscreen && <button {...buttonProps} onClick={event => guarded(event, endTrial)}>结束试练</button>}
    {!session.ready && <p role="status">正在查询服务器恢复状态…</p>}
    {session.error && !session.revoked && !session.pending && !recovery && <button {...buttonProps} onClick={event => guarded(event, () => void session.reconcile())}>重新对账</button>}
  </section>
}
function envToRect(environment: EnvironmentSnapshot): Rect { return { x: 0, y: 0, width: environment.width_css_px, height: environment.height_css_px } }
export default AssessmentHost
