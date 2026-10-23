import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useMemo } from "react"
import { Link } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { formatShanghaiDate } from "../teaching/datetime"
import { getTalentReport } from "./api"
import type {
  DynamicVisionRule,
  Grade,
  NormalizedResult,
  RawByTest,
  TestId,
  TestRule,
} from "./contracts"
import { TALENT_TEST_NAMES } from "./TalentTestsPage"
import "./talentTests.css"

const GRADE_NAMES: Record<Grade, string> = {
  low: "较低",
  medium: "中等",
  high: "较高",
  exceptional: "超高",
}

const INPUT_NOTES: Partial<Record<NormalizedResult["device"]["input_kind"], string>> = {
  touch: "触屏输入，和鼠标结果的适用条件不同",
  pen: "手写笔输入，和鼠标结果的适用条件不同",
  mixed: "混合输入方式，仅在相同输入条件下比较",
}

const QUALITY_FLAG_NOTES: Record<string, string> = {
  timing_anomaly: "计时数据存在异常，请在稳定环境下复测",
  unknown_input: "未能确认输入设备，请使用可识别的输入设备复测",
  background_interrupted: "测试期间页面进入后台或切换，请保持页面前台后复测",
  display_duration_deviation: "显示时长偏差，建议复测",
}

function qualityFlagNote(flag: string): string {
  return QUALITY_FLAG_NOTES[flag] ?? "未识别的质量提示，请联系管理员确认后复测"
}

function needsRetest(result: NormalizedResult): boolean {
  return result.quality_flags.length > 0
}

function primaryRaw(testId: TestId, value: number): string {
  switch (testId) {
    case "reactiontime":
    case "aim": return `${value}毫秒`
    case "sequence":
    case "memory": return `到达${value}关`
    case "number-memory": return `正确${value}位`
    case "verbal-memory": return `累计正确判断${value}次`
    case "chimp": return `到达${value}个数字难度`
    case "dynamic-vision": return `答对${value}/5题`
  }
}

function strengthName(testId: TestId): string {
  const sensitive = ["reactiontime", "aim", "chimp", "dynamic-vision"].includes(testId)
  return TALENT_TEST_NAMES[testId] + (sensitive ? "（本次操作环境下的表现）" : "")
}

function rawDetail(result: NormalizedResult): string | null {
  switch (result.test_id) {
    case "reactiontime":
      return "too_soon_count" in result.raw ? `抢点 ${result.raw.too_soon_count} 次` : null
    case "aim":
      return "miss_count" in result.raw ? `未命中 ${result.raw.miss_count} 次` : null
    case "verbal-memory":
      return "重复词判断也计次，不代表不同词数量"
    case "sequence": {
      if (!("reached_level" in result.raw) || !("completed_level" in result.raw)) return null
      return `到达 ${result.raw.reached_level} 级，完成 ${result.raw.completed_level} 级`
    }
    case "chimp": {
      if (!("reached_count" in result.raw) || !("completed_count" in result.raw)) return null
      return `到达 ${result.raw.reached_count} 个，完成 ${result.raw.completed_count} 个`
    }
    case "memory": {
      if (!("accepted_misses" in result.raw)) return null
      return `到达 ${result.raw.reached_level} 级，完成 ${result.raw.completed_level} 级；有效误点 ${result.raw.accepted_misses} 次`
    }
    case "number-memory": {
      if (!("correct_digits" in result.raw) || !("failed_digits" in result.raw)) return null
      return `通过 ${result.raw.correct_digits} 位，失败于 ${result.raw.failed_digits} 位`
    }
    case "dynamic-vision": {
      const raw = dynamicVisionRaw(result)
      if (!raw) return null
      const correct = raw.outcomes.filter((outcome) => outcome === "correct").length
      const skipped = raw.outcomes.filter((outcome) => outcome === "skipped").length
      return `第 ${raw.selected_level} 级，答对 ${correct}/5 题，跳过 ${skipped} 题`
    }
    default:
      return null
  }
}

function isDynamicVisionRule(
  rule: TestRule | DynamicVisionRule | undefined,
): rule is DynamicVisionRule {
  return rule?.engine_version === "dynamic-vision-v1" &&
    "kind" in rule && rule.kind === "dynamic_vision" &&
    Array.isArray(rule.levels)
}

function isAnchorRule(
  rule: TestRule | DynamicVisionRule | undefined,
): rule is TestRule {
  return Boolean(rule && "anchors" in rule && Array.isArray(rule.anchors))
}

function dynamicVisionRaw(result: NormalizedResult): RawByTest["dynamic-vision"] | null {
  if (result.test_id !== "dynamic-vision") return null
  const raw = result.raw
  if (
    !("selected_level" in raw) ||
    !("outcomes" in raw) ||
    !("measured_display_ms" in raw) ||
    !("font_size_px" in raw) ||
    typeof raw.selected_level !== "number" ||
    !Number.isInteger(raw.selected_level) ||
    raw.selected_level < 1 || raw.selected_level > 5 ||
    !Array.isArray(raw.outcomes) || raw.outcomes.length !== 5 ||
    !raw.outcomes.every((outcome) =>
      outcome === "correct" || outcome === "incorrect" || outcome === "skipped"
    ) ||
    !Array.isArray(raw.measured_display_ms) || raw.measured_display_ms.length !== 5 ||
    !raw.measured_display_ms.every((duration) =>
      typeof duration === "number" && Number.isFinite(duration) && duration > 0 && duration <= 10_000
    ) ||
    (raw.font_size_px !== 40 && raw.font_size_px !== 56)
  ) return null
  return raw as RawByTest["dynamic-vision"]
}

function DynamicVisionDetails({
  result,
  rule,
}: {
  result: NormalizedResult
  rule: DynamicVisionRule
}) {
  const raw = dynamicVisionRaw(result)
  if (!raw) return null
  const level = rule.levels.find((candidate) => candidate.level === raw.selected_level)
  if (!level) return null
  const correctCount = raw.outcomes.filter((outcome) => outcome === "correct").length
  const skippedCount = raw.outcomes.filter((outcome) => outcome === "skipped").length
  const coefficient = (level.coefficient_tenths / 10).toFixed(1)
  const retest = needsRetest(result)
  return (
    <section className="talent-dynamic-report" aria-label="动态视力详细报告">
      <h2>动态视力：瞬时数字识别</h2>
      <dl>
        <dt>本次成绩</dt><dd>第 {raw.selected_level} 级，答对 {correctCount}/5 题</dd>
        <dt>基础分</dt><dd>{correctCount * rule.base_points_per_correct}</dd>
        <dt>难度系数</dt><dd>{coefficient}</dd>
        <dt>计分方式</dt><dd>{correctCount} × 10 × {coefficient} = {result.score}</dd>
        <dt>数字位数</dt><dd>{level.digit_count} 位</dd>
        <dt>标称闪现</dt><dd>{level.nominal_display_ms} 毫秒</dd>
        <dt>跳过</dt><dd>跳过 {skippedCount} 题</dd>
        <dt>档位</dt><dd>{retest ? "需复测" : GRADE_NAMES[result.grade]}</dd>
      </dl>
      <p>浏览器测得的显示时长并非硬件校准值。</p>
      <p>不能据此认定使用辅助技术用户的能力。</p>
      {retest ? (
        <ul aria-label="动态视力质量提示">
          {result.quality_flags.map((flag, index) => (
            <li key={`${flag}:${index}`}>{qualityFlagNote(flag)}</li>
          ))}
        </ul>
      ) : null}
      {correctCount === 5 && raw.selected_level < 5 ? (
        <p>本级最高 {level.coefficient_tenths * 5} 分；可在新一轮尝试更高难度。</p>
      ) : null}
      <div className="talent-report-table-wrap">
        <table className="talent-report-table talent-dynamic-rule-table">
          <caption>动态视力五级规则</caption>
          <thead>
            <tr><th>难度</th><th>位数</th><th>标称闪现</th><th>系数</th><th>最高分</th></tr>
          </thead>
          <tbody>
            {rule.levels.map((item) => (
              <tr key={item.level}>
                <th scope="row">{item.level} 级</th>
                <td>{item.digit_count} 位</td>
                <td>{item.nominal_display_ms} 毫秒</td>
                <td>{(item.coefficient_tenths / 10).toFixed(1)}</td>
                <td>{item.coefficient_tenths * 5} 分</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export function TalentReportPage({
  accountId,
  reportId,
}: {
  accountId: string
  reportId: string
}) {
  const queryClient = useQueryClient()
  const rootKey = useMemo(() => ["talent-tests", accountId, "report"] as const, [accountId])
  const report = useQuery({
    queryKey: [...rootKey, reportId],
    queryFn: () => getTalentReport(reportId),
  })

  useEffect(() => {
    if (!(report.error instanceof ProblemDetailsError) || report.error.problem.status !== 403) return
    void queryClient.cancelQueries({ queryKey: rootKey }).finally(() => {
      queryClient.removeQueries({ queryKey: rootKey })
    })
  }, [queryClient, report.error, rootKey])

  if (report.isPending) return <p>正在读取报告…</p>
  if (report.isError || !report.data) return <p role="alert">报告不存在或无权查看。</p>
  const value = report.data
  const displayName = `${value.person.name}${value.person.nickname ? `（${value.person.nickname}）` : ""}`
  const itemCountName = value.rules.test_ids.length === 8
    ? "八项"
    : value.rules.test_ids.length === 7
      ? "七项"
      : `${value.rules.test_ids.length} 项`
  const dynamicResult = value.results.find((result) => result.test_id === "dynamic-vision")
  const dynamicRule = value.rules.tests["dynamic-vision"]
  const visibleStrengths = value.strengths.filter((testId) => {
    const strengthResult = value.results.find((result) => result.test_id === testId)
    return strengthResult !== undefined && !needsRetest(strengthResult)
  })

  return (
    <div className="talent-page talent-report-page">
      <header className="talent-page-heading">
        <div>
          <p className="talent-game-kicker">TRAINING REPORT</p>
          <h1>训练参考报告</h1>
          <h2>{displayName}</h2>
          <p>完成日期：{formatShanghaiDate(value.completed_at)}</p>
          <p>轮次标识：{value.session_id}</p>
        </div>
        <Link to="/talent-tests">返回天赋测试</Link>
      </header>

      <div className="talent-report-table-wrap">
        <table className="talent-report-table">
          <caption>{itemCountName}训练结果</caption>
          <thead><tr><th>项目</th><th>原始结果</th><th>评分</th><th>等级</th><th>说明</th></tr></thead>
          <tbody>
            {value.results.map((result) => {
              const retest = needsRetest(result)
              const inputNote = INPUT_NOTES[result.device.input_kind]
              return (
                <tr key={result.test_id}>
                  <th scope="row">{TALENT_TEST_NAMES[result.test_id]}</th>
                  <td>
                    <div className="talent-report-cell-stack">
                      <strong>{primaryRaw(result.test_id, result.raw_value)}</strong>
                      {rawDetail(result) ? <small>{rawDetail(result)}</small> : null}
                    </div>
                  </td>
                  <td>{retest ? `试算分：${result.score}，建议复测` : result.score}</td>
                  <td>{retest ? "需复测" : GRADE_NAMES[result.grade]}</td>
                  <td>
                    <div className="talent-report-cell-stack">
                      {retest ? (
                        <ul aria-label={`${TALENT_TEST_NAMES[result.test_id]}质量提示`}>
                          {result.quality_flags.map((flag, index) => (
                            <li key={`${flag}:${index}`}>{qualityFlagNote(flag)}</li>
                          ))}
                        </ul>
                      ) : null}
                      {inputNote ? <span>{inputNote}</span> : null}
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {dynamicResult && isDynamicVisionRule(dynamicRule) ? (
        <DynamicVisionDetails result={dynamicResult} rule={dynamicRule} />
      ) : null}

      <section className="talent-report-summary" aria-label="报告结论">
        <p>优势项目：{visibleStrengths.length > 0 ? visibleStrengths.map(strengthName).join("、") : "暂无"}</p>
        <ul>{value.suggestions.map((suggestion) => <li key={suggestion}>{suggestion}</li>)}</ul>
        <ul aria-label="历史比较">
          {value.rules.test_ids.map((testId) => {
            const comparison = value.comparisons[testId]
            return (
              <li key={testId}>
                {comparison ? (
                  <>{TALENT_TEST_NAMES[testId]}：原始值变化 {comparison.raw_delta}，评分变化 {comparison.score_delta}</>
                ) : testId === "dynamic-vision" ? (
                  <>动态视力：暂不可比较；仅比较同难度、同引擎、同评分、同标称参数、同字号、同区域及同输入环境且双方均无质量警告的已完成结果。</>
                ) : (
                  <>{TALENT_TEST_NAMES[testId]}：暂不可比较；仅比较同引擎、同评分、同语言、同输入方式、同几何配置且双方均无质量警告的已完成结果。</>
                )}
              </li>
            )
          })}
        </ul>
        {value.mixed_devices ? <p>本轮使用了混合设备；各项仅按自身设备条件解释。</p> : null}
      </section>

      <section className="talent-rule-basis" aria-label="规则依据">
        <h2>规则依据</h2>
        <p>目录 {value.rules.catalog_version} · 评分 {value.rules.scoring_version} · 报告 {value.rules.report_version}</p>
        <ul>
          {value.rules.test_ids.map((testId) => {
            const rule = value.rules.tests[testId]
            if (testId === "dynamic-vision" && isDynamicVisionRule(rule)) {
              return (
                <li key={testId}>
                  动态视力：{rule.source_label}；基础分每题 {rule.base_points_per_correct} 分，难度系数按五级规则表。
                </li>
              )
            }
            if (!isAnchorRule(rule)) return null
            return (
              <li key={testId}>
                {TALENT_TEST_NAMES[testId]}：{rule.source_label}；锚点 {rule.anchors.map((raw, index) => `${[0, 25, 50, 70, 90, 100][index]}分：${primaryRaw(testId, raw)}`).join(" / ")}
              </li>
            )
          })}
        </ul>
      </section>
      <p className="talent-disclaimer">{value.disclaimer}</p>
    </div>
  )
}
