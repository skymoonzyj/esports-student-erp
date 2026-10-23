import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent,
} from "react"

import type { GameProps, InputKind } from "../../contracts"
import {
  answerVerbalMemory,
  startVerbalMemory,
  type VerbalMemoryAnswer,
  type VerbalMemoryState,
} from "./engine"
import "./VerbalGame.css"

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

export function VerbalGame({
  onComplete,
  onInterrupted,
}: GameProps<"verbal-memory">) {
  const [game, setGame] = useState<VerbalMemoryState | null>(null)
  const answeredTurnRef = useRef(-1)
  const activeRef = useRef(false)
  const completedRef = useRef(false)
  const runStartedAtRef = useRef<number | null>(null)
  const inputKindsRef = useRef<Set<InputKind>>(new Set())
  const onInterruptedRef = useRef(onInterrupted)

  const reset = useCallback(() => {
    activeRef.current = false
    completedRef.current = false
    answeredTurnRef.current = -1
    runStartedAtRef.current = null
    inputKindsRef.current = new Set()
    setGame(null)
  }, [])

  useEffect(() => {
    onInterruptedRef.current = onInterrupted
  }, [onInterrupted])

  useEffect(() => {
    function handleVisibilityChange() {
      if (
        document.visibilityState !== "hidden" ||
        !activeRef.current ||
        completedRef.current
      ) {
        return
      }

      reset()
      onInterruptedRef.current("hidden")
    }

    document.addEventListener("visibilitychange", handleVisibilityChange)
    return () => {
      activeRef.current = false
      document.removeEventListener("visibilitychange", handleVisibilityChange)
    }
  }, [reset])

  function start() {
    answeredTurnRef.current = -1
    activeRef.current = true
    completedRef.current = false
    runStartedAtRef.current = performance.now()
    inputKindsRef.current = new Set()
    setGame(startVerbalMemory())
  }

  function recordPointer(event: PointerEvent<HTMLButtonElement>) {
    if (!event.isPrimary || (event.pointerType === "mouse" && event.button !== 0)) {
      return
    }
    inputKindsRef.current.add(normalizePointerType(event.pointerType))
  }

  function answer(choice: VerbalMemoryAnswer) {
    if (
      game === null ||
      game.finished ||
      completedRef.current ||
      answeredTurnRef.current === game.turn
    ) {
      return
    }

    answeredTurnRef.current = game.turn
    const next = answerVerbalMemory(game, choice)
    setGame(next)
    if (!next.finished) return

    activeRef.current = false
    completedRef.current = true
    const endedAt = performance.now()
    const startedAt = runStartedAtRef.current ?? endedAt
    onComplete({
      test_id: "verbal-memory",
      raw: {
        correct_count: next.score,
        error_count: 3,
        total_count: next.turn,
        word_bank_version: "zh-192-v1",
      },
      device: {
        input_kind: inputKindFor(inputKindsRef.current),
        viewport_width: window.innerWidth,
        viewport_height: window.innerHeight,
        geometry: null,
      },
      duration_ms: Math.max(0, Math.round(endedAt - startedAt)),
      background_interrupted: false,
    })
  }

  return (
    <section aria-label="中文词语记忆测试区域" className="verbal-game">
      <h2 className="verbal-game__title">中文词语记忆</h2>

      {game === null ? (
        <div className="verbal-game__intro">
          <p>词语会逐个出现。出现过请选择“见过”，第一次出现请选择“新词”。</p>
          <p>成绩按累计正确判断次数记录；你有 3 次出错机会。</p>
          <p className="verbal-game__pilot">评分锚点为内部试行训练目标。</p>
          <button className="verbal-game__start" onClick={start} type="button">
            开始测试
          </button>
        </div>
      ) : null}

      {game !== null && !game.finished ? (
        <div className="verbal-game__play">
          <p className="verbal-game__status" aria-live="polite">
            <span>剩余机会 {game.lives}</span>
            <span>累计正确判断 {game.score}</span>
          </p>
          <p className="verbal-game__word" aria-atomic="true" aria-live="polite">
            {game.currentWord}
          </p>
          <div className="verbal-game__actions">
            <button
              onClick={() => answer("seen")}
              onPointerDown={recordPointer}
              type="button"
            >
              见过
            </button>
            <button
              onClick={() => answer("new")}
              onPointerDown={recordPointer}
              type="button"
            >
              新词
            </button>
          </div>
        </div>
      ) : null}

      {game?.finished ? (
        <div className="verbal-game__result" aria-live="polite">
          <p>测试完成</p>
          <p>累计正确判断 {game.score} 次</p>
          <p className="verbal-game__pilot">评分锚点为内部试行训练目标。</p>
        </div>
      ) : null}
    </section>
  )
}
