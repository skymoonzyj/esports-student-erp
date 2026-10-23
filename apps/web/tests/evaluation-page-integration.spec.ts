import { expect, test } from "@playwright/test"

import {
  expectAboveMobileNavigation,
  expectHorizontallyInsideViewport,
  expectNoHorizontalOverflow,
  loginAsAdmin,
  loginAsCustomerService,
  openNavigationItem,
} from "./helpers"

const e2eBaseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5175"
const scoreOrigin = process.env.E2E_SCORE_ORIGIN ?? "http://localhost:8010"

test("admin opens one ERP-authenticated score shell embedded and standalone", async ({
  page,
}) => {
  await loginAsAdmin(page)
  await openNavigationItem(page, "评价中心")
  await expect(page.getByRole("heading", { name: "评价中心" })).toBeVisible()
  await page.getByRole("link", { name: "打开系统评价" }).click()

  const frame = page.frameLocator('iframe[title="系统评价"]')
  await expect(frame.getByText("已连接竞学 ERP")).toBeVisible()
  await expect(frame.getByText("E2E平台管理员")).toBeVisible()
  await expect(frame.locator('input[type="file"]').first()).toBeDisabled()
  await expect(frame.getByText("GPU 服务尚未开放").first()).toBeVisible()
  await expect(frame.getByRole("heading", { name: "登录工作台" })).toHaveCount(0)

  const [standalone] = await Promise.all([
    page.waitForEvent("popup"),
    page.getByRole("link", { name: "单独打开" }).click(),
  ])
  await expect(standalone.getByText("已连接竞学 ERP")).toBeVisible()
  await expect(standalone.getByText("E2E平台管理员")).toBeVisible()
  await expect(standalone).toHaveURL(`${scoreOrigin}/`)

  const apiCookies = await page.context().cookies(
    `${e2eBaseUrl}/api/v1/auth/me`,
  )
  const embeddedCookies = await page.context().cookies(
    `${e2eBaseUrl}/evaluation-tool/`,
  )
  const standaloneCookies = await page.context().cookies(
    `${scoreOrigin}/`,
  )
  expect(apiCookies.map((cookie) => cookie.name)).toContain("erp_session")
  expect(embeddedCookies.map((cookie) => cookie.name)).not.toContain("erp_session")
  expect(embeddedCookies.map((cookie) => cookie.name)).toContain(
    "game_score_session_embedded",
  )
  expect(standaloneCookies.map((cookie) => cookie.name)).toEqual(
    expect.arrayContaining(["game_score_session_standalone"]),
  )
  expect(standaloneCookies.map((cookie) => cookie.name)).not.toContain(
    "erp_session",
  )

  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".systemEvaluationPage")
  await expectAboveMobileNavigation(page, ".evaluationFrameShell")
})

test("customer service has no evaluation entry by default", async ({ page }) => {
  await loginAsCustomerService(page)
  await expect(page.getByRole("link", { name: "评价中心" })).toHaveCount(0)
  await page.goto("/evaluations")
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole("heading", { name: "工作台" })).toBeVisible()
})
