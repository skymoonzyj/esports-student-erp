import { expect, test } from "@playwright/test"

import {
  assignRoleToPerson,
  bindGuardian,
  createMinorStudent,
  createPerson,
  expectNoHorizontalOverflow,
  loginAsAdmin,
  loginAsCustomerService,
  openPersonAccount,
} from "./helpers"

test("admin completes the minor guardian account lifecycle", async ({ page }) => {
  await loginAsAdmin(page)
  const guardian = await createPerson(page, "guardian")
  await openPersonAccount(page, guardian)
  await assignRoleToPerson(page, guardian, "监护人")

  // Exercise the real save/navigation flow with deterministic heading collisions.
  // A same-name H2 and a prefix-matching H1 must not replace the detail-page H1.
  await page.addInitScript(() => {
    if (window.location.pathname !== "/people/new") return
    const observer = new MutationObserver(() => {
      if (document.getElementById("person-heading-locator-regression")) return
      const heading = document.querySelector(".personDetailHeading h1")
      const name = heading?.textContent ?? ""
      if (!/^E2E-viewport-\d+-student-\d+$/.test(name)) return
      const section = document.createElement("section")
      section.id = "person-heading-locator-regression"
      section.setAttribute("aria-label", "人员标题定位回归副标题")
      const secondary = document.createElement("h2")
      secondary.textContent = name
      const related = document.createElement("h1")
      related.textContent = `${name}相关资料`
      section.append(secondary, related)
      document.body.append(section)
      observer.disconnect()
    })
    observer.observe(document, { childList: true, subtree: true })
  })
  const student = await createMinorStudent(page)
  await expect(page).toHaveURL(new RegExp(`/people/${student.id}$`))
  await expect(page.getByRole("heading", { name: student.name, level: 1, exact: true })).toBeVisible()
  const secondaryHeadings = page.getByRole("region", { name: "人员标题定位回归副标题" })
  await expect(secondaryHeadings.getByRole("heading", { name: student.name, level: 2, exact: true })).toBeVisible()
  await expect(secondaryHeadings.getByRole("heading", { name: `${student.name}相关资料`, level: 1, exact: true })).toBeVisible()
  await page.goto(`/people?name=${encodeURIComponent(student.name)}`)
  const pendingRow = page.getByRole("row").filter({
    has: page.getByRole("link", { name: student.name, exact: true }),
  })
  await expect(pendingRow).toContainText("未成年")
  await expect(pendingRow).toContainText("待绑定监护人")

  await bindGuardian(page, student, guardian)
  await expect(page.getByText("未成年 · 监护人已绑定")).toBeVisible()
  await openPersonAccount(page, student)

  await page.getByRole("button", { name: "结束监护关系" }).click()
  await page.getByLabel("结束原因").fill("E2E 监护安排调整")
  await page.getByRole("button", { name: "确认结束" }).click()
  await expect(page.getByText("已结束", { exact: true })).toBeVisible()
  await expect(page.getByText("结束原因：E2E 监护安排调整")).toBeVisible()

  await page.goto("/settings/accounts")
  const accountRow = page.getByRole("row").filter({ hasText: student.name })
  await expect(accountRow.getByText("已停用", { exact: true })).toBeVisible()
  await expectNoHorizontalOverflow(page)
})

test("customer service sees roster status without internal guardian diagnostics", async ({
  page,
}) => {
  await loginAsCustomerService(page)
  await page.goto(`/people?name=${encodeURIComponent("E2E待绑定学员")}`)
  const pendingRow = page.getByRole("row").filter({
    has: page.getByRole("link", { name: "E2E待绑定学员", exact: true }),
  })
  await expect(pendingRow).toContainText("未成年")
  await expect(pendingRow).toContainText("待绑定监护人")

  await pendingRow.getByRole("link", { name: "E2E待绑定学员", exact: true }).click()
  await expect(page.getByRole("heading", { name: "E2E待绑定学员" })).toBeVisible()
  await expect(page.getByText(/\*{8}/)).toBeVisible()
  await expect(page.getByText("未成年 · 待绑定监护人")).toBeVisible()
  await expect(page.getByRole("list", { name: "监护就绪诊断" })).toHaveCount(0)
  await expect(page.getByRole("link", { name: "前往账号管理" })).toHaveCount(0)
  await expect(page.getByRole("link", { name: "前往权限管理" })).toHaveCount(0)
  await expect(page.getByRole("button", { name: "结束监护关系" })).toHaveCount(0)
  await expect(page.getByRole("button", { name: "查看完整证件号码" })).toHaveCount(0)
  await expectNoHorizontalOverflow(page)
})

test("super-admin navigation stays inside the rail when the menu is taller than the viewport", async ({
  page,
}) => {
  test.skip(page.viewportSize()?.width !== 1024, "desktop rail regression")
  await loginAsAdmin(page)
  await page.setViewportSize({ width: 1024, height: 640 })

  const navigation = page.getByRole("navigation", { name: "主导航" })
  for (const groupName of ["人员中心", "餐饮统计", "财务结算"]) {
    await navigation.getByRole("link", { name: groupName, exact: true }).click()
  }

  const metrics = await navigation.evaluate((element) => {
    const rail = element.closest<HTMLElement>(".shellRail")
    if (!rail) throw new Error("Navigation rail is required")
    const railBox = rail.getBoundingClientRect()
    const navigationBox = element.getBoundingClientRect()
    return {
      navigationClientHeight: element.clientHeight,
      navigationScrollHeight: element.scrollHeight,
      navigationBottom: navigationBox.bottom,
      railBottom: railBox.bottom,
      railOverflow: getComputedStyle(rail).overflow,
      navigationOverflowY: getComputedStyle(element).overflowY,
    }
  })
  expect(metrics.navigationScrollHeight).toBeGreaterThan(metrics.navigationClientHeight)
  expect(metrics.navigationBottom).toBeLessThanOrEqual(metrics.railBottom)
  expect(metrics.railOverflow).toBe("hidden")
  expect(metrics.navigationOverflowY).toBe("auto")

  await navigation.evaluate((element) => {
    element.scrollTop = element.scrollHeight
  })
  const finalLink = navigation.getByRole("link", { name: "安全审计", exact: true })
  await expect(finalLink).toBeVisible()
  const finalLinkBox = await finalLink.boundingBox()
  const railBox = await page.locator(".shellRail").boundingBox()
  if (!finalLinkBox || !railBox) throw new Error("Desktop rail geometry is required")
  expect(finalLinkBox.y + finalLinkBox.height).toBeLessThanOrEqual(railBox.y + railBox.height)
})
