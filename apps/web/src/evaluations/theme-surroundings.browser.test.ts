// @vitest-environment node
import { readFileSync } from "node:fs"
import { chromium, type Browser, type Page } from "@playwright/test"
import { afterAll, beforeAll, describe, expect, it } from "vitest"

const styleFiles = [
  "styles/tokens.css", "styles/global.css", "talentTests/talentTests.css",
  "assessmentRankings/rankings.css", "esportsAssessments/assessment.css",
  "esportsAssessments/reports.css", "evaluations/evaluations.css",
  "evaluations/system-evaluation.css", "evaluations/teaching-reports.css",
  "evaluations/evaluation-foundation.css", "evaluations/camp-surveys.css",
]
const css = styleFiles.map((file) => readFileSync(new URL(`../${file}`, import.meta.url), "utf8")).join("\n")

// Isolated layout fixtures use production selectors and CSS, without API data or a live game.
const fixture = `
<main style="padding:16px;display:grid;gap:24px;min-width:0">
  <section class="assessmentHome"><header class="assessmentHero"><p class="assessmentEyebrow">ASSESSMENT</p><h1>电竞能力评测</h1><p>完成测试后查看评测报告</p><div class="assessmentActions"><a href="#reports">历史报告</a></div></header>
    <div class="assessmentCatalog">${[1, 2].map((n) => `<article class="assessmentSuiteCard"><div class="assessmentSuiteHeading"><h2>评测套组 ${n}</h2><span data-state="available">可用</span></div><p>说明与练习准备</p><button>开始评测</button></article>`).join("")}</div></section>
  <section class="talent-page"><header class="talent-page-heading"><h1>天赋测试</h1><button>历史记录</button></header><div class="talent-session-panel"><h2>当前测试进度</h2><div class="talent-catalog-grid"><article class="talent-test-card"><h2>反应能力</h2><p>请先阅读说明</p></article></div></div><section class="talent-readable-reports"><form role="search"><label>搜索授权人员<input type="search"></label><button>搜索</button></form></section></section>
  <section class="assessmentReports" id="reports"><article class="assessmentReportsCard"><h2>评测报告</h2><dl class="assessmentReportsMetrics"><div><dt>完成次数</dt><dd>12</dd></div><div><dt>综合得分</dt><dd>86</dd></div></dl><div class="assessmentReportsTabs"><button aria-selected="true">综合结果</button><button>单项结果</button></div></article></section>
  <section class="assessmentRanking"><h2>排行榜</h2><div class="assessmentRankingSummary"><p>本周</p><p>有效成绩</p></div><div class="assessmentRankingTableWrap"><table><thead><tr><th>排名</th><th>学员</th><th>得分</th></tr></thead><tbody><tr><td>1</td><td>演示学员</td><td>86</td></tr></tbody></table></div><button>下一页</button></section>
  <section class="evaluationPage"><div class="evaluationBranchGrid">${[1, 2, 3].map(() => `<article class="evaluationBranch active"><div><h2>教学评价</h2><p>教学报告与反馈</p></div><a href="#review">进入审核</a></article>`).join("")}</div></section>
  <section class="systemEvaluationDashboard"><div class="systemEvaluationMetricGrid"><article class="systemEvaluationMetric"><span>待审核</span><strong>12</strong><a href="#review">查看任务</a></article><article class="systemEvaluationMetric"><span>已发布</span><strong>28</strong><a href="#review">查看结果</a></article></div></section>
  <section class="teachingReportPage teachingReportReviewPage" id="review"><header class="teachingReportHeader"><h1>教学评语审核</h1></header><div class="teachingReportWorkspace"><div class="teachingReportQueue"><article class="teachingReportQueueCard selected"><h2>待审核报告</h2><button class="teachingReportQueueSelect">查看内容</button></article></div><article class="teachingReportDetail"><form class="teachingReportReviewForm"><label>审核意见<textarea id="review-text">保留正在输入的意见</textarea></label><fieldset><legend>审核决定</legend><label><input type="radio" name="decision" checked>通过并发布</label></fieldset><button>提交审核</button></form></article></div></section>
  <section class="foundationPage"><header class="foundationPageHeader"><h1>基础数据管理</h1><button class="foundationPrimaryButton">添加配置</button></header><div class="foundationToolbar"><label>筛选<input value="示例"></label><button class="foundationIconTextButton">刷新</button></div><form class="foundationForm"><label>账号<input value="示例账号"></label><button class="foundationPrimaryButton">保存</button></form></section>
  <section class="campSurveyPage"><h1>营期问卷</h1><form class="campSurveyForm"><fieldset><legend>本期反馈</legend><label>总体评价<select><option>满意</option></select></label><label>建议<textarea>演示反馈</textarea></label><label class="campSurveyCheckboxLabel"><input type="checkbox">确认提交</label></fieldset><div class="campSurveyActions"><button>提交问卷</button></div></form></section>
</main>`

let browser: Browser
let page: Page
beforeAll(async () => {
  browser = await chromium.launch({ headless: true })
  page = await browser.newPage()
}, 20_000)
afterAll(async () => { await browser?.close() })

async function render(theme: "clear" | "command", width: number) {
  await page.setViewportSize({ width, height: 900 })
  await page.setContent(`<!doctype html><html data-ui-theme="${theme}"><head><meta charset="utf-8"><style>${css}</style></head><body>${fixture}</body></html>`)
}

describe("assessment/evaluation theme surroundings in Chromium", () => {
  it.each([320, 390, 768, 1440])("keeps both themes contained and controls reachable at %ipx", async (width) => {
    for (const theme of ["clear", "command"] as const) {
      await render(theme, width)
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
      expect(overflow, `${theme} document overflow at ${width}px`).toBeLessThanOrEqual(1)
      for (const selector of [".foundationIconTextButton", ".foundationForm input", ".campSurveyCheckboxLabel", ".assessmentActions a", ".assessmentRanking button", ".talent-page input"]) {
        const box = await page.locator(selector).boundingBox()
        expect(box?.height, `${theme} ${selector} touch target`).toBeGreaterThanOrEqual(44)
      }
      expect(await page.locator(".teachingReportWorkspace").evaluate((element) => getComputedStyle(element).gridTemplateColumns.split(" ").length)).toBe(width <= 768 ? 1 : 2)
      if (process.env.TASK7_VISUAL_DIR && (width === 320 || width === 1440)) {
        await page.screenshot({ path: `${process.env.TASK7_VISUAL_DIR}/task-7-${theme}-${width}.png`, fullPage: true })
      }
    }
  })

  it("uses connected compact metrics in command while retaining report input and focus", async () => {
    await render("clear", 1440)
    const airy = await page.locator(".systemEvaluationMetricGrid").evaluate((element) => parseFloat(getComputedStyle(element).gap))
    const clearPadding = await page.locator(".teachingReportDetail").evaluate((element) => parseFloat(getComputedStyle(element).paddingTop))
    await page.locator("#review-text").fill("尚未提交的审核意见")
    await page.locator("#review-text").focus()
    await page.evaluate(() => { document.documentElement.dataset.uiTheme = "command" })
    expect(await page.locator(".systemEvaluationMetricGrid").evaluate((element) => parseFloat(getComputedStyle(element).gap))).toBe(0)
    expect(airy).toBeGreaterThan(0)
    expect(await page.locator(".teachingReportDetail").evaluate((element) => parseFloat(getComputedStyle(element).paddingTop))).toBeLessThan(clearPadding)
    expect(await page.locator("#review-text").inputValue()).toBe("尚未提交的审核意见")
    expect(await page.locator("#review-text").evaluate((element) => document.activeElement === element)).toBe(true)
    const outline = await page.locator("#review-text").evaluate((element) => getComputedStyle(element).outlineStyle)
    expect(outline).not.toBe("none")
  })

  it.each(["clear", "command"] as const)("keeps long upload/admin dialogs bounded and dismissible in %s", async (theme) => {
    await render(theme, 320)
    await page.locator("body").evaluate((body) => {
      body.insertAdjacentHTML("beforeend", `<div class="foundationDialogBackdrop"><section class="foundationDialog" role="dialog" aria-modal="true"><header><h2>配置上传与资料审核</h2><button aria-label="关闭">×</button></header><form class="foundationForm"><label>上传文件<input type="file"></label><label>备注<textarea rows="40">较长的表单内容</textarea></label><div class="foundationDialogActions"><button>取消</button><button type="submit">保存</button></div></form></section></div>`)
    })
    const dialog = await page.getByRole("dialog").boundingBox()
    expect(dialog?.x).toBeGreaterThanOrEqual(0)
    expect((dialog?.x ?? 0) + (dialog?.width ?? 0)).toBeLessThanOrEqual(320)
    expect(dialog?.height).toBeLessThanOrEqual(900)
    const close = await page.getByRole("button", { name: "关闭", exact: true }).boundingBox()
    expect(close?.height).toBeGreaterThanOrEqual(44)
    expect(close?.width).toBeGreaterThanOrEqual(44)
    const save = page.getByRole("dialog").getByRole("button", { name: "保存", exact: true })
    await save.scrollIntoViewIfNeeded()
    expect(await save.isVisible()).toBe(true)
  })
})
