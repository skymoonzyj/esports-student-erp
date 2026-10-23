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

  const student = await createMinorStudent(page)
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
  await expect(page.getByRole("button", { name: "查看完整身份证" })).toHaveCount(0)
  await expectNoHorizontalOverflow(page)
})
