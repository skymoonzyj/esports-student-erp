import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
  type PointerEvent,
} from "react"

import type { DynamicVisionRule, GameProps, InputKind, RawByTest } from "../../contracts"
import {
  DYNAMIC_VISION_RULE,
  createDynamicVisionDigits,
  judgeDynamicVisionAnswer,
  normalizeDynamicVisionInput,
  summarizeDynamicVision,
  type DynamicVisionDifficulty,
  type DynamicVisionOutcome,
} from "./engine"
import "./DynamicVisionGame.css"

type DynamicVisionPhase =
  | "intro"
  | "countdown-two"
  | "countdown-one"
  | "blank"
  | "displaying"
  | "answer"
  | "review"
  | "completed"

type FixedGeometry = {
  width: number
  height: number
  fontSize: 40 | 56
  viewportWidth: number
  viewportHeight: number
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

function measuredGeometry(
  stage: HTMLElement,
  observedSize?: { width: number; height: number },
): FixedGeometry | null {
  const rect = stage.getBoundingClientRect()
  const width = observedSize?.width ?? rect.width
  const height = observedSize?.height ?? rect.height
  const fontSize = Number.parseFloat(window.getComputedStyle(stage).fontSize)
  const expectedFontSize = window.innerWidth < 600 ? 40 : 56
  if (
    !Number.isFinite(width) || width <= 0 ||
    !Number.isFinite(height) || height <= 0 ||
    fontSize !== expectedFontSize
  ) return null
  return {
    width,
    height,
    fontSize: expectedFontSize,
    viewportWidth: window.innerWidth,
    viewportHeight: window.innerHeight,
  }
}

function sameDisplayGeometry(left: FixedGeometry, right: FixedGeometry): boolean {
  return left.width === right.width &&
    left.height === right.height &&
    left.fontSize === right.fontSize
}

export function DynamicVisionGame({
  onComplete,
  onInterrupted,
  onAttemptStarted,
  rule = DYNAMIC_VISION_RULE,
}: GameProps<"dynamic-vision"> & { onAttemptStarted?: () => void; rule?: DynamicVisionRule }) {
  const [phase, setPhase] = useState<DynamicVisionPhase>("intro")
  const [selectedLevel, setSelectedLevel] = useState<DynamicVisionDifficulty>(1)
  const [questionIndex, setQuestionIndex] = useState(0)
  const [digits, setDigits] = useState("")
  const [answer, setAnswer] = useState("")
  const [outcomes, setOutcomes] = useState<DynamicVisionOutcome[]>([])
  const [lastOutcome, setLastOutcome] = useState<DynamicVisionOutcome | null>(null)
  const [measurementError, setMeasurementError] = useState<string | null>(null)
  const stageRef = useRef<HTMLDivElement>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const startedAtRef = useRef<number | null>(null)
  const displayStartedAtRef = useRef<number | null>(null)
  const terminalRef = useRef(false)
  const submittingRef = useRef(false)
  const composingRef = useRef(false)
  const measuredRef = useRef<number[]>([])
  const outcomesRef = useRef<DynamicVisionOutcome[]>([])
  const inputKindsRef = useRef<Set<InputKind>>(new Set())
  const geometryRef = useRef<FixedGeometry | null>(null)
  const onCompleteRef = useRef(onComplete)
  const onInterruptedRef = useRef(onInterrupted)
  const selectedRule = rule.levels[selectedLevel - 1]
  const maxScore = rule.engine_version === "dynamic-vision-v2" ? rule.max_score : 100

  useEffect(() => {
    onCompleteRef.current = onComplete
  }, [onComplete])

  useEffect(() => {
    onInterruptedRef.current = onInterrupted
  }, [onInterrupted])

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const resetAttempt = useCallback(() => {
    clearTimer()
    startedAtRef.current = null
    displayStartedAtRef.current = null
    submittingRef.current = false
    composingRef.current = false
    measuredRef.current = []
    outcomesRef.current = []
    inputKindsRef.current = new Set()
    geometryRef.current = null
    setQuestionIndex(0)
    setDigits("")
    setAnswer("")
    setOutcomes([])
    setLastOutcome(null)
    setPhase("intro")
  }, [clearTimer])

  const interruptAttempt = useCallback((reason: "hidden" | "geometry_changed") => {
    if (terminalRef.current || startedAtRef.current === null) return
    terminalRef.current = true
    resetAttempt()
    onInterruptedRef.current(reason)
  }, [resetAttempt])

  const validateActiveGeometry = useCallback((
    observedSize?: { width: number; height: number },
  ): boolean => {
    if (terminalRef.current || startedAtRef.current === null) return false
    const stage = stageRef.current
    const fixed = geometryRef.current
    const current = stage ? measuredGeometry(stage, observedSize) : null
    if (!fixed || !current || !sameDisplayGeometry(fixed, current)) {
      interruptAttempt("geometry_changed")
      return false
    }
    return true
  }, [interruptAttempt])

  useEffect(() => {
    const stage = stageRef.current
    if (!stage) return

    const observer = new ResizeObserver((entries) => {
      const entry = entries.find((candidate) => candidate.target === stage)
      if (entry) validateActiveGeometry(entry.contentRect)
    })
    observer.observe(stage)
    return () => observer.disconnect()
  }, [validateActiveGeometry])

  useEffect(() => {
    if (phase === "intro" || phase === "completed" || startedAtRef.current === null) return
    let frame: number | null = null
    const checkFont = () => {
      frame = null
      if (!validateActiveGeometry()) return
      frame = requestAnimationFrame(checkFont)
    }
    frame = requestAnimationFrame(checkFont)
    return () => {
      if (frame !== null) cancelAnimationFrame(frame)
    }
  }, [phase, validateActiveGeometry])

  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.hidden) interruptAttempt("hidden")
    }
    document.addEventListener("visibilitychange", handleVisibilityChange)
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange)
  }, [interruptAttempt])

  useEffect(() => {
    let duration: number | null = null
    let next: DynamicVisionPhase | null = null
    if (phase === "countdown-two") {
      duration = 600
      next = "countdown-one"
    } else if (phase === "countdown-one") {
      duration = 600
      next = "blank"
    } else if (phase === "blank") {
      duration = 1300
      next = "displaying"
    }
    if (duration === null || next === null) return
    const timer = setTimeout(() => {
      if (timerRef.current === timer) timerRef.current = null
      if (!document.hidden && validateActiveGeometry()) setPhase(next)
    }, duration)
    timerRef.current = timer
    return () => {
      clearTimeout(timer)
      if (timerRef.current === timer) timerRef.current = null
    }
  }, [phase, validateActiveGeometry])

  useLayoutEffect(() => {
    if (phase === "displaying") {
      if (!validateActiveGeometry()) return
      if (displayStartedAtRef.current === null) {
        displayStartedAtRef.current = performance.now()
      }
      const timer = setTimeout(() => {
        if (timerRef.current === timer) timerRef.current = null
        if (!document.hidden && validateActiveGeometry()) setPhase("answer")
      }, selectedRule.nominal_display_ms)
      timerRef.current = timer
      return () => {
        clearTimeout(timer)
        if (timerRef.current === timer) timerRef.current = null
      }
    }
    if (phase === "answer" && displayStartedAtRef.current !== null) {
      const measured = performance.now() - displayStartedAtRef.current
      displayStartedAtRef.current = null
      if (!Number.isFinite(measured) || measured <= 0 || measured > 10_000) {
        terminalRef.current = true
        resetAttempt()
        setMeasurementError("无法可靠测量显示时长，请重新开始。")
        return
      }
      measuredRef.current.push(measured)
    }
  }, [phase, resetAttempt, selectedRule.nominal_display_ms, validateActiveGeometry])

  function startAttempt() {
    if (startedAtRef.current !== null || !stageRef.current) return
    const geometry = measuredGeometry(stageRef.current)
    if (!geometry) {
      setMeasurementError("无法可靠测量测试区域，请调整窗口后重新开始。")
      return
    }
    terminalRef.current = false
    submittingRef.current = false
    composingRef.current = false
    measuredRef.current = []
    outcomesRef.current = []
    inputKindsRef.current = new Set()
    geometryRef.current = geometry
    startedAtRef.current = performance.now()
    displayStartedAtRef.current = null
    setMeasurementError(null)
    setQuestionIndex(0)
    setDigits(createDynamicVisionDigits(selectedRule))
    setAnswer("")
    setOutcomes([])
    setLastOutcome(null)
    setPhase("countdown-two")
    onAttemptStarted?.()
  }

  function submitAnswer(skip: boolean) {
    if (
      phase !== "answer" ||
      submittingRef.current ||
      terminalRef.current ||
      (!skip && answer.length !== selectedRule.digit_count)
    ) return
    if (!validateActiveGeometry()) return
    submittingRef.current = true
    const outcome: DynamicVisionOutcome = skip
      ? "skipped"
      : judgeDynamicVisionAnswer(digits, answer)
    const nextOutcomes = [...outcomesRef.current, outcome]
    outcomesRef.current = nextOutcomes
    setOutcomes(nextOutcomes)
    setLastOutcome(outcome)

    if (nextOutcomes.length !== 5) {
      setPhase("review")
      return
    }

    const geometry = geometryRef.current
    const startedAt = startedAtRef.current
    const endedAt = performance.now()
    const measured = [...measuredRef.current]
    const duration = startedAt === null ? Number.NaN : endedAt - startedAt
    if (
      !geometry ||
      measured.length !== 5 ||
      !Number.isFinite(duration) ||
      duration < measured.reduce((total, value) => total + value, 0)
    ) {
      terminalRef.current = true
      resetAttempt()
      setMeasurementError("无法可靠测量本次测试，请重新开始。")
      return
    }

    terminalRef.current = true
    clearTimer()
    setPhase("completed")
    onCompleteRef.current({
      test_id: "dynamic-vision",
      raw: {
        selected_level: selectedLevel,
        outcomes: nextOutcomes as RawByTest["dynamic-vision"]["outcomes"],
        measured_display_ms: measured as RawByTest["dynamic-vision"]["measured_display_ms"],
        font_size_px: geometry.fontSize,
      },
      device: {
        input_kind: observedInputKind(inputKindsRef.current),
        viewport_width: geometry.viewportWidth,
        viewport_height: geometry.viewportHeight,
        geometry: {
          width: geometry.width,
          height: geometry.height,
          target_diameter: null,
        },
      },
      duration_ms: duration,
      background_interrupted: false,
    })
  }

  function nextQuestion() {
    if (phase !== "review" || terminalRef.current) return
    if (!validateActiveGeometry()) return
    submittingRef.current = false
    composingRef.current = false
    setQuestionIndex((value) => value + 1)
    setDigits(createDynamicVisionDigits(selectedRule))
    setAnswer("")
    setLastOutcome(null)
    setPhase("countdown-two")
  }

  function handleChange(event: ChangeEvent<HTMLInputElement>) {
    setAnswer(normalizeDynamicVisionInput(event.target.value, selectedRule.digit_count))
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!composingRef.current) submitAnswer(false)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Enter" || event.repeat) return
    event.preventDefault()
    if (!composingRef.current && !event.nativeEvent.isComposing) submitAnswer(false)
  }

  function handlePointerDown(event: PointerEvent<HTMLElement>) {
    if (!event.isPrimary || (event.pointerType === "mouse" && event.button !== 0)) return
    inputKindsRef.current.add(pointerInputKind(event.pointerType))
  }

  const summary = outcomes.length === 5
    ? summarizeDynamicVision(selectedLevel, outcomes, rule)
    : null
  const uncappedScore = summary ? summary.baseScore * selectedRule.coefficient_tenths / 10 : 0
  const difficultyPicker = phase !== "completed" ? (
    <fieldset>
      <legend>选择难度</legend>
      <div className="dynamic-vision__levels">
        {rule.levels.map((rule) => (
          <label key={rule.level}>
            <input
              checked={selectedLevel === rule.level}
              disabled={phase !== "intro"}
              name="dynamic-vision-level"
              onChange={() => setSelectedLevel(rule.level)}
              type="radio"
              value={rule.level}
            />
            第 {rule.level} 级：{rule.digit_count} 位，标称 {rule.nominal_display_ms} 毫秒，
            每题 {rule.coefficient_tenths} 分，最高 {Math.min(maxScore, rule.coefficient_tenths * 5)} 分
            {rule.coefficient_tenths * 5 > maxScore ? "（100 分封顶）" : null}
          </label>
        ))}
      </div>
    </fieldset>
  ) : null

  return (
    <section
      aria-label="动态视力测试区域"
      className={`dynamic-vision dynamic-vision--${phase}`}
      onPointerDownCapture={handlePointerDown}
    >
      {phase === "intro" ? (
        <div className="dynamic-vision__intro">
          <h2>动态视力</h2>
          <p>瞬时数字识别：先选择一个难度，每轮完成五题。</p>
          <p>这是依赖视觉短时呈现的内部训练参考，不是医学视力检查。</p>
          <p>浏览器测得的显示时长并非硬件校准值。</p>
          <p>不能据此认定使用辅助技术用户的能力。</p>
          {difficultyPicker}
          {measurementError ? <p role="alert">{measurementError}</p> : null}
          <button type="button" onClick={startAttempt}>
            {measurementError ? "重新开始" : "开始测试"}
          </button>
        </div>
      ) : null}

      {phase !== "intro" ? (
        <>
          {difficultyPicker}
          <p className="dynamic-vision__progress">第 {questionIndex + 1}/5 题 · 第 {selectedLevel} 级</p>
        </>
      ) : null}

      <div className="dynamic-vision__stage" data-testid="dynamic-vision-stage" ref={stageRef}>
        {phase === "countdown-two" ? "2" : null}
        {phase === "countdown-one" ? "1" : null}
        {phase === "displaying" ? (
          <span
            aria-hidden="true"
            data-testid="dynamic-vision-stimulus"
            style={{
              letterSpacing: rule.engine_version === "dynamic-vision-v2" ? "0.16em" : "0em",
              // CSS adds spacing after the last glyph; exclude it from centering.
              marginInlineEnd: rule.engine_version === "dynamic-vision-v2" ? "-0.16em" : undefined,
            }}
          >
            {digits}
          </span>
        ) : null}
      </div>

      {phase === "answer" ? (
        <form className="dynamic-vision__answer" onSubmit={handleSubmit}>
          <label htmlFor="dynamic-vision-answer">输入数字</label>
          <input
            id="dynamic-vision-answer"
            autoComplete="off"
            autoFocus
            inputMode="numeric"
            maxLength={selectedRule.digit_count}
            pattern="[0-9]*"
            value={answer}
            onChange={handleChange}
            onCompositionStart={() => { composingRef.current = true }}
            onCompositionEnd={() => { composingRef.current = false }}
            onKeyDown={handleKeyDown}
          />
          <div aria-label="屏幕数字键盘" className="dynamic-vision__keypad">
            {Array.from({ length: 10 }, (_, digit) => (
              <button
                aria-label={`数字 ${digit}`}
                disabled={answer.length >= selectedRule.digit_count}
                key={digit}
                onClick={() => setAnswer((value) =>
                  normalizeDynamicVisionInput(`${value}${digit}`, selectedRule.digit_count)
                )}
                type="button"
              >
                {digit}
              </button>
            ))}
          </div>
          <div className="dynamic-vision__actions">
            <button type="button" onClick={() => setAnswer((value) => value.slice(0, -1))}>删除一位</button>
            <button type="button" onClick={() => setAnswer("")}>清空</button>
            <button type="submit" disabled={answer.length !== selectedRule.digit_count}>提交答案</button>
            <button type="button" onClick={() => submitAnswer(true)}>跳过本题</button>
          </div>
        </form>
      ) : null}

      {phase === "review" || phase === "completed" ? (
        <div aria-live="polite" className="dynamic-vision__review">
          <h2>
            {lastOutcome === "correct"
              ? "回答正确"
              : lastOutcome === "skipped"
                ? "本题已跳过"
                : "回答错误"}
          </h2>
          <p>正确数字：{digits}</p>
          {lastOutcome !== "skipped" ? <p>你的答案：{answer}</p> : null}
          {phase === "review" ? (
            <button type="button" onClick={nextQuestion}>下一题</button>
          ) : null}
          {phase === "completed" && summary ? (
            <div className="dynamic-vision__summary">
              <h2>本项已完成</h2>
              <p>第 {selectedLevel} 级，答对 {summary.correctCount}/5 题</p>
              <p>
                基础分 {summary.baseScore} × 难度系数 {summary.coefficient.toFixed(1)} = {uncappedScore} 分
                {uncappedScore > maxScore ? `，最终 ${summary.score} 分（${maxScore} 分封顶）` : null}
              </p>
              <p>结果正在保存，请稍候。</p>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
