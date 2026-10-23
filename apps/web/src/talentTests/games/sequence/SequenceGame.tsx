import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from "react"

import type { GameProps, InputKind } from "../../contracts"
import {
  beginResponse,
  createInitialSequenceState,
  restartSequence,
  startSequence,
  submitSequenceTile,
  type SequenceState,
} from "./engine"
import "./SequenceGame.css"

const BOARD_TILES = Array.from({ length: 9 }, (_, index) => index)
const FIRST_FLASH_DELAY_MS = 600
const FLASH_DURATION_MS = 400
const FADE_DURATION_MS = 300
const INPUT_FEEDBACK_MS = 300

function pointerInputKind(pointerType: string): InputKind {
  if (pointerType === "mouse" || pointerType === "touch" || pointerType === "pen") {
    return pointerType
  }
  return "unknown"
}

function observedInputKind(kinds: ReadonlySet<InputKind>): InputKind {
  if (kinds.size === 0) return "unknown"
  if (kinds.size > 1) return "mixed"
  return kinds.values().next().value ?? "unknown"
}

export function SequenceGame({ onComplete, onInterrupted }: GameProps<"sequence">) {
  const initialState = createInitialSequenceState()
  const [state, setState] = useState<SequenceState>(initialState)
  const [activeTile, setActiveTile] = useState<number | null>(null)
  const stateRef = useRef(initialState)
  const showingTimersRef = useRef<number[]>([])
  const feedbackTimerRef = useRef<number | null>(null)
  const startedAtRef = useRef<number | null>(null)
  const inputKindsRef = useRef<Set<InputKind>>(new Set())
  const completedRef = useRef(false)
  const onCompleteRef = useRef(onComplete)
  const onInterruptedRef = useRef(onInterrupted)

  useEffect(() => {
    onCompleteRef.current = onComplete
  }, [onComplete])

  useEffect(() => {
    onInterruptedRef.current = onInterrupted
  }, [onInterrupted])

  const setCurrentState = useCallback((next: SequenceState) => {
    stateRef.current = next
    setState(next)
  }, [])

  const clearShowingTimers = useCallback(() => {
    for (const timer of showingTimersRef.current) window.clearTimeout(timer)
    showingTimersRef.current = []
  }, [])

  const clearFeedback = useCallback(() => {
    if (feedbackTimerRef.current !== null) {
      window.clearTimeout(feedbackTimerRef.current)
      feedbackTimerRef.current = null
    }
    setActiveTile(null)
  }, [])

  const clearAllTimers = useCallback(() => {
    clearShowingTimers()
    clearFeedback()
  }, [clearFeedback, clearShowingTimers])

  useEffect(() => {
    if (state.phase !== "showing") return

    clearShowingTimers()
    const stepDuration = FLASH_DURATION_MS + FADE_DURATION_MS
    for (const [index, tile] of state.sequence.entries()) {
      const startAt = FIRST_FLASH_DELAY_MS + index * stepDuration
      showingTimersRef.current.push(
        window.setTimeout(() => {
          if (stateRef.current.phase === "showing") setActiveTile(tile)
        }, startAt),
        window.setTimeout(() => {
          if (stateRef.current.phase === "showing") setActiveTile(null)
        }, startAt + FLASH_DURATION_MS),
      )
    }
    showingTimersRef.current.push(
      window.setTimeout(() => {
        const current = stateRef.current
        if (current.phase === "showing") setCurrentState(beginResponse(current))
      }, FIRST_FLASH_DELAY_MS + state.sequence.length * stepDuration),
    )

    return clearShowingTimers
  }, [clearShowingTimers, setCurrentState, state.phase, state.sequence])

  useEffect(() => {
    function handleVisibilityChange() {
      if (!document.hidden) return
      const phase = stateRef.current.phase
      if (phase !== "showing" && phase !== "responding") return

      clearAllTimers()
      startedAtRef.current = null
      inputKindsRef.current = new Set()
      completedRef.current = false
      setCurrentState(restartSequence())
      onInterruptedRef.current("hidden")
    }

    document.addEventListener("visibilitychange", handleVisibilityChange)
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange)
      clearAllTimers()
    }
  }, [clearAllTimers, setCurrentState])

  function start() {
    clearAllTimers()
    completedRef.current = false
    inputKindsRef.current = new Set()
    startedAtRef.current = performance.now()
    setCurrentState(startSequence(createInitialSequenceState(), Math.random()))
  }

  function retry() {
    clearAllTimers()
    completedRef.current = false
    inputKindsRef.current = new Set()
    startedAtRef.current = null
    setCurrentState(restartSequence())
  }

  function submitTile(tile: number, inputKind: InputKind) {
    const current = stateRef.current
    if (current.phase !== "responding" || completedRef.current) return

    inputKindsRef.current.add(inputKind)
    clearFeedback()
    setActiveTile(tile)
    feedbackTimerRef.current = window.setTimeout(() => {
      feedbackTimerRef.current = null
      setActiveTile(null)
    }, INPUT_FEEDBACK_MS)

    const next = submitSequenceTile(current, tile, Math.random())
    setCurrentState(next)
    if (next.phase !== "result" || next.failedStep === null) return

    completedRef.current = true
    const endedAt = performance.now()
    const startedAt = startedAtRef.current ?? endedAt
    onCompleteRef.current({
      test_id: "sequence",
      raw: {
        reached_level: next.level,
        completed_level: next.level - 1,
        failed_step: next.failedStep,
        end_reason: "wrong_tile",
      },
      device: {
        input_kind: observedInputKind(inputKindsRef.current),
        viewport_width: window.innerWidth,
        viewport_height: window.innerHeight,
        geometry: null,
      },
      duration_ms: Math.max(0, Math.round(endedAt - startedAt)),
      background_interrupted: false,
    })
  }

  function handleTilePointerDown(event: PointerEvent<HTMLButtonElement>, tile: number) {
    if (!event.isPrimary || (event.pointerType === "mouse" && event.button !== 0)) return
    event.preventDefault()
    submitTile(tile, pointerInputKind(event.pointerType))
  }

  function handleTileKeyDown(event: KeyboardEvent<HTMLButtonElement>, tile: number) {
    if (event.repeat || (event.key !== "Enter" && event.key !== " ")) return
    event.preventDefault()
    submitTile(tile, "unknown")
  }

  if (state.phase === "intro") {
    return (
      <section aria-label="顺序记忆测试区域" className="sequence-game" data-testid="sequence-game">
        <div className="sequence-game__panel">
          <h2>顺序记忆</h2>
          <p>记住方格亮起的顺序，然后依次点击。</p>
          <button className="sequence-game__action" onClick={start} type="button">
            开始测试
          </button>
        </div>
      </section>
    )
  }

  if (state.phase === "result") {
    return (
      <section aria-label="顺序记忆测试结果" className="sequence-game" data-testid="sequence-game">
        <div aria-live="polite" className="sequence-game__panel">
          <h2>到达第 {state.level} 关</h2>
          <p>完整通过 {state.level - 1} 关</p>
          {state.failedStep !== null ? <p>第 {state.failedStep} 步点击错误</p> : null}
          <button className="sequence-game__action" onClick={retry} type="button">
            重新开始
          </button>
        </div>
      </section>
    )
  }

  const isResponding = state.phase === "responding"
  return (
    <section aria-label="顺序记忆测试区域" className="sequence-game" data-testid="sequence-game">
      <div className="sequence-game__status" role="status">
        <span>关卡：{state.level}</span>
        <span>{isResponding ? "按顺序点击方格" : "请观察"}</span>
      </div>
      <div aria-label="顺序记忆九宫格" className="sequence-game__board" role="group">
        {BOARD_TILES.map((tile) => (
          <button
            aria-label={`第 ${tile + 1} 格`}
            className={`sequence-game__tile${activeTile === tile ? " sequence-game__tile--active" : ""}`}
            disabled={!isResponding}
            key={tile}
            onKeyDown={(event) => handleTileKeyDown(event, tile)}
            onPointerDown={(event) => handleTilePointerDown(event, tile)}
            type="button"
          >
            <span aria-hidden="true" />
          </button>
        ))}
      </div>
    </section>
  )
}
