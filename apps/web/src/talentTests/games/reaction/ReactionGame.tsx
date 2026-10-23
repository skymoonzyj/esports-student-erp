import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react"

import type { GameProps, InputKind } from "../../contracts"
import {
  appendReactionSample,
  averageReactionTime,
  measureReactionTime,
  REACTION_TRIAL_COUNT,
} from "./engine"
import "./ReactionGame.css"

type GamePhase = "intro" | "waiting" | "go" | "too-soon" | "result" | "final" | "interrupted"

function observedInputKind(kinds: ReadonlySet<InputKind>): InputKind {
  if (kinds.size === 0) {
    return "unknown"
  }
  if (kinds.size === 1) {
    return kinds.values().next().value ?? "unknown"
  }
  return "mixed"
}

function pointerInputKind(pointerType: string): InputKind {
  if (pointerType === "mouse" || pointerType === "touch" || pointerType === "pen") {
    return pointerType
  }
  return "unknown"
}

export function ReactionGame({ onComplete, onInterrupted }: GameProps<"reactiontime">) {
  const [phase, setPhase] = useState<GamePhase>("intro")
  const [sampleCount, setSampleCount] = useState(0)
  const [lastResult, setLastResult] = useState<number | null>(null)
  const phaseRef = useRef<GamePhase>("intro")
  const samplesRef = useRef<number[]>([])
  const tooSoonCountRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const frameRef = useRef<number | null>(null)
  const greenStartedAtRef = useRef<number | null>(null)
  const gameStartedAtRef = useRef<number | null>(null)
  const inputKindsRef = useRef<Set<InputKind>>(new Set())
  const waitingAcceptsEarlyPressRef = useRef(false)
  const terminalRef = useRef(false)
  const onInterruptedRef = useRef(onInterrupted)
  onInterruptedRef.current = onInterrupted

  const moveToPhase = useCallback((nextPhase: GamePhase) => {
    phaseRef.current = nextPhase
    setPhase(nextPhase)
  }, [])

  const clearPendingStart = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
    if (frameRef.current !== null) {
      cancelAnimationFrame(frameRef.current)
      frameRef.current = null
    }
  }, [])

  const startWaiting = useCallback(() => {
    if (terminalRef.current) {
      return
    }

    clearPendingStart()
    greenStartedAtRef.current = null
    waitingAcceptsEarlyPressRef.current = false
    moveToPhase("waiting")

    const delay = 2_500 + Math.floor(Math.random() * 2_500)
    timerRef.current = setTimeout(() => {
      timerRef.current = null
      if (terminalRef.current || phaseRef.current !== "waiting" || document.hidden) {
        return
      }

      frameRef.current = requestAnimationFrame(() => {
        frameRef.current = null
        if (terminalRef.current || phaseRef.current !== "waiting" || document.hidden) {
          return
        }
        greenStartedAtRef.current = performance.now()
        moveToPhase("go")
      })
    }, delay)
  }, [clearPendingStart, moveToPhase])

  const handlePlayAction = useCallback(() => {
    if (terminalRef.current) {
      return
    }

    switch (phaseRef.current) {
      case "intro":
        gameStartedAtRef.current = performance.now()
        startWaiting()
        return
      case "too-soon":
      case "result":
        startWaiting()
        return
      case "waiting":
        if (!waitingAcceptsEarlyPressRef.current) {
          return
        }
        clearPendingStart()
        greenStartedAtRef.current = null
        waitingAcceptsEarlyPressRef.current = false
        tooSoonCountRef.current += 1
        moveToPhase("too-soon")
        return
      case "go": {
        const startedAt = greenStartedAtRef.current
        greenStartedAtRef.current = null
        if (startedAt === null) {
          startWaiting()
          return
        }

        const endedAt = performance.now()
        const elapsed = measureReactionTime(startedAt, endedAt)
        if (elapsed === null) {
          startWaiting()
          return
        }

        const nextSamples = appendReactionSample(samplesRef.current, elapsed)
        samplesRef.current = nextSamples
        setSampleCount(nextSamples.length)
        setLastResult(elapsed)

        if (nextSamples.length !== REACTION_TRIAL_COUNT) {
          moveToPhase("result")
          return
        }

        terminalRef.current = true
        clearPendingStart()
        moveToPhase("final")
        const gameStartedAt = gameStartedAtRef.current ?? endedAt
        onComplete({
          test_id: "reactiontime",
          raw: {
            samples_ms: [...nextSamples],
            too_soon_count: tooSoonCountRef.current,
          },
          device: {
            input_kind: observedInputKind(inputKindsRef.current),
            viewport_width: window.innerWidth,
            viewport_height: window.innerHeight,
            geometry: null,
          },
          duration_ms: Math.max(0, Math.round(endedAt - gameStartedAt)),
          background_interrupted: false,
        })
        return
      }
      case "final":
      case "interrupted":
        return
    }
  }, [clearPendingStart, moveToPhase, onComplete, startWaiting])

  useEffect(() => {
    function handleWindowPointerRelease(event: PointerEvent) {
      if (event.isPrimary && phaseRef.current === "waiting") {
        waitingAcceptsEarlyPressRef.current = true
      }
    }

    function handleVisibilityChange() {
      if (!document.hidden || terminalRef.current) {
        return
      }

      terminalRef.current = true
      clearPendingStart()
      greenStartedAtRef.current = null
      moveToPhase("interrupted")
      onInterruptedRef.current("hidden")
    }

    document.addEventListener("visibilitychange", handleVisibilityChange)
    window.addEventListener("pointerup", handleWindowPointerRelease)
    window.addEventListener("pointercancel", handleWindowPointerRelease)
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange)
      window.removeEventListener("pointerup", handleWindowPointerRelease)
      window.removeEventListener("pointercancel", handleWindowPointerRelease)
      clearPendingStart()
      greenStartedAtRef.current = null
    }
  }, [clearPendingStart, moveToPhase])

  function handlePointerDown(event: ReactPointerEvent<HTMLElement>) {
    if (!event.isPrimary || (event.pointerType === "mouse" && event.button !== 0)) {
      return
    }
    if (event.target instanceof Element && event.target.closest("button, a, input, textarea, select")) {
      return
    }
    event.preventDefault()
    inputKindsRef.current.add(pointerInputKind(event.pointerType))
    handlePlayAction()
  }

  function handlePointerRelease(event: ReactPointerEvent<HTMLElement>) {
    if (event.isPrimary && phaseRef.current === "waiting") {
      waitingAcceptsEarlyPressRef.current = true
    }
  }

  function handleKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.repeat || (event.key !== " " && event.key !== "Enter")) {
      return
    }
    if (event.target instanceof Element && event.target.closest("button, a, input, textarea, select")) {
      return
    }
    event.preventDefault()
    inputKindsRef.current.add("unknown")
    handlePlayAction()
  }

  function handleKeyUp(event: KeyboardEvent<HTMLElement>) {
    if ((event.key === " " || event.key === "Enter") && phaseRef.current === "waiting") {
      waitingAcceptsEarlyPressRef.current = true
    }
  }

  const activeAttempt = Math.min(sampleCount + 1, REACTION_TRIAL_COUNT)
  const average = averageReactionTime(samplesRef.current)
  const isInteractive = phase !== "final" && phase !== "interrupted"

  return (
    <section
      aria-label="反应时间测试区域"
      aria-keyshortcuts={isInteractive ? "Enter Space" : undefined}
      className={`reaction-game reaction-game--${phase}`}
      data-testid="reaction-game"
      onKeyDown={handleKeyDown}
      onKeyUp={handleKeyUp}
      onPointerCancel={handlePointerRelease}
      onPointerDown={handlePointerDown}
      onPointerUp={handlePointerRelease}
      role={isInteractive ? "button" : "status"}
      tabIndex={isInteractive ? 0 : -1}
    >
      <div className="reaction-game__content" aria-live="polite" aria-atomic="true">
        {phase === "intro" ? (
          <>
            <h2 className="reaction-game__title">反应时间</h2>
            <p className="reaction-game__message">红色区域变绿时，请尽快点击。点击任意位置开始。</p>
          </>
        ) : null}
        {phase === "waiting" || phase === "go" ? (
          <>
            <h2 className="reaction-game__title">{phase === "waiting" ? "等待变绿" : "点击！"}</h2>
            <p className="reaction-game__progress">第 {activeAttempt} / {REACTION_TRIAL_COUNT} 次</p>
          </>
        ) : null}
        {phase === "too-soon" ? (
          <>
            <h2 className="reaction-game__title">太早了！</h2>
            <p className="reaction-game__message">点击重试。</p>
          </>
        ) : null}
        {phase === "result" && lastResult !== null ? (
          <>
            <h2 className="reaction-game__title">{lastResult} 毫秒</h2>
            <p className="reaction-game__message">点击继续</p>
            <p className="reaction-game__progress">第 {sampleCount} / {REACTION_TRIAL_COUNT} 次</p>
          </>
        ) : null}
        {phase === "final" ? (
          <>
            <h2 className="reaction-game__title">测试完成</h2>
            {average !== null ? <p className="reaction-game__message">平均 {average} 毫秒</p> : null}
          </>
        ) : null}
        {phase === "interrupted" ? (
          <>
            <h2 className="reaction-game__title">测试已中止</h2>
            <p className="reaction-game__message">页面切换后请重新开始本项。</p>
          </>
        ) : null}
      </div>
    </section>
  )
}
