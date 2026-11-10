import { expect, test } from "@playwright/test"
import type { APIRequestContext, Page } from "@playwright/test"

import type { DepartureReminders } from "../src/portal/departureApi"
import { e2eCredentialTuple } from "./e2eCredentials"
import { createAdminRequest, createRequestSession, expectNoHorizontalOverflow, loginWithIdentifier, logout, refocus } from "./helpers"

const ENDPOINT = "/api/v1/portal/departure-reminders"
const section = (page: Page) => page.getByRole("region", { name: "退宿／结营提醒", exact: true })
const activeSteps = new Map<Page, string>()
const windows = new Map<Page, string[]>()
test.use({ trace: "off", screenshot: "off", video: "off" })
test.describe.configure({ mode: "serial" })

async function step<T>(page: Page, name: string, action: () => Promise<T>): Promise<T> {
  activeSteps.set(page, name)
  return await test.step(name, action)
}

test.beforeEach(async ({ page }) => {
  windows.set(page, [])
  page.on("request", (request) => {
    const url = new URL(request.url())
    if (url.pathname === ENDPOINT) windows.get(page)?.push(url.searchParams.toString())
  })
})

test.afterEach(async ({ page }, info) => {
  if (info.status !== info.expectedStatus && !page.isClosed()) {
    console.log("Departure diagnostic " + JSON.stringify({
      step: activeSteps.get(page), pathname: new URL(page.url()).pathname,
      headings: (await page.locator("h1,h2").allTextContents()).slice(0, 6),
      departureWindows: windows.get(page),
      failureVisible: await page.getByText("退宿／结营提醒暂时无法更新", { exact: true }).isVisible(),
      pressedWindow: await page.getByRole("group", { name: "提醒日期范围" }).locator('button[aria-pressed="true"]').allTextContents(),
    }))
  }
  activeSteps.delete(page)
  windows.delete(page)
})

async function reminders(api: APIRequestContext, query = "window=three_days&page=1&page_size=50") {
  const response = await api.get(`${ENDPOINT}?${query}`)
  expect(response.status()).toBe(200)
  expect(response.headers()["cache-control"]).toContain("no-store")
  return await response.json() as DepartureReminders
}

test("real workbench merges sources, keeps names distinct, filters dates and paginates without writes", async ({ page }) => {
  await step(page, "登录", () => loginWithIdentifier(page, ...e2eCredentialTuple("admin")))
  const requests: string[] = []
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/")) requests.push(request.method())
  })
  const panel = section(page)
  const all = await step(page, "首屏", async () => {
    await expect(panel).toHaveCount(1)
    await expect(panel.getByRole("button", { name: /^近三日/ })).toHaveAttribute("aria-pressed", "true")
    return await reminders(page.request)
  })
  expect(all.available).toBe(true)
  expect(all.total).toBeGreaterThan(20)
  expect(new Set(all.items.map((row) => row.person_id)).size).toBe(all.total)
  expect(all.items.filter((row) => row.full_name === "E2E同名提醒")).toHaveLength(2)
  expect(all.items.find((row) => row.full_name === "E2E多来源提醒")?.events).toHaveLength(3)
  expect(all.items.find((row) => row.full_name === "E2E未激活提醒")?.events.map((event) => event.kind).sort()).toEqual(["enrollment_end", "planned_checkout"])
  const longTerm = all.items.find((row) => row.full_name === "E2E常住结营提醒")
  expect(longTerm?.events.map((event) => event.kind)).toEqual(["enrollment_end"])
  const completed = all.items.find((row) => row.full_name === "E2E独立已完成提醒")
  expect(completed?.events).toEqual([expect.objectContaining({ kind: "enrollment_end", enrollment_status: "completed" })])
  expect(all.items.some((row) => row.full_name === "E2E已退房不提醒")).toBe(false)
  await expect(panel.getByRole("article")).toHaveCount(20)
  await step(page, "翻页", async () => {
  const next = page.waitForResponse((response) => new URL(response.url()).pathname === ENDPOINT && new URL(response.url()).searchParams.get("page") === "2")
  await panel.getByRole("button", { name: "下一页提醒" }).click()
  const nextResponse = await next
  expect(nextResponse.status()).toBe(200)
  const second = await nextResponse.json() as DepartureReminders
  await expect(panel.getByRole("article")).toHaveCount(Math.min(20, all.total - 20))
  expect(second.items.map((row) => row.person_id)).toEqual(all.items.slice(20, 40).map((row) => row.person_id))
  expect(second.items.some((row) => all.items.slice(0, 20).some((first) => first.person_id === row.person_id))).toBe(false)
  })
  await step(page, "今日", async () => {
  const todayResponse = page.waitForResponse((response) => new URL(response.url()).pathname === ENDPOINT && new URL(response.url()).searchParams.get("window") === "today")
  await panel.getByRole("button", { name: /^今日/ }).click()
  const today = await (await todayResponse).json() as DepartureReminders
  expect(today.page).toBe(1)
  expect(today.total).toBe(all.today_count)
  expect(today.total).toBeLessThan(all.total)
  expect(today.items.every((row) => row.events.every((event) => event.date === today.business_date))).toBe(true)
  await expect(panel.getByText("教学已完成", { exact: true })).toHaveCount(0)
  })
  await step(page, "focus", async () => {
  const focused = page.waitForResponse((response) => new URL(response.url()).pathname === ENDPOINT && new URL(response.url()).searchParams.get("window") === "today")
  await refocus(page)
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")))
  expect((await focused).status()).toBe(200)
  await expect(panel.getByRole("button", { name: /^今日/ })).toHaveAttribute("aria-pressed", "true")
  })
  await step(page, "路由返回", async () => {
  const dashboard = page.url()
  await panel.getByRole("link", { name: /^查看计划退宿详情/ }).first().click()
  await expect(page).toHaveURL(/\/housing\/rooms\//)
  await expect(page.getByRole("heading", { level: 1, name: /^房间 / })).toBeVisible()
  await expect(panel).toHaveCount(0)
  const returned = page.waitForResponse((response) => new URL(response.url()).pathname === ENDPOINT && new URL(response.url()).searchParams.get("window") === "today")
  await page.goBack()
  await expect(page).toHaveURL(dashboard)
  expect((await returned).status()).toBe(200)
  await expect(panel.getByRole("button", { name: /^今日/ })).toHaveAttribute("aria-pressed", "true")
  })
  await panel.getByRole("button", { name: /^近三日/ }).click()
  await expect(panel.getByText("教学已完成", { exact: true })).toBeVisible()
  await expect(panel.getByRole("button", { name: /编辑|退房|确认|调房|删除|导出|完成保洁/ })).toHaveCount(0)
  expect(requests.every((method) => method === "GET")).toBe(true)
  await expectNoHorizontalOverflow(page)
})

test("real role scopes do not borrow another source or detail permission", async ({ page }) => {
  const cases = [
    ["customer_service", "E2E未激活提醒", "planned_checkout"],
    ["coach", "E2E教练关联提醒", "enrollment_end"],
    ["ready_male_student", "E2E队伍关联提醒", "planned_checkout"],
    ["life_teacher", "E2E邀请关联提醒", "planned_checkout"],
    ["student", "E2E自助学员", "planned_checkout"],
    ["guardian", "E2E已绑定学员", "planned_checkout"],
  ] as const
  for (const [key, name, kind] of cases) {
    const { api } = await createRequestSession(...e2eCredentialTuple(key))
    try {
      const data = await reminders(api)
      expect(data.available, key).toBe(true)
      expect(data.items.find((row) => row.full_name === name)?.events.some((event) => event.kind === kind), key).toBe(true)
      if (key === "customer_service") {
        expect(data.items.flatMap((row) => row.events).every((event) => event.kind === "planned_checkout" && event.building_name === "4幢")).toBe(true)
      }
      if (key === "ready_male_student" || key === "life_teacher") {
        expect(data.items.map((row) => row.full_name)).toEqual([name])
        expect(data.items.flatMap((row) => row.events).every((event) => event.kind === "planned_checkout" && event.detail_path === null)).toBe(true)
      }
    } finally { await api.dispose() }
  }
  await loginWithIdentifier(page, ...e2eCredentialTuple("ready_male_student"))
  await expect(section(page).getByRole("heading", { name: "E2E队伍关联提醒", exact: true })).toBeVisible()
  await expect(section(page).getByRole("link")).toHaveCount(0)
  await expect(section(page).getByText("营期结束", { exact: true })).toHaveCount(0)
  await expectNoHorizontalOverflow(page)
})

test("switching accounts removes the previous account's reminder list and hides unavailable module", async ({ page }) => {
  await loginWithIdentifier(page, ...e2eCredentialTuple("admin"))
  await expect(section(page).getByRole("heading", { name: "E2E未激活提醒", exact: true })).toBeVisible()
  await logout(page)
  await loginWithIdentifier(page, ...e2eCredentialTuple("student"))
  await expect(section(page).getByRole("heading", { name: "E2E自助学员", exact: true })).toBeVisible()
  await expect(section(page).getByText("E2E未激活提醒", { exact: true })).toHaveCount(0)
  const self = await reminders(page.request)
  expect(self.items.map((row) => row.full_name)).toEqual(["E2E自助学员"])
  await logout(page)
  await loginWithIdentifier(page, ...e2eCredentialTuple("other_guardian"))
  expect((await reminders(page.request)).available).toBe(false)
  await expect(section(page)).toHaveCount(0)
  await expectNoHorizontalOverflow(page)
})

test("ending a real guardian relationship clears its cached reminders on focus", async ({ page }) => {
  const width = page.viewportSize()?.width ?? 1440
  const index = { 1440: 1, 768: 2, 360: 3 }[width]
  if (!index) throw new Error("Departure runner must select the three contracted viewports")
  const personId = `ed600000-0000-4000-8000-${String(100 + index).padStart(12, "0")}`
  const relationshipId = `ed610000-0000-4000-8000-${String(index).padStart(12, "0")}`
  const name = `E2E撤权提醒${width}`
  await loginWithIdentifier(page, ...e2eCredentialTuple("guardian"))
  await expect(section(page).getByRole("heading", { name, exact: true })).toBeVisible()
  const { admin, csrfToken } = await createAdminRequest()
  try {
    const ended = await admin.post(`/api/v1/people/${personId}/guardians/${relationshipId}/end`, {
      headers: { "X-CSRF-Token": csrfToken }, data: { reason: "Synthetic departure focus revocation" },
    })
    expect(ended.status()).toBe(200)
  } finally { await admin.dispose() }
  const refreshed = page.waitForResponse((response) => new URL(response.url()).pathname === ENDPOINT && response.status() === 200)
  await refocus(page)
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")))
  const current = await (await refreshed).json() as DepartureReminders
  expect(current.items.some((row) => row.person_id === personId)).toBe(false)
  await expect(section(page).getByText(name, { exact: true })).toHaveCount(0)
  await expect(section(page).getByRole("heading", { name: "E2E已绑定学员", exact: true })).toBeVisible()
  await expectNoHorizontalOverflow(page)
})
