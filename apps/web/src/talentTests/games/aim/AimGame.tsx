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
  beginAimTest,
  createAimState,
  randomTargetPosition,
  recordAimHit,
  recordAimMiss,
  targetDiameterForWidth,
  type AimState,
  type TargetPosition,
} from "./engine"
import "./AimGame.css"

interface ArenaGeometry {
  width: number
  height: number
  targetDiameter: number
}

function geometryFor(width: number, height: number): ArenaGeometry {
  const safeWidth = Math.max(1, width)
  const safeHeight = Math.max(1, height)
  return {
    width: safeWidth,
    height: safeHeight,
    targetDiameter: targetDiameterForWidth(safeWidth),
  }
}

function sameGeometry(left: ArenaGeometry, right: ArenaGeometry): boolean {
  return (
    left.width === right.width &&
    left.height === right.height &&
    left.targetDiameter === right.targetDiameter
  )
}

function inputKindFor(inputs: Set<InputKind>): InputKind {
  if (inputs.size === 0) return "unknown"
  if (inputs.size > 1) return "mixed"
  return inputs.values().next().value ?? "unknown"
}

function normalizePointerType(pointerType: string): InputKind {
  if (pointerType === "mouse" || pointerType === "touch" || pointerType === "pen") {
    return pointerType
  }
  return "unknown"
}

export function AimGame({ onComplete, onInterrupted }: GameProps<"aim">) {
  const arenaRef = useRef<HTMLDivElement>(null)
  const gameRef = useRef<AimState>(createAimState())
  const activeGeometryRef = useRef<ArenaGeometry | null>(null)
  const runStartedAtRef = useRef<number | null>(null)
  const targetLockedRef = useRef(false)
  const targetSerialRef = useRef(0)
  const missCountRef = useRef(0)
  const inputKindsRef = useRef<Set<InputKind>>(new Set())
  const onInterruptedRef = useRef(onInterrupted)
  const [game, setGame] = useState(gameRef.current)
  const [geometry, setGeometry] = useState(() => geometryFor(1, 1))
  const [targetPosition, setTargetPosition] = useState<TargetPosition>({ x: 0.5, y: 0.5 })
  const [targetSerial, setTargetSerial] = useState(0)

  useEffect(() => {
    onInterruptedRef.current = onInterrupted
  }, [onInterrupted])

  const setCurrentGame = useCallback((next: AimState) => {
    gameRef.current = next
    setGame(next)
  }, [])

  const resetRun = useCallback(() => {
    const reset = createAimState()
    setCurrentGame(reset)
    activeGeometryRef.current = null
    runStartedAtRef.current = null
    targetLockedRef.current = false
    targetSerialRef.current = 0
    missCountRef.current = 0
    inputKindsRef.current = new Set()
    setTargetSerial(0)
  }, [setCurrentGame])

  const interruptRun = useCallback(
    (reason: "hidden" | "geometry_changed") => {
      if (gameRef.current.phase !== "playing") return
      resetRun()
      onInterruptedRef.current(reason)
    },
    [resetRun],
  )

  useEffect(() => {
    const arena = arenaRef.current
    if (!arena) return

    const applyMeasurement = (width: number, height: number) => {
      const nextGeometry = geometryFor(width, height)
      const activeGeometry = activeGeometryRef.current
      if (gameRef.current.phase === "playing" && activeGeometry !== null) {
        if (!sameGeometry(activeGeometry, nextGeometry)) {
          interruptRun("geometry_changed")
          setGeometry(nextGeometry)
          setTargetPosition({ x: nextGeometry.width / 2, y: nextGeometry.height / 2 })
        }
        return
      }
      setGeometry(nextGeometry)
      setTargetPosition({ x: nextGeometry.width / 2, y: nextGeometry.height / 2 })
    }

    const initialRect = arena.getBoundingClientRect()
    applyMeasurement(initialRect.width, initialRect.height)

    const observer = new ResizeObserver((entries) => {
      const entry = entries.find((candidate) => candidate.target === arena)
      if (entry) applyMeasurement(entry.contentRect.width, entry.contentRect.height)
    })
    observer.observe(arena)
    return () => observer.disconnect()
  }, [interruptRun])

  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.visibilityState === "hidden") interruptRun("hidden")
    }
    document.addEventListener("visibilitychange", handleVisibilityChange)
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange)
  }, [interruptRun])

  const sampleInput = useCallback((pointerType: string) => {
    inputKindsRef.current.add(normalizePointerType(pointerType))
  }, [])

  const placeNewTarget = useCallback((arena: ArenaGeometry) => {
    setTargetPosition(
      randomTargetPosition(arena.width, arena.height, arena.targetDiameter),
    )
    const nextSerial = targetSerialRef.current + 1
    targetSerialRef.current = nextSerial
    setTargetSerial(nextSerial)
    targetLockedRef.current = false
  }, [])

  const start = useCallback(
    (pointerType?: string) => {
      if (gameRef.current.phase !== "intro") return
      if (pointerType !== undefined) sampleInput(pointerType)
      const now = performance.now()
      activeGeometryRef.current = geometry
      runStartedAtRef.current = now
      missCountRef.current = 0
      const next = beginAimTest(gameRef.current, now)
      setCurrentGame(next)
      placeNewTarget(geometry)
    },
    [geometry, placeNewTarget, sampleInput, setCurrentGame],
  )

  const hitTarget = useCallback(
    (pointerType: string | undefined, expectedSerial: number) => {
      if (
        targetLockedRef.current ||
        expectedSerial !== targetSerialRef.current ||
        gameRef.current.phase !== "playing"
      ) {
        return
      }
      targetLockedRef.current = true
      if (pointerType !== undefined) sampleInput(pointerType)

      const now = performance.now()
      const next = recordAimHit(gameRef.current, now)
      setCurrentGame(next)
      if (next.phase === "playing") {
        const activeGeometry = activeGeometryRef.current
        if (activeGeometry) placeNewTarget(activeGeometry)
        return
      }

      const activeGeometry = activeGeometryRef.current
      const runStartedAt = runStartedAtRef.current
      if (!activeGeometry || runStartedAt === null) return
      const durationMs = Math.max(next.totalElapsedMs, now - runStartedAt)
      onComplete({
        test_id: "aim",
        raw: {
          intervals_ms: next.intervalsMs,
          miss_count: missCountRef.current,
          total_elapsed_ms: next.totalElapsedMs,
        },
        device: {
          input_kind: inputKindFor(inputKindsRef.current),
          viewport_width: window.innerWidth,
          viewport_height: window.innerHeight,
          geometry: {
            width: activeGeometry.width,
            height: activeGeometry.height,
            target_diameter: activeGeometry.targetDiameter,
          },
        },
        duration_ms: durationMs,
        background_interrupted: false,
      })
    },
    [onComplete, placeNewTarget, sampleInput, setCurrentGame],
  )

  function handleStartPointerDown(event: PointerEvent<HTMLButtonElement>) {
    if (event.button !== 0 || !event.isPrimary) return
    event.preventDefault()
    event.stopPropagation()
    start(event.pointerType)
  }

  function handleStartKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.repeat || (event.key !== "Enter" && event.key !== " ")) return
    event.preventDefault()
    event.stopPropagation()
    start()
  }

  function handleTargetPointerDown(event: PointerEvent<HTMLButtonElement>) {
    if (event.button !== 0 || !event.isPrimary) return
    event.preventDefault()
    event.stopPropagation()
    hitTarget(event.pointerType, targetSerial)
  }

  function handleTargetKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.repeat || (event.key !== "Enter" && event.key !== " ")) return
    event.preventDefault()
    event.stopPropagation()
    hitTarget(undefined, targetSerial)
  }

  function handleSurfacePointerDown(event: PointerEvent<HTMLDivElement>) {
    if (
      gameRef.current.phase !== "playing" ||
      event.button !== 0 ||
      !event.isPrimary ||
      event.target !== event.currentTarget
    ) {
      return
    }
    sampleInput(event.pointerType)
    missCountRef.current += 1
    setCurrentGame(recordAimMiss(gameRef.current))
  }

  return (
    <div aria-label="瞄准训练区域" className="aim-game" ref={arenaRef}>
      {game.phase === "intro" && (
        <div className="aim-game__intro">
          <h1>瞄准训练</h1>
          <button
            aria-label="点击目标开始"
            className="aim-game__target aim-game__target--intro"
            onKeyDown={handleStartKeyDown}
            onPointerDown={handleStartPointerDown}
            type="button"
          >
            <span aria-hidden="true" className="aim-game__bullseye" />
          </button>
          <p>尽快击中 30 个目标。</p>
          <p>点击目标开始。</p>
        </div>
      )}

      {game.phase === "playing" && (
        <>
          <p aria-live="polite" className="aim-game__counter">
            剩余 <strong>{game.remaining}</strong>
          </p>
          <div
            aria-label="瞄准训练游戏区域"
            className="aim-game__surface"
            onPointerDown={handleSurfacePointerDown}
            role="application"
          >
            <button
              aria-label={`击中目标，剩余 ${game.remaining} 个`}
              className="aim-game__target aim-game__target--playing"
              key={targetSerial}
              onKeyDown={handleTargetKeyDown}
              onPointerDown={handleTargetPointerDown}
              style={{
                height: `${geometry.targetDiameter}px`,
                left: `${targetPosition.x}px`,
                top: `${targetPosition.y}px`,
                width: `${geometry.targetDiameter}px`,
              }}
              type="button"
            >
              <span aria-hidden="true" className="aim-game__bullseye" />
            </button>
          </div>
        </>
      )}

      {game.phase === "result" && (
        <p aria-live="polite" className="aim-game__saving">
          正在保存结果…
        </p>
      )}
    </div>
  )
}
