import { useCallback, useEffect, useRef, useState } from "react"
import { Link } from "react-router-dom"
import { ItemRankingPanel } from "../assessmentRankings/ItemRankingPanel"
import { useTalentItemRanking } from "./api"

import { AimGame } from "./games/aim/AimGame"
import { ChimpGame } from "./games/chimp/ChimpGame"
import { DynamicVisionGame } from "./games/dynamicVision/DynamicVisionGame"
import { NumberGame } from "./games/number/NumberGame"
import { ReactionGame } from "./games/reaction/ReactionGame"
import { SequenceGame } from "./games/sequence/SequenceGame"
import { VerbalGame } from "./games/verbal/VerbalGame"
import { VisualGame } from "./games/visual/VisualGame"
import { isSupportedEngineVersion, type CompletedGame, type DynamicVisionRule, type RawByTest, type TestId } from "./contracts"
import { GameChrome } from "./GameChrome"
import { useTalentSession } from "./useTalentSession"

export type GameHostProps = {
  accountId: string
  personId: string
  sessionId: string
  testId: TestId
  onSessionCompleted?: (reportId: string) => void
}

const COPY: Record<TestId, { title: string; instructions: string }> = {
  reactiontime: { title: "反应速度", instructions: "完成五次有效反应，按平均响应时间评分并保存结果。" },
  aim: { title: "瞄准速度", instructions: "依次点击目标并完成全部回合。" },
  sequence: { title: "顺序记忆", instructions: "记住亮起顺序并按原顺序点击。" },
  "number-memory": { title: "数字记忆", instructions: "记住数字并在隐藏后输入。" },
  "verbal-memory": { title: "中文词语记忆", instructions: "判断当前词语是否曾经出现。" },
  chimp: { title: "黑猩猩测试", instructions: "按数字从小到大的顺序点击方格。" },
  memory: { title: "视觉记忆", instructions: "记住亮起方块的位置。" },
  "dynamic-vision": { title: "动态视力", instructions: "选择一个难度，完成五次瞬时数字识别。" },
}

function DynamicVisionSummary({ raw, rule, serverScore }: {
  raw: RawByTest[TestId]
  rule: DynamicVisionRule
  serverScore?: number
}) {
  if (!("selected_level" in raw)) return null
  const level = rule.levels.find((candidate) => candidate.level === raw.selected_level)
  if (!level) return null
  const correctCount = raw.outcomes.filter((outcome) => outcome === "correct").length
  const baseScore = correctCount * rule.base_points_per_correct
  const uncappedScore = baseScore * level.coefficient_tenths / 10
  const maxScore = rule.engine_version === "dynamic-vision-v2" ? rule.max_score : 100
  const score = Math.min(maxScore, uncappedScore)
  return (
    <section aria-label="动态视力结果摘要">
      <p>第 {raw.selected_level} 级，答对 {correctCount}/5 题</p>
      <p>基础分 {baseScore} × 难度系数 {(level.coefficient_tenths / 10).toFixed(1)}</p>
      {uncappedScore > maxScore ? <p>试算原始 {uncappedScore} 分，最终 {score} 分（{maxScore} 分封顶）</p> : null}
      {serverScore === undefined ? (
        <p>试算分：{score} 分（尚未保存，不计入完成进度）</p>
      ) : (
        <p className="talent-game-score">服务器成绩：{serverScore} 分</p>
      )}
    </section>
  )
}

export function GameHost({
  accountId,
  personId,
  sessionId,
  testId,
  onSessionCompleted,
}: GameHostProps) {
  const {
    session,
    pendingResult,
    readState,
    reread,
    saveState,
    statusText,
    retryAfterSeconds,
    submit,
    retry,
  } = useTalentSession(accountId, personId, sessionId)
  const [gameKey, setGameKey] = useState(0)
  const [interruption, setInterruption] = useState<string | null>(null)
  const terminalKeyRef = useRef<number | null>(null)
  const interruptedKeyRef = useRef<number | null>(null)
  const announcedReportIdsRef = useRef(new Set<string>())
  const navigationPressRef = useRef<Element | null>(null)

  useEffect(() => {
    setGameKey((value) => value + 1)
    terminalKeyRef.current = null
    interruptedKeyRef.current = null
    setInterruption(null)
  }, [accountId, personId, sessionId, testId])

  useEffect(() => {
    if (saveState !== "rejected" || terminalKeyRef.current === null) return
    terminalKeyRef.current = null
    setGameKey((value) => value + 1)
  }, [saveState])

  const handleComplete = useCallback((result: CompletedGame) => {
    if (result.test_id !== testId || terminalKeyRef.current === gameKey) return
    terminalKeyRef.current = gameKey
    void submit(result)
  }, [gameKey, submit, testId])

  const handleInterrupted = useCallback((reason: "hidden" | "geometry_changed") => {
    if (terminalKeyRef.current === gameKey || interruptedKeyRef.current === gameKey) return
    interruptedKeyRef.current = gameKey
    setInterruption(
      reason === "hidden"
        ? "页面切换已中止本项，请重新开始"
        : "游戏区域尺寸已改变，本项已中止，请重新开始",
    )
    setGameKey((value) => value + 1)
  }, [gameKey])

  const handleAttemptStarted = useCallback(() => {
    interruptedKeyRef.current = null
    setInterruption(null)
  }, [])

  const copy = COPY[testId]
  const saved = session?.results.find((result) => result.test_id === testId)
  const itemRanking = useTalentItemRanking(accountId, personId, saved?.id)
  const sessionIncludesTest = session?.rules.test_ids.includes(testId) ?? false
  const activeRule = sessionIncludesTest ? session?.rules.tests[testId] : undefined
  const actualVersion = activeRule?.engine_version
  const nextTest = saved && session?.status === "in_progress"
    ? session.rules.test_ids.find((candidate) => !session.results.some((result) => result.test_id === candidate))
    : undefined

  useEffect(() => {
    if (saved || saveState === "saved") setInterruption(null)
  }, [saveState, saved])

  useEffect(() => {
    const reportId = session?.status === "completed" ? session.report_id : null
    if (!reportId || !onSessionCompleted || announcedReportIdsRef.current.has(reportId)) return
    announcedReportIdsRef.current.add(reportId)
    onSessionCompleted(reportId)
  }, [onSessionCompleted, session?.report_id, session?.status])

  let content = <p>正在加载本轮训练…</p>
  if (!session && readState === "failed") {
    content = <button type="button" onClick={reread}>重新读取本轮</button>
  } else if (!session && readState === "blocked") {
    content = <p>本轮读取已停止，请验证身份后重新进入。</p>
  }
  let activeGame = false
  if (session) {
    if (!sessionIncludesTest || !activeRule) {
      content = <p role="alert">本轮不包含该测试项目。</p>
    } else if (saved) {
      content = testId === "dynamic-vision" && "kind" in activeRule ? (
        <DynamicVisionSummary raw={saved.raw} rule={activeRule} serverScore={saved.score} />
      ) : (
        <div>
          {testId === "reactiontime" ? <p>5 次平均响应：{saved.raw_value} 毫秒</p> : null}
          <p className="talent-game-score">服务器成绩：{saved.score} 分</p>
        </div>
      )
    } else if (session.status !== "in_progress") {
      content = <p>本轮训练已结束，不能继续作答。</p>
    } else if (!isSupportedEngineVersion(testId, actualVersion)) {
      content = <p role="alert">规则版本暂不受支持，请联系管理员。</p>
    } else if (saveState === "saving" || saveState === "pending") {
      content = (
        <div>
          {testId === "dynamic-vision" && pendingResult?.test_id === testId && "kind" in activeRule ? (
            <DynamicVisionSummary raw={pendingResult.raw} rule={activeRule} />
          ) : null}
          <p>结果已经完成，正在等待服务器确认。</p>
          {saveState === "pending" ? (
            <button
              type="button"
              disabled={retryAfterSeconds > 0}
              onClick={() => void retry()}
            >
              {retryAfterSeconds > 0
                ? `${retryAfterSeconds} 秒后可重试`
                : "手动重试"}
            </button>
          ) : null}
        </div>
      )
    } else {
      activeGame = true
      switch (testId) {
        case "reactiontime":
          content = <ReactionGame key={gameKey} onComplete={handleComplete} onInterrupted={handleInterrupted} />
          break
        case "aim":
          content = <AimGame key={gameKey} onComplete={handleComplete} onInterrupted={handleInterrupted} />
          break
        case "sequence":
          content = <SequenceGame key={gameKey} onComplete={handleComplete} onInterrupted={handleInterrupted} />
          break
        case "number-memory":
          content = <NumberGame key={gameKey} onComplete={handleComplete} onInterrupted={handleInterrupted} />
          break
        case "verbal-memory":
          content = <VerbalGame key={gameKey} onComplete={handleComplete} onInterrupted={handleInterrupted} />
          break
        case "chimp":
          content = <ChimpGame key={gameKey} onComplete={handleComplete} onInterrupted={handleInterrupted} />
          break
        case "memory":
          content = <VisualGame key={gameKey} onComplete={handleComplete} onInterrupted={handleInterrupted} />
          break
        case "dynamic-vision":
          content = "kind" in activeRule ? <DynamicVisionGame key={gameKey} rule={activeRule} onComplete={handleComplete} onInterrupted={handleInterrupted} onAttemptStarted={handleAttemptStarted} /> : <p role="alert">规则版本暂不受支持，请联系管理员。</p>
          break
      }
    }
  }

  if (activeGame) {
    // Native controls activate on click; removing the status on press would
    // move them before pointer release / keyboard activation. Only these two
    // custom games consume the press itself (including stopped propagation).
    const startsOnPress = testId === "reactiontime" || testId === "aim"
    // Dynamic vision owns its start boundary: configuring its controlled radio
    // must not trigger a parent render before the radio change is committed.
    const startsOnClick = !startsOnPress && testId !== "dynamic-vision"
    content = (
      <div
        onKeyDownCapture={startsOnPress ? handleAttemptStarted : undefined}
        onPointerDownCapture={startsOnPress ? handleAttemptStarted : undefined}
        onClickCapture={startsOnClick ? handleAttemptStarted : undefined}
        style={{ display: "contents" }}
      >
        {content}
      </div>
    )
  }

  const recoveryStatusVisible = saveState === "saving" ||
    saveState === "pending" ||
    saveState === "rejected"
  const visibleStatusText = recoveryStatusVisible
    ? statusText ?? interruption
    : interruption ?? (saveState === "saved" && !saved ? null : statusText)

  return (
    <GameChrome
      title={copy.title}
      instructions={copy.instructions}
      statusText={visibleStatusText}
    >
      <div
        onPointerDownCapture={(event) => {
          navigationPressRef.current = event.target instanceof Element
            ? event.target.closest("a")
            : null
        }}
        onPointerCancelCapture={() => { navigationPressRef.current = null }}
      >
        {content}
        <nav
          className="talent-actions"
          aria-label="测试项目导航"
          onClickCapture={(event) => {
            const link = event.target instanceof Element ? event.target.closest("a") : null
            const pressedLink = navigationPressRef.current
            navigationPressRef.current = null
            // Completing on pointer-down can shrink the stage before touch-up.
            // Ignore a click retargeted onto a link; keyboard/AT clicks have detail 0.
            if (event.detail > 0 && link !== pressedLink) {
              event.preventDefault()
              event.stopPropagation()
            }
          }}
        >
          {nextTest ? (
            <Link className="primaryButton" to={`/talent-tests/sessions/${sessionId}/${nextTest}`}>
              继续下一项：{COPY[nextTest].title}
            </Link>
          ) : null}
          {session?.status === "completed" && session.report_id ? (
            <Link className="primaryButton" to={`/talent-tests/reports/${session.report_id}`}>
              查看本轮系统评价
            </Link>
          ) : null}
          <Link className="secondaryButton" to="/talent-tests">返回项目列表</Link>
        </nav>
        {saved ? <ItemRankingPanel
          view={itemRanking.data}
          loading={itemRanking.isFetching}
          error={itemRanking.error}
          onRetry={() => { void itemRanking.refetch() }}
        /> : null}
      </div>
    </GameChrome>
  )
}
