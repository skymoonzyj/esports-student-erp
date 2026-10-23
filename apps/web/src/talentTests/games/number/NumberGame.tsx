import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
  type PointerEvent,
} from "react"

import type { GameProps, InputKind } from "../../contracts"
import {
  createNumberString,
  evaluateNumberAnswer,
  memoryDurationMs,
} from "./engine"
import "./NumberGame.css"

type NumberPhase = "intro" | "memorizing" | "answer" | "review" | "completed"

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

export function NumberGame({
  onComplete,
  onInterrupted,
}: GameProps<"number-memory">) {
  const [phase, setPhase] = useState<NumberPhase>("intro")
  const [level, setLevel] = useState(1)
  const [number, setNumber] = useState("")
  const [answer, setAnswer] = useState("")
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const gameStartedAtRef = useRef<number | null>(null)
  const terminalRef = useRef(false)
  const submittingRef = useRef(false)
  const composingRef = useRef(false)
  const inputKindsRef = useRef<Set<InputKind>>(new Set())
  const onInterruptedRef = useRef(onInterrupted)

  useEffect(() => {
    onInterruptedRef.current = onInterrupted
  }, [onInterrupted])

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const startRound = useCallback(
    (nextLevel: number) => {
      clearTimer()
      if (gameStartedAtRef.current === null) {
        gameStartedAtRef.current = performance.now()
      }
      terminalRef.current = false
      submittingRef.current = false
      composingRef.current = false
      setLevel(nextLevel)
      setNumber(createNumberString(nextLevel))
      setAnswer("")
      setPhase("memorizing")
      timerRef.current = setTimeout(() => {
        timerRef.current = null
        if (!terminalRef.current && !document.hidden) {
          setPhase("answer")
        }
      }, memoryDurationMs(nextLevel))
    },
    [clearTimer],
  )

  const interrupt = useCallback(() => {
    if (terminalRef.current || gameStartedAtRef.current === null) return

    terminalRef.current = true
    clearTimer()
    submittingRef.current = false
    composingRef.current = false
    gameStartedAtRef.current = null
    inputKindsRef.current.clear()
    setLevel(1)
    setNumber("")
    setAnswer("")
    setPhase("intro")
    onInterruptedRef.current("hidden")
  }, [clearTimer])

  useEffect(() => {
    function handleVisibilityChange() {
      if (document.hidden) interrupt()
    }

    document.addEventListener("visibilitychange", handleVisibilityChange)
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange)
      terminalRef.current = true
      clearTimer()
    }
  }, [clearTimer, interrupt])

  function submitAnswer() {
    if (
      phase !== "answer" ||
      submittingRef.current ||
      terminalRef.current ||
      answer.length === 0
    ) {
      return
    }

    submittingRef.current = true
    const result = evaluateNumberAnswer(number, answer, level)
    if (result.correct) {
      setPhase("review")
      return
    }

    terminalRef.current = true
    clearTimer()
    setPhase("completed")
    const endedAt = performance.now()
    const startedAt = gameStartedAtRef.current ?? endedAt
    onComplete({
      test_id: "number-memory",
      raw: {
        correct_digits: result.scoreDigits,
        failed_digits: result.failedLevel,
        display_duration_ms: memoryDurationMs(result.failedLevel),
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

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!composingRef.current) submitAnswer()
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Enter" || event.repeat) return
    event.preventDefault()
    if (!composingRef.current && !event.nativeEvent.isComposing) submitAnswer()
  }

  function handleChange(event: ChangeEvent<HTMLInputElement>) {
    setAnswer(event.target.value.replace(/\D/g, ""))
  }

  function handlePointerDown(event: PointerEvent<HTMLElement>) {
    if (!event.isPrimary || (event.pointerType === "mouse" && event.button !== 0)) return
    inputKindsRef.current.add(pointerInputKind(event.pointerType))
  }

  return (
    <section
      aria-label="数字记忆测试区域"
      className={`number-game number-game--${phase}`}
      onPointerDownCapture={handlePointerDown}
    >
      {phase === "intro" ? (
        <div className="number-game__panel">
          <h2>数字记忆</h2>
          <p>记住屏幕上的数字，然后按原顺序输入。</p>
          <button type="button" onClick={() => startRound(1)}>
            开始数字记忆
          </button>
        </div>
      ) : null}

      {phase === "memorizing" ? (
        <div className="number-game__panel" aria-live="polite">
          <p className="number-game__level">第 {level} 关</p>
          <p className="number-game__digits">{number}</p>
          <p>请记住这个数字</p>
        </div>
      ) : null}

      {phase === "answer" ? (
        <form className="number-game__panel" onSubmit={handleSubmit}>
          <p className="number-game__level">第 {level} 关</p>
          <label htmlFor="number-memory-answer">输入刚才显示的数字</label>
          <input
            id="number-memory-answer"
            autoComplete="off"
            autoFocus
            inputMode="numeric"
            pattern="[0-9]*"
            value={answer}
            onChange={handleChange}
            onCompositionStart={() => {
              composingRef.current = true
            }}
            onCompositionEnd={() => {
              composingRef.current = false
            }}
            onKeyDown={handleKeyDown}
          />
          <button type="submit" disabled={answer.length === 0}>
            提交答案
          </button>
        </form>
      ) : null}

      {phase === "review" ? (
        <div className="number-game__panel" aria-live="polite">
          <h2>回答正确</h2>
          <dl className="number-game__review">
            <div>
              <dt>数字</dt>
              <dd>{number}</dd>
            </div>
            <div>
              <dt>你的答案</dt>
              <dd>{answer}</dd>
            </div>
          </dl>
          <button type="button" onClick={() => startRound(level + 1)}>
            下一题
          </button>
        </div>
      ) : null}

      {phase === "completed" ? (
        <div className="number-game__panel" role="status">
          <h2>本项已完成</h2>
          <p>成绩正在保存，请稍候。</p>
        </div>
      ) : null}
    </section>
  )
}
