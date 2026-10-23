import { expect, test } from "@playwright/test"

import {
  expectAboveMobileNavigation,
  expectHorizontallyInsideViewport,
  expectNoHorizontalOverflow,
  expectResponsiveRecordLabels,
  loginAsAdmin,
  loginAsCustomerService,
  loginAsGuardian,
  loginAsStudent,
  logout,
  openNavigationItem,
} from "./helpers"

function viewportWidth(page: Parameters<typeof loginAsAdmin>[0]): number {
  return page.viewportSize()?.width ?? 1440
}

test("admin manages evaluation foundation", async ({ page }) => {
  const width = viewportWidth(page)
  const roleKey = `e2e_viewport_${width}`
  const roleName = `E2E视口营务${width}`
  const limitReason = `E2E ${width} 视口账号上限验收`
  const phoneSuffix = String(width).padStart(8, "0")
  const firstPhone = `139${phoneSuffix}`
  const secondPhone = `137${phoneSuffix}`
  const firstMasked = `${firstPhone.slice(0, 3)}****${firstPhone.slice(-4)}`
  const secondMasked = `${secondPhone.slice(0, 3)}****${secondPhone.slice(-4)}`

  await loginAsAdmin(page)
  await page.goto("/evaluations/admin")
  await expect(page.getByRole("heading", { name: "基础数据管理" })).toBeVisible()
  await expect(page.getByRole("region", { name: "工作人员服务" })).toBeVisible()
  await page.getByLabel("营期").selectOption({ label: "E2E招生暑期营" })

  await page.getByRole("button", { name: "新增工作人员角色" }).click()
  let dialog = page.getByRole("dialog", { name: "新增工作人员角色" })
  await dialog.getByLabel("角色标识").fill(roleKey)
  await dialog.getByLabel("角色名称").fill(roleName)
  await dialog.getByLabel("角色类别").selectOption("operations")
  await dialog.getByRole("button", { name: "确认创建" }).click()
  await expect(page.getByText(roleName, { exact: true })).toBeVisible()

  await page.getByRole("button", { name: "新增工作人员服务" }).click()
  dialog = page.getByRole("dialog", { name: "新增工作人员服务" })
  await expect(dialog).toBeVisible()
  await dialog.getByLabel("搜索工作人员").fill("E2E生活老师")
  dialog = page.getByRole("dialog", { name: "新增工作人员服务" })
  await expect(dialog.getByRole("option", { name: "E2E生活老师" })).toHaveCount(1)
  const assignmentSelects = dialog.locator("select")
  await assignmentSelects.nth(0).selectOption({ label: "E2E生活老师" })
  await assignmentSelects.nth(1).selectOption({ label: roleName })
  await assignmentSelects.nth(2).selectOption("project")
  await assignmentSelects.nth(3).selectOption({ label: "英雄联盟" })
  await dialog.getByLabel("开始日期").fill("2026-01-01")
  await dialog.getByRole("button", { name: "确认创建" }).click()

  let assignmentRow = page.locator("tbody tr").filter({ hasText: roleName })
  await expect(assignmentRow).toHaveCount(1)
  await expect(page.getByRole("region", { name: "工作人员服务" }).getByRole("table")).toBeVisible()
  await expectResponsiveRecordLabels(assignmentRow, [
    "工作人员",
    "角色",
    "范围",
    "状态/原因",
    "操作",
  ])
  await expect(assignmentRow).toContainText("进行中")
  await assignmentRow.getByRole("button", { name: "结束 E2E生活老师 的服务" }).click()
  dialog = page.getByRole("dialog", { name: "结束工作人员服务" })
  await dialog.getByLabel("结束原因").fill(`E2E ${width} 视口结束服务`)
  await dialog.getByRole("button", { name: "确认结束" }).click()
  assignmentRow = page.locator("tbody tr").filter({ hasText: roleName })
  await expect(assignmentRow).toContainText(`E2E ${width} 视口结束服务`)
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".evaluationAdminPage")
  await expectAboveMobileNavigation(page, ".foundationTableWrap")

  await page.getByRole("tab", { name: "学员匹配" }).click()
  await page.getByLabel("搜索学员").fill(`E2E招生学员${width}`)
  await page.getByRole("button", { name: "搜索" }).click()
  await page
    .getByRole("button", { name: new RegExp(`^选择 E2E招生学员${width} ·`) })
    .click()
  await expect(page.getByRole("heading", { name: `E2E招生学员${width}` })).toBeVisible()
  await page.getByRole("button", { name: "调整账号上限" }).click()
  dialog = page.getByRole("dialog", { name: "调整游戏账号上限" })
  await dialog.getByRole("spinbutton", { name: "账号上限" }).fill("4")
  await dialog.getByRole("textbox", { name: "调整原因" }).fill(limitReason)
  await dialog.getByRole("button", { name: "确认调整" }).click()
  await expect(page.getByText("当前有效 0 / 4 个")).toBeVisible()

  await page.getByRole("button", { name: "添加辅助手机号" }).click()
  dialog = page.getByRole("dialog", { name: "添加辅助手机号" })
  await dialog.getByRole("textbox", { name: "手机号", exact: true }).fill(firstPhone)
  await dialog.getByRole("button", { name: "确认添加" }).click()
  await expect(page.getByText(firstMasked, { exact: true })).toBeVisible()
  await page.getByRole("button", { name: `替换 ${firstMasked}` }).click()
  dialog = page.getByRole("dialog", { name: "替换辅助手机号" })
  await dialog.getByRole("textbox", { name: "新手机号" }).fill(secondPhone)
  await dialog.getByRole("textbox", { name: "替换原因" }).fill(`E2E ${width} 视口号码替换`)
  await dialog.getByRole("button", { name: "确认替换" }).click()
  await expect(page.getByText(secondMasked, { exact: true })).toBeVisible()
  const phoneRow = page.locator("tbody tr").filter({ hasText: secondMasked })
  await expectResponsiveRecordLabels(phoneRow, ["手机号", "状态", "操作"])
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".studentMatchingPanel")
  await expectAboveMobileNavigation(page, ".matchingSection")

  await page.getByRole("tab", { name: "分析配置" }).click()
  const analysisPanel = page.getByRole("region", { name: "分析配置" })
  await analysisPanel.locator("select").first().selectOption({ label: "英雄联盟" })
  await expect(page.getByRole("heading", { name: "英雄联盟分析配置" })).toBeVisible()
  const currentProfile = analysisPanel.getByRole("article").filter({
    hasText: "当前版本 V1",
  })
  await expect(currentProfile).toContainText("当前版本 V1")
  await expect(currentProfile).toContainText("人工分析")
  await expect(currentProfile).toContainText("审核后发布")
  await expect(currentProfile.getByText("模式", { exact: true })).toBeVisible()
  await expect(currentProfile.getByText("发布方式", { exact: true })).toBeVisible()

  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".evaluationAdminPage")
  await expectAboveMobileNavigation(page, ".analysisProfilesPanel")
})

test("student and unauthorized roles see only allowed actions", async ({ page }) => {
  const width = viewportWidth(page)
  const gameId = `E2E-VIEWPORT-${width}`
  const unbindReason = `E2E ${width} 视口自行解绑`

  await loginAsStudent(page)
  await openNavigationItem(page, "评价中心")
  await page.getByRole("link", { name: "我的游戏账号" }).click()
  await expect(page.getByRole("heading", { name: "我的游戏账号" })).toBeVisible()
  await page.getByRole("button", { name: "添加游戏账号" }).click()
  let dialog = page.getByRole("dialog", { name: "添加游戏账号" })
  await dialog.getByLabel("项目").selectOption({ label: "无畏契约" })
  await dialog.getByLabel("游戏账号 ID").fill(gameId)
  await dialog.getByRole("button", { name: "确认绑定" }).click()

  let accountRow = page.locator("tbody tr").filter({ hasText: gameId })
  await expect(accountRow).toHaveCount(1)
  await expect(page.getByRole("table")).toBeVisible()
  await expectResponsiveRecordLabels(accountRow, [
    "项目",
    "游戏账号 ID",
    "状态",
    "解绑原因",
    "操作",
  ])
  await expect(accountRow).toContainText("有效")
  await accountRow.getByRole("button", { name: `解绑 ${gameId}` }).click()
  dialog = page.getByRole("dialog", { name: "解绑游戏账号" })
  await dialog.getByLabel("解绑原因").fill(unbindReason)
  await dialog.getByRole("button", { name: "确认解绑" }).click()
  accountRow = page.locator("tbody tr").filter({ hasText: gameId })
  await expect(accountRow).toContainText("已解绑")
  await expect(accountRow).toContainText(unbindReason)

  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".gameAccountsPage")
  await expectAboveMobileNavigation(page, ".foundationTableWrap")

  await logout(page)
  await loginAsGuardian(page)
  await openNavigationItem(page, "评价中心")
  await expect(page.getByRole("heading", { name: "评价中心" })).toBeVisible()
  const systemEvaluationCard = page.getByRole("article").filter({ hasText: "系统评价" })
  await expect(systemEvaluationCard.getByRole("link", { name: "打开系统评价" })).toBeVisible()
  await expect(page.getByRole("link", { name: "我的游戏账号" })).toHaveCount(0)
  await page.goto("/evaluations/game-accounts")
  await expect(page).not.toHaveURL(/\/evaluations\/game-accounts$/)
  await expect(page.getByRole("button", { name: "添加游戏账号" })).toHaveCount(0)

  await logout(page)
  await loginAsCustomerService(page)
  await expect(page.getByRole("link", { name: "评价中心" })).toHaveCount(0)
  await page.goto("/evaluations")
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole("heading", { name: "工作台" })).toBeVisible()
})
