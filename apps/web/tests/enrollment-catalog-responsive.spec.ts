import { expect, test } from "@playwright/test"

import {
  expectAboveMobileNavigation,
  expectHorizontallyInsideViewport,
  expectNoHorizontalOverflow,
  loginAsStudent,
} from "./helpers"

const PROGRAMS = [
  ["周末半天班", "¥800"],
  ["周末全天班", "¥1,500"],
  ["七日体验营", "¥6,999"],
  ["月度体验营", "¥12,888"],
  ["季度体验营", "¥32,888"],
  ["家庭教育及亲子关系修复课程", "¥3,000"],
  ["普通暑期班", "¥11,888"],
  ["职业班冲刺班", "¥13,888"],
  ["暑期进阶班", "¥14,888"],
] as const

test("student selects a priced program and reads its details on every viewport", async ({
  page,
}) => {
  await loginAsStudent(page)
  await page.getByRole("link", { name: "学员报名", exact: true }).click()

  await expect(page.getByRole("heading", { name: "学员报名", exact: true })).toBeVisible()
  await expect(page.getByRole("radio", { name: "选择此班型" })).toHaveCount(9)
  await expect(page.getByLabel("游戏项目").locator("option")).toHaveCount(4)
  for (const [name, price] of PROGRAMS) {
    const card = page.locator(".enrollmentProgramGrid article", {
      has: page.getByRole("heading", { name, exact: true }),
    })
    await expect(card).toBeVisible()
    await expect(card.getByText(price, { exact: true })).toBeVisible()
  }

  await page.getByRole("radio", { name: "选择此班型" }).first().check()
  const submitBand = page.locator(".enrollmentSubmitBand")
  await expect(submitBand.getByText("¥800", { exact: true })).toBeVisible()
  await expect(submitBand.getByRole("button", { name: "提交报名申请" })).toBeEnabled()
  await expect(page.getByRole("radio", { name: "线下付款" })).toBeChecked()
  await expect(page.getByRole("radio", { name: "二维码支付" })).toBeDisabled()

  await page.getByRole("button", { name: "详细介绍" }).first().click()
  const dialog = page.getByRole("dialog", { name: "周末半天班" })
  await expect(dialog.getByRole("heading", { name: "课程介绍" })).toBeVisible()
  await expect(dialog.getByRole("heading", { name: "适合学员" })).toBeVisible()
  await expect(dialog.getByRole("heading", { name: "课程内容" })).toBeVisible()
  await expect(dialog.getByRole("table", { name: "周末半天班课程安排" })).toBeVisible()
  await expectHorizontallyInsideViewport(page, ".enrollmentDialog")
  await dialog.getByRole("button", { name: "关闭详细介绍" }).click()

  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".enrollmentProgramGrid article")
  await expectAboveMobileNavigation(page, ".enrollmentSubmitBand")
})
