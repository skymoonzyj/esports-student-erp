import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { readFileSync } from "node:fs"
import { afterEach, expect, it, vi } from "vitest"

import type { ReportView } from "./contracts"
import { TalentReportPage } from "./TalentReportPage"

const report: ReportView = {
  id: "report-one",
  person: { id: "person-one", name: "测试学员", nickname: "小测" },
  session_id: "session-one",
  completed_at: "2026-09-20T02:00:00Z",
  rules: {
    catalog_version: "talent-training-v1",
    scoring_version: "talent-score-v1",
    report_version: "talent-report-v1",
    test_ids: ["reactiontime", "aim", "sequence", "number-memory", "verbal-memory", "chimp", "memory"],
    tests: {
      reactiontime: { engine_version: "reactiontime-clone-v1", anchors: [500, 275, 225, 175, 150, 100], source_label: "公开训练规则", unit: "ms" },
      aim: { engine_version: "aim-clone-v1", anchors: [1000, 550, 450, 350, 300, 250], source_label: "公开训练规则", unit: "ms" },
      sequence: { engine_version: "sequence-clone-v1", anchors: [0, 8, 10, 18, 25, 35], source_label: "公开训练规则", unit: "level" },
      "number-memory": { engine_version: "number-memory-clone-v1", anchors: [0, 7, 9, 13, 16, 20], source_label: "公开训练规则", unit: "digits" },
      "verbal-memory": { engine_version: "verbal-memory-clone-v1", anchors: [0, 20, 50, 100, 200, 300], source_label: "内部试行训练目标", unit: "count" },
      chimp: { engine_version: "chimp-clone-v1", anchors: [0, 9, 11, 15, 20, 25], source_label: "公开训练规则", unit: "count" },
      memory: { engine_version: "memory-clone-v1", anchors: [0, 6, 10, 18, 25, 35], source_label: "内部试行训练目标", unit: "level" },
    },
  },
  results: [
    result("reactiontime", 188, "ms", 95, "exceptional", ["timing_anomaly", "unknown_input", "background_interrupted"], { samples_ms: [0, 200, 210, 220, 230], too_soon_count: 0 }, "mouse"),
    result("aim", 420, "ms", 80, "high", [], { intervals_ms: Array(30).fill(420), miss_count: 2, total_elapsed_ms: 12600 }, "touch"),
    result("sequence", 9, "level", 55, "medium", [], { reached_level: 10, completed_level: 9, failed_step: 3, end_reason: "wrong_tile" }, "mouse"),
    result("number-memory", 6, "digits", 30, "low", [], { correct_digits: 6, failed_digits: 7, display_duration_ms: 6000 }, "mouse"),
    result("verbal-memory", 210, "count", 92, "exceptional", [], { correct_count: 210, error_count: 4, total_count: 214, word_bank_version: "zh-192-v1" }, "mouse"),
    result("chimp", 12, "count", 65, "medium", ["future_server_warning"], { reached_count: 13, completed_count: 12, strikes: 3, end_reason: "strikes" }, "pen"),
  ],
  strengths: ["aim"],
  suggestions: ["分次练习，关注稳定性。"],
  comparisons: {
    aim: {
      baseline_report_id: "baseline-aim",
      raw_delta: -30,
      score_delta: 5,
    },
  },
  mixed_devices: true,
  disclaimer: "结果仅用于训练参考，不代表智力、排名或职业潜力。",
}

afterEach(() => vi.unstubAllGlobals())

it("展示原始值、到达与通过差异、四档与异常复测，不渲染敏感身份字段", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify(report), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })))

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <TalentReportPage accountId="account-reader" reportId="report-one" />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByRole("heading", { name: "训练参考报告", level: 1 })).toBeVisible()
  expect(screen.getByRole("heading", { name: "测试学员（小测）", level: 2 })).toBeVisible()
  const reaction = screen.getByRole("row", { name: /反应速度/ })
  expect(within(reaction).getByText("188毫秒")).toBeVisible()
  expect(within(reaction).getByText("试算分：95，建议复测")).toBeVisible()
  expect(within(reaction).getByText("抢点 0 次")).toBeVisible()
  expect(screen.getByText("轮次标识：session-one")).toBeVisible()
  expect(within(reaction).getByText("需复测")).toBeVisible()
  expect(within(reaction).getByText(/计时数据存在异常/)).toBeVisible()
  expect(within(reaction).getByText(/未能确认输入设备/)).toBeVisible()
  expect(within(reaction).getByText(/测试期间页面进入后台或切换/)).toBeVisible()
  expect(within(reaction).queryByText(/timing_anomaly|unknown_input|background_interrupted/)).not.toBeInTheDocument()
  const aim = screen.getByRole("row", { name: /瞄准速度/ })
  expect(within(aim).getByText("较高")).toBeVisible()
  expect(within(aim).queryByText("需复测")).not.toBeInTheDocument()
  expect(within(aim).getByText(/触屏输入/)).toBeVisible()
  expect(within(aim).getByText("未命中 2 次")).toBeVisible()
  expect(screen.getByText("累计正确判断210次")).toBeVisible()
  expect(screen.getByText(/重复词判断也计次，不代表不同词数量/)).toBeVisible()
  expect(screen.getByText(/0分：500毫秒.*70分：175毫秒.*100分：100毫秒/)).toBeVisible()
  const sequence = screen.getByRole("row", { name: /顺序记忆/ })
  expect(within(sequence).getByText(/到达 10 级，完成 9 级/)).toBeVisible()
  expect(within(screen.getByRole("row", { name: /黑猩猩测试/ })).getByText("需复测")).toBeVisible()
  expect(screen.getByText("较低")).toBeVisible()
  expect(screen.getByText("中等")).toBeVisible()
  expect(screen.getByText("超高")).toBeVisible()
  const comparisons = screen.getByRole("list", { name: "历史比较" })
  expect(within(comparisons).getAllByRole("listitem")).toHaveLength(7)
  expect(within(comparisons).getByText(/瞄准速度：原始值变化 -30，评分变化 5/)).toBeVisible()
  expect(within(comparisons).getByText(/反应速度：暂不可比较/)).toHaveTextContent(
    /同引擎、同评分、同语言、同输入方式、同几何配置且双方均无质量警告/,
  )
  expect(screen.getByText(/未识别的质量提示，请联系管理员确认后复测/)).toBeVisible()
  expect(screen.queryByText(/future_server_warning/)).not.toBeInTheDocument()
  expect(screen.getByText(/混合设备/)).toBeVisible()
  expect(screen.queryByText(/手机号|身份证|138\d|110105/)).not.toBeInTheDocument()
  expect(screen.queryByText(/总分|全球排名|百分位|雷达/)).not.toBeInTheDocument()
})

it.each(["mouse", "touch"] as const)("qualifies all device-sensitive strengths for %s", async (inputKind) => {
  const value = structuredClone(report)
  value.strengths = ["reactiontime", "aim", "chimp"]
  value.results.forEach((item) => { item.device.input_kind = inputKind; item.quality_flags = [] })
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } })))
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><TalentReportPage accountId="reader" reportId="one" /></QueryClientProvider></MemoryRouter>)
  const summary = await screen.findByRole("region", { name: "报告结论" })
  expect(within(summary).getByText(/优势项目：/)).toHaveTextContent("反应速度（本次操作环境下的表现）、瞄准速度（本次操作环境下的表现）、黑猩猩测试（本次操作环境下的表现）")
})

it("uses Chinese task-specific primary results and retains visual error statistics", async () => {
  const value = structuredClone(report)
  value.results = [
    result("reactiontime", 175, "ms", 70, "high", [], { samples_ms: [175, 175, 175, 175, 175], too_soon_count: 2 }, "mouse"),
    result("aim", 350, "ms", 70, "high", [], { intervals_ms: Array(30).fill(350), miss_count: 3, total_elapsed_ms: 10500 }, "mouse"),
    result("sequence", 18, "level", 70, "high", [], { reached_level: 18, completed_level: 17, failed_step: 1, end_reason: "wrong_tile" }, "mouse"),
    result("number-memory", 13, "digits", 70, "high", [], { correct_digits: 13, failed_digits: 14, display_duration_ms: 12200 }, "mouse"),
    result("verbal-memory", 200, "count", 90, "exceptional", [], { correct_count: 200, error_count: 3, total_count: 203, word_bank_version: "zh-192-v1" }, "mouse"),
    result("chimp", 15, "count", 70, "high", [], { reached_count: 15, completed_count: 14, strikes: 3, end_reason: "strikes" }, "mouse"),
    result("memory", 18, "level", 70, "high", [], { reached_level: 18, completed_level: 17, accepted_misses: 11, lives_used: 3, end_reason: "lives" }, "mouse"),
  ]
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } })))
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><TalentReportPage accountId="reader" reportId="one" /></QueryClientProvider></MemoryRouter>)
  const table = await screen.findByRole("table", { name: "七项训练结果" })
  for (const primary of ["175毫秒", "350毫秒", "正确13位", "累计正确判断200次", "到达15个数字难度"]) {
    expect(within(table).getByText(primary)).toBeVisible()
  }
  expect(within(table).getAllByText("到达18关")).toHaveLength(2)
  expect(within(table).getByText(/有效误点 11 次/)).toBeVisible()
  expect(within(table).queryByText(/\b(ms|level|digits|words|count)\b/)).not.toBeInTheDocument()
})

it("报告刷新返回 403 时清除当前账号的敏感缓存", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
    type: "about:blank",
    title: "Forbidden",
    status: 403,
    detail: "权限已撤销",
    code: "forbidden",
    request_id: "report-revoked",
  }), { status: 403, headers: { "Content-Type": "application/problem+json" } })))
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const key = ["talent-tests", "account-reader", "report", "report-one"]
  queryClient.setQueryData(key, report)

  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <TalentReportPage accountId="account-reader" reportId="report-one" />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(screen.getByText("测试学员（小测）")).toBeVisible()
  expect(await screen.findByRole("alert")).toHaveTextContent("报告不存在或无权查看")
  expect(screen.queryByText("测试学员（小测）")).not.toBeInTheDocument()
  await waitFor(() => expect(queryClient.getQueryData(key)).toBeUndefined())
})

it("保留报告表格单元格布局并只在单元格内部纵向排列内容", () => {
  const css = readFileSync("src/talentTests/talentTests.css", "utf8")

  expect(css).not.toMatch(/\.talent-report-table td\s*\{[^}]*display:\s*grid/s)
  expect(css).toMatch(/\.talent-report-cell-stack\s*\{[^}]*display:\s*grid/s)
})

it("renders the dynamic vision formula, quality note, and frozen five-level rule table", async () => {
  const value = dynamicReport(4, ["correct", "correct", "correct", "correct", "skipped"], 72, [
    "display_duration_deviation",
  ])
  value.strengths = ["dynamic-vision"]
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
  })))
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><TalentReportPage accountId="reader" reportId="dynamic" /></QueryClientProvider></MemoryRouter>)

  expect(await screen.findByRole("table", { name: "八项训练结果" })).toBeVisible()
  const detail = screen.getByRole("region", { name: "动态视力详细报告" })
  const facts = detail.querySelector("dl")!
  expect(within(facts).getByText("第 4 级，答对 4/5 题")).toBeVisible()
  expect(within(facts).getByText("40")).toBeVisible()
  expect(within(facts).getByText("1.8")).toBeVisible()
  expect(within(facts).getByText("4 × 10 × 1.8 = 72")).toBeVisible()
  expect(within(facts).getByText("5 位")).toBeVisible()
  expect(within(facts).getByText("36 毫秒")).toBeVisible()
  expect(within(facts).getByText("跳过 1 题")).toBeVisible()
  expect(within(detail).getByText("显示时长偏差，建议复测")).toBeVisible()
  expect(screen.getByRole("table", { name: "动态视力五级规则" })).toHaveTextContent(
    "1 级3 位100 毫秒1.260 分",
  )
  expect(screen.getByRole("region", { name: "报告结论" })).toHaveTextContent("优势项目：暂无")
  expect(screen.queryByText(/动态视力：原始值变化/)).not.toBeInTheDocument()
  expect(screen.getByText(/动态视力：暂不可比较/)).toHaveTextContent(/同难度.*同标称参数.*同字号.*同区域/)
})

it("explains the level-one full-score cap without calling it a deficit", async () => {
  const value = dynamicReport(1, ["correct", "correct", "correct", "correct", "correct"], 60, [])
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
  })))
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><TalentReportPage accountId="reader" reportId="dynamic-low" /></QueryClientProvider></MemoryRouter>)

  const detail = await screen.findByRole("region", { name: "动态视力详细报告" })
  expect(within(detail).getByText("第 1 级，答对 5/5 题")).toBeVisible()
  expect(within(detail).getByText("5 × 10 × 1.2 = 60")).toBeVisible()
  expect(within(detail).getByText(/本级最高 60 分/)).toHaveTextContent("可在新一轮尝试更高难度")
  expect(within(detail).queryByText(/能力不足|缺陷|较差/)).not.toBeInTheDocument()
  expect(screen.queryByText(/平均|总天赋指数/)).not.toBeInTheDocument()
})

function result(
  test_id: ReportView["results"][number]["test_id"],
  raw_value: number,
  unit: string,
  score: number,
  grade: ReportView["results"][number]["grade"],
  quality_flags: string[],
  raw: ReportView["results"][number]["raw"],
  input_kind: ReportView["results"][number]["device"]["input_kind"],
): ReportView["results"][number] {
  return {
    test_id,
    raw_value,
    unit,
    score,
    grade,
    quality_flags,
    raw,
    device: { input_kind, viewport_width: 390, viewport_height: 844, geometry: null },
    duration_ms: 1000,
    background_interrupted: false,
    engine_version: `${test_id}-clone-v1`,
    scoring_version: "talent-score-v1",
    locale: "zh-CN",
  }
}

function dynamicReport(
  level: 1 | 2 | 3 | 4 | 5,
  outcomes: ("correct" | "incorrect" | "skipped")[],
  score: number,
  quality_flags: string[],
): ReportView {
  const correct = outcomes.filter((outcome) => outcome === "correct").length
  const levels = [
    { level: 1 as const, digit_count: 3 as const, nominal_display_ms: 100 as const, coefficient_tenths: 12 as const },
    { level: 2 as const, digit_count: 4 as const, nominal_display_ms: 100 as const, coefficient_tenths: 14 as const },
    { level: 3 as const, digit_count: 5 as const, nominal_display_ms: 100 as const, coefficient_tenths: 16 as const },
    { level: 4 as const, digit_count: 5 as const, nominal_display_ms: 36 as const, coefficient_tenths: 18 as const },
    { level: 5 as const, digit_count: 6 as const, nominal_display_ms: 36 as const, coefficient_tenths: 20 as const },
  ] as const
  return {
    ...structuredClone(report),
    id: `dynamic-${level}`,
    rules: {
      ...structuredClone(report.rules),
      catalog_version: "talent-training-v2",
      scoring_version: "talent-score-v2",
      report_version: "talent-report-v2",
      test_ids: [...report.rules.test_ids, "dynamic-vision"],
      tests: {
        ...structuredClone(report.rules.tests),
        "dynamic-vision": {
          kind: "dynamic_vision",
          engine_version: "dynamic-vision-v1",
          source_label: "内部试行训练规则",
          unit: "count",
          base_points_per_correct: 10,
          levels: [...levels],
        },
      },
    },
    results: [{
      test_id: "dynamic-vision",
      raw_value: correct,
      unit: "count",
      score,
      grade: score >= 90 ? "exceptional" : score >= 70 ? "high" : score >= 40 ? "medium" : "low",
      quality_flags,
      raw: {
        selected_level: level,
        outcomes: [...outcomes],
        measured_display_ms: [36, 36, 36, 36, 36],
        font_size_px: 56,
      },
      device: {
        input_kind: "mouse",
        viewport_width: 1280,
        viewport_height: 720,
        geometry: { width: 800, height: 420, target_diameter: null },
      },
      duration_ms: 13_000,
      background_interrupted: false,
      engine_version: "dynamic-vision-v1",
      scoring_version: "talent-score-v2",
      locale: "zh-CN",
    }],
    comparisons: {},
  } as ReportView
}
