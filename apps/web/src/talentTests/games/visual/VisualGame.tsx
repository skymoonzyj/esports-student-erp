import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from "react"

import type { GameProps, Geometry, InputKind } from "../../contracts"
import {
  advanceAfterSuccess,
  beginAnswering,
  createRound,
  selectTile,
  startVisualMemory,
  type VisualMemoryRound,
} from "./engine"
import "./VisualGame.css"

const REVEAL_DELAY_MS = 500
const REVEAL_DURATION_MS = 1000
const FLIP_DURATION_MS = 300
const FEEDBACK_DURATION_MS = 450

function normalizePointerType(pointerType: string): InputKind {
  if (pointerType === "mouse" || pointerType === "touch" || pointerType === "pen") {
    return pointerType
  }
  return "unknown"
}

function inputKindFor(inputKinds: ReadonlySet<InputKind>): InputKind {
  if (inputKinds.size === 0) return "unknown"
  if (inputKinds.size > 1) return "mixed"
  return inputKinds.values().next().value ?? "unknown"
}

function sameBoardGeometry(left: Geometry, right: Geometry): boolean {
  return left.width === right.width && left.height === right.height
}

export function VisualGame({
  onComplete,
  onInterrupted,
}: GameProps<"memory">) {
  const [round, setRound] = useState<VisualMemoryRound | null>(null)
  const [showTargets, setShowTargets] = useState(false)
  const roundRef = useRef<VisualMemoryRound | null>(null)
  const boardRef = useRef<HTMLDivElement>(null)
  const firstTileRef = useRef<HTMLButtonElement>(null)
  const timersRef = useRef<Set<number>>(new Set())
  const activeRef = useRef(false)
  const completedRef = useRef(false)
  const startedAtRef = useRef<number | null>(null)
  const acceptedMissesRef = useRef(0)
  const inputKindsRef = useRef<Set<InputKind>>(new Set())
  const activeGeometryRef = useRef<Geometry | null>(null)
  const onCompleteRef = useRef(onComplete)
  const onInterruptedRef = useRef(onInterrupted)

  useEffect(() => {
    onCompleteRef.current = onComplete
  }, [onComplete])

  useEffect(() => {
    onInterruptedRef.current = onInterrupted
  }, [onInterrupted])

  const setCurrentRound = useCallback((next: VisualMemoryRound | null) => {
    roundRef.current = next
    setRound(next)
  }, [])

  const clearTimers = useCallback(() => {
    for (const timer of timersRef.current) window.clearTimeout(timer)
    timersRef.current.clear()
  }, [])

  const queue = useCallback((callback: () => void, delay: number) => {
    const timer = window.setTimeout(() => {
      timersRef.current.delete(timer)
      callback()
    }, delay)
    timersRef.current.add(timer)
  }, [])

  const reset = useCallback(() => {
    clearTimers()
    activeRef.current = false
    completedRef.current = false
    startedAtRef.current = null
    acceptedMissesRef.current = 0
    inputKindsRef.current = new Set()
    activeGeometryRef.current = null
    setShowTargets(false)
    setCurrentRound(null)
  }, [clearTimers, setCurrentRound])

  const interrupt = useCallback(
    (reason: "hidden" | "geometry_changed") => {
      if (!activeRef.current || completedRef.current) return
      reset()
      onInterruptedRef.current(reason)
    },
    [reset],
  )

  const prepareRound = useCallback(
    (nextRound: VisualMemoryRound) => {
      clearTimers()
      setShowTargets(false)
      setCurrentRound(nextRound)
      queue(() => setShowTargets(true), REVEAL_DELAY_MS)
      queue(
        () => setShowTargets(false),
        REVEAL_DELAY_MS + REVEAL_DURATION_MS,
      )
      queue(() => {
        const current = roundRef.current
        if (current === nextRound) setCurrentRound(beginAnswering(current))
      }, REVEAL_DELAY_MS + REVEAL_DURATION_MS + FLIP_DURATION_MS)
    },
    [clearTimers, queue, setCurrentRound],
  )

  useEffect(() => {
    function handleVisibilityChange() {
      if (document.hidden) interrupt("hidden")
    }

    document.addEventListener("visibilitychange", handleVisibilityChange)
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange)
      clearTimers()
    }
  }, [clearTimers, interrupt])

  const hasBoard = round !== null && round.phase !== "result"
  useEffect(() => {
    if (!hasBoard) return
    const board = boardRef.current
    const firstTile = firstTileRef.current
    if (!board || !firstTile) return

    const applyMeasurement = (width: number, height: number) => {
      const tileWidth = firstTile.getBoundingClientRect().width
      const nextGeometry: Geometry = {
        width,
        height,
        target_diameter: tileWidth > 0 ? tileWidth : null,
      }
      const activeGeometry = activeGeometryRef.current
      if (activeGeometry === null) {
        activeGeometryRef.current = nextGeometry
      } else if (!sameBoardGeometry(activeGeometry, nextGeometry)) {
        interrupt("geometry_changed")
      }
    }

    const initialRect = board.getBoundingClientRect()
    applyMeasurement(initialRect.width, initialRect.height)
    const observer = new ResizeObserver((entries) => {
      const entry = entries.find((candidate) => candidate.target === board)
      if (entry) applyMeasurement(entry.contentRect.width, entry.contentRect.height)
    })
    observer.observe(board)
    return () => observer.disconnect()
  }, [hasBoard, interrupt])

  function start() {
    clearTimers()
    activeRef.current = true
    completedRef.current = false
    startedAtRef.current = performance.now()
    acceptedMissesRef.current = 0
    inputKindsRef.current = new Set()
    activeGeometryRef.current = null
    prepareRound(startVisualMemory())
  }

  function chooseTile(index: number, inputKind: InputKind) {
    const before = roundRef.current
    if (!before || before.phase !== "answering" || completedRef.current) return

    inputKindsRef.current.add(inputKind)
    const next = selectTile(before, index)
    if (next === before) return
    if (next.misses.length > before.misses.length) acceptedMissesRef.current += 1
    setCurrentRound(next)

    if (next.phase === "level-complete") {
      queue(
        () => prepareRound(advanceAfterSuccess(next)),
        FEEDBACK_DURATION_MS,
      )
      return
    }

    if (next.phase === "lost-life") {
      queue(
        () => prepareRound(createRound(next.level, next.lives)),
        FEEDBACK_DURATION_MS,
      )
      return
    }

    if (next.phase !== "result") return
    completedRef.current = true
    activeRef.current = false
    clearTimers()
    const endedAt = performance.now()
    const startedAt = startedAtRef.current ?? endedAt
    onCompleteRef.current({
      test_id: "memory",
      raw: {
        reached_level: next.level,
        completed_level: next.level - 1,
        accepted_misses: acceptedMissesRef.current,
        lives_used: 3,
        end_reason: "lives",
      },
      device: {
        input_kind: inputKindFor(inputKindsRef.current),
        viewport_width: window.innerWidth,
        viewport_height: window.innerHeight,
        geometry: activeGeometryRef.current,
      },
      duration_ms: Math.max(0, Math.round(endedAt - startedAt)),
      background_interrupted: false,
    })
  }

  function handlePointerDown(event: PointerEvent<HTMLButtonElement>, index: number) {
    if (!event.isPrimary || (event.pointerType === "mouse" && event.button !== 0)) return
    event.preventDefault()
    chooseTile(index, normalizePointerType(event.pointerType))
  }

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.repeat || (event.key !== "Enter" && event.key !== " ")) return
    event.preventDefault()
    chooseTile(index, "unknown")
  }

  if (round === null) {
    return (
      <section aria-label="视觉记忆测试区域" className="visual-game">
        <div className="visual-game__panel">
          <h2>视觉记忆</h2>
          <p>记住亮起的方格，然后找出全部目标。</p>
          <p>每轮三个不同误点会扣除一条生命。</p>
          <p className="visual-game__pilot">评分锚点为内部试行训练目标。</p>
          <button className="visual-game__action" onClick={start} type="button">
            开始视觉记忆
          </button>
        </div>
      </section>
    )
  }

  if (round.phase === "result") {
    return (
      <section aria-label="视觉记忆测试结果" className="visual-game">
        <div aria-live="polite" className="visual-game__panel">
          <h2>到达第 {round.level} 关</h2>
          <p>完整通过 {round.level - 1} 关</p>
          <p className="visual-game__pilot">评分锚点为内部试行训练目标。</p>
        </div>
      </section>
    )
  }

  const cells = Array.from({ length: round.gridSize ** 2 }, (_, index) => index)
  const rows = Array.from({ length: round.gridSize }, (_, rowIndex) =>
    cells.slice(rowIndex * round.gridSize, (rowIndex + 1) * round.gridSize),
  )
  const instruction =
    round.phase === "answering"
      ? "请选择刚才亮起的方格"
      : round.phase === "level-complete"
        ? "本关完成"
        : round.phase === "lost-life"
          ? "扣除一条生命"
          : "请观察亮起的方格"

  return (
    <section
      aria-label="视觉记忆测试区域"
      className={`visual-game${round.phase === "level-complete" ? " visual-game--success" : ""}${round.phase === "lost-life" ? " visual-game--failure" : ""}`}
    >
      <div className="visual-game__status" role="status">
        <span>关卡：{round.level}</span>
        <span aria-label={`剩余 ${round.lives} 条生命`} className="visual-game__hearts">
          {[0, 1, 2].map((heart) => (
            <span
              aria-hidden="true"
              className={heart < round.lives ? "" : "visual-game__heart--lost"}
              key={heart}
            >
              ♥
            </span>
          ))}
        </span>
      </div>
      <p className="visual-game__instruction">{instruction}</p>
      <div
        aria-label={`${round.gridSize}乘${round.gridSize}视觉记忆方格棋盘`}
        className="visual-game__board"
        ref={boardRef}
        role="group"
      >
        {rows.map((row, rowIndex) => (
          <div className="visual-game__row" key={rowIndex}>
            {row.map((index) => {
              const active =
                round.selected.includes(index) ||
                (showTargets && round.targets.includes(index))
              const wrong = round.misses.includes(index)
              return (
                <button
                  aria-label={`第 ${index + 1} 格`}
                  className={`visual-game__tile${active ? " visual-game__tile--active" : ""}${wrong ? " visual-game__tile--wrong" : ""}`}
                  data-testid="visual-memory-tile"
                  disabled={round.phase !== "answering" || active || wrong}
                  key={index}
                  onKeyDown={(event) => handleKeyDown(event, index)}
                  onPointerDown={(event) => handlePointerDown(event, index)}
                  ref={index === 0 ? firstTileRef : undefined}
                  type="button"
                >
                  {active ? (
                    <span aria-hidden="true" data-testid="visual-memory-tile-active" />
                  ) : null}
                </button>
              )
            })}
          </div>
        ))}
      </div>
    </section>
  )
}
