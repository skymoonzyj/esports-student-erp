import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent,
} from "react"

import type { GameProps, InputKind } from "../../contracts"
import {
  CHIMP_BOARD_SIZE,
  continueChimp,
  createInitialChimpState,
  selectChimpCell,
  startChimp,
  type ChimpState,
} from "./engine"
import "./ChimpGame.css"

type BoardGeometry = {
  width: number
  height: number
  targetDiameter: number
}

const BOARD_SLOTS = Array.from(
  { length: CHIMP_BOARD_SIZE },
  (_, position) => position,
)

function positionLabel(position: number) {
  const row = Math.floor(position / 8) + 1
  const column = (position % 8) + 1
  return `方块 第${row}行第${column}列`
}

function pointerInputKind(pointerType: string): InputKind {
  if (pointerType === "mouse" || pointerType === "touch" || pointerType === "pen") {
    return pointerType
  }
  return "unknown"
}

function observedInputKind(kinds: ReadonlySet<InputKind>): InputKind {
  if (kinds.size === 0) return "unknown"
  if (kinds.size === 1) return kinds.values().next().value ?? "unknown"
  return "mixed"
}

function geometryFor(width: number, height: number): BoardGeometry {
  const safeWidth = Math.max(1, width)
  const safeHeight = Math.max(1, height)
  const cellInset = window.innerWidth >= 700 ? 10 : 4
  return {
    width: safeWidth,
    height: safeHeight,
    targetDiameter: Math.max(1, safeWidth / 8 - cellInset),
  }
}

function sameGeometry(left: BoardGeometry, right: BoardGeometry) {
  return (
    left.width === right.width &&
    left.height === right.height &&
    left.targetDiameter === right.targetDiameter
  )
}

function isActive(state: ChimpState) {
  return state.phase === "playing" || state.phase === "interstitial"
}

export function ChimpGame({ onComplete, onInterrupted }: GameProps<"chimp">) {
  const initial = createInitialChimpState()
  const stateRef = useRef(initial)
  const boardRef = useRef<HTMLDivElement>(null)
  const activeGeometryRef = useRef<BoardGeometry | null>(null)
  const startedAtRef = useRef<number | null>(null)
  const inputKindsRef = useRef<Set<InputKind>>(new Set())
  const terminalRef = useRef(false)
  const onInterruptedRef = useRef(onInterrupted)
  const [state, setState] = useState(initial)

  useEffect(() => {
    onInterruptedRef.current = onInterrupted
  }, [onInterrupted])

  const setCurrentState = useCallback((next: ChimpState) => {
    stateRef.current = next
    setState(next)
  }, [])

  const interruptRun = useCallback(
    (reason: "hidden" | "geometry_changed") => {
      if (!isActive(stateRef.current) || terminalRef.current) return
      terminalRef.current = true
      activeGeometryRef.current = null
      startedAtRef.current = null
      inputKindsRef.current = new Set()
      setCurrentState(createInitialChimpState())
      onInterruptedRef.current(reason)
      terminalRef.current = false
    },
    [setCurrentState],
  )

  useEffect(() => {
    if (state.phase !== "playing") return
    const board = boardRef.current
    if (!board) return

    const applyMeasurement = (width: number, height: number) => {
      const geometry = geometryFor(width, height)
      const activeGeometry = activeGeometryRef.current
      if (activeGeometry === null) {
        activeGeometryRef.current = geometry
      } else if (isActive(stateRef.current) && !sameGeometry(activeGeometry, geometry)) {
        interruptRun("geometry_changed")
      }
    }

    const initialRect = board.getBoundingClientRect()
    applyMeasurement(initialRect.width, initialRect.height)
    const observer = new ResizeObserver((entries) => {
      const entry = entries.find((candidate) => candidate.target === board)
      if (entry) applyMeasurement(entry.contentRect.width, entry.contentRect.height)
    })
    const handleWindowResize = () => {
      const rect = board.getBoundingClientRect()
      applyMeasurement(rect.width, rect.height)
    }
    observer.observe(board)
    window.addEventListener("resize", handleWindowResize)
    return () => {
      observer.disconnect()
      window.removeEventListener("resize", handleWindowResize)
    }
  }, [interruptRun, state.phase])

  useEffect(() => {
    function handleVisibilityChange() {
      if (document.hidden) interruptRun("hidden")
    }

    document.addEventListener("visibilitychange", handleVisibilityChange)
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange)
  }, [interruptRun])

  function start() {
    if (stateRef.current.phase !== "intro") return
    terminalRef.current = false
    activeGeometryRef.current = null
    startedAtRef.current = performance.now()
    inputKindsRef.current = new Set(inputKindsRef.current)
    setCurrentState(startChimp(stateRef.current))
  }

  function continueRound() {
    setCurrentState(continueChimp(stateRef.current))
  }

  function complete(next: ChimpState) {
    if (terminalRef.current) return
    const geometry = activeGeometryRef.current
    if (!geometry) return

    terminalRef.current = true
    const endedAt = performance.now()
    const startedAt = startedAtRef.current ?? endedAt
    const reachedEngineCap = next.numberCount === CHIMP_BOARD_SIZE && next.strikes < 3
    onComplete({
      test_id: "chimp",
      raw: {
        reached_count: next.numberCount,
        completed_count: reachedEngineCap
          ? CHIMP_BOARD_SIZE
          : next.numberCount === 4
            ? 0
            : next.numberCount - 1,
        strikes: next.strikes,
        end_reason: reachedEngineCap ? "engine_cap" : "strikes",
      },
      device: {
        input_kind: observedInputKind(inputKindsRef.current),
        viewport_width: window.innerWidth,
        viewport_height: window.innerHeight,
        geometry: {
          width: geometry.width,
          height: geometry.height,
          target_diameter: geometry.targetDiameter,
        },
      },
      duration_ms: Math.max(0, Math.round(endedAt - startedAt)),
      background_interrupted: false,
    })
  }

  function pressCell(position: number) {
    const next = selectChimpCell(stateRef.current, position)
    if (next === stateRef.current) return
    setCurrentState(next)
    if (next.phase === "result") complete(next)
  }

  function samplePointer(event: PointerEvent<HTMLElement>) {
    if (!event.isPrimary || (event.pointerType === "mouse" && event.button !== 0)) return
    inputKindsRef.current.add(pointerInputKind(event.pointerType))
  }

  return (
    <section
      aria-label="黑猩猩测试区域"
      className={`chimp-game chimp-game--${state.phase}`}
      onPointerDownCapture={samplePointer}
    >
      {state.phase === "intro" ? (
        <div className="chimp-game__panel">
          <h2>黑猩猩测试</h2>
          <p>请按数字顺序点击方块，难度会逐级提高。</p>
          <button type="button" onClick={start}>
            开始黑猩猩测试
          </button>
        </div>
      ) : null}

      {state.phase === "interstitial" ? (
        <div className="chimp-game__panel" aria-live="polite">
          <h2>数字 {state.numberCount}</h2>
          <p>失误 {state.strikes} / 3</p>
          <button type="button" onClick={continueRound}>
            继续
          </button>
        </div>
      ) : null}

      {state.phase === "playing" ? (
        <>
          <p className="chimp-game__status" role="status" aria-live="polite">
            数字 {state.numberCount}，失误 {state.strikes} / 3
          </p>
          <div
            aria-label="八列五行数字方格"
            className="chimp-game__board"
            ref={boardRef}
            role="group"
          >
            {BOARD_SLOTS.map((position) => {
              const number = state.board[position]
              return (
                <div className="chimp-game__slot" key={position}>
                  {number === null ? null : (
                    <button
                      aria-label={
                        state.masked ? positionLabel(position) : `数字 ${number}`
                      }
                      className={`chimp-game__cell${state.masked ? " chimp-game__cell--masked" : ""}`}
                      onClick={() => pressCell(position)}
                      type="button"
                    >
                      {state.masked ? null : <span aria-hidden="true">{number}</span>}
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        </>
      ) : null}

      {state.phase === "result" ? (
        <div className="chimp-game__panel" role="status">
          <h2>到达 {state.numberCount} 个数字</h2>
          <p>
            完整通过 {state.numberCount === CHIMP_BOARD_SIZE && state.strikes < 3
              ? CHIMP_BOARD_SIZE
              : state.numberCount === 4
                ? 0
                : state.numberCount - 1} 个数字
          </p>
          <p>结果正在保存…</p>
        </div>
      ) : null}
    </section>
  )
}
