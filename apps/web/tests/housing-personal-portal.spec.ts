import { expect, test } from "@playwright/test"

import {
  createAdminRequest,
  expectAboveMobileNavigation,
  expectHorizontallyInsideViewport,
  expectNoHorizontalOverflow,
  expectPrivateHousingProjection,
  loginAsGuardian,
  loginAsStudent,
  logout,
  refocus,
} from "./helpers"

const REVOCATION_STUDENTS = {
  1440: {
    id: "e2000000-0000-4000-8000-000000000010",
    name: "E2E招生学员1440",
    relationshipId: "e2400000-0000-4000-8000-000000000001",
    room: "4-202",
  },
  1024: {
    id: "e2000000-0000-4000-8000-000000000020",
    name: "E2E招生学员1024",
    relationshipId: "e2400000-0000-4000-8000-000000000002",
    room: "4-203",
  },
  768: {
    id: "e2000000-0000-4000-8000-000000000030",
    name: "E2E招生学员768",
    relationshipId: "e2400000-0000-4000-8000-000000000003",
    room: "4-205",
  },
  360: {
    id: "e2000000-0000-4000-8000-000000000040",
    name: "E2E招生学员360",
    relationshipId: "e2400000-0000-4000-8000-000000000004",
    room: "4-206",
  },
} as const

test("guardian and student see only authorized personal housing", async ({
  page,
}, testInfo) => {
  test.setTimeout(180_000)
  page.setDefaultTimeout(15_000)
  const width = page.viewportSize()?.width ?? 1440
  const adult = REVOCATION_STUDENTS[width as keyof typeof REVOCATION_STUDENTS]
  if (!adult) throw new Error(`Missing housing fixture for ${width}`)

  await loginAsGuardian(page)
  await page.getByRole("link", { name: "住宿信息", exact: true }).click()
  await expect(
    page.getByRole("heading", { name: "住宿信息", exact: true }),
  ).toBeVisible()
  const subjectSelector = page.getByLabel("当前学员")
  await subjectSelector.selectOption({ label: "E2E已绑定学员" })
  const currentStay = page.locator(".myHousingCurrent .myHousingStayCard")
  await expect(currentStay.getByText("4-201", { exact: true })).toBeVisible()
  await expect(
    currentStay.getByText("E2E监护人第二学员", { exact: true }),
  ).toBeVisible()

  await subjectSelector.selectOption({ label: adult.name })
  await expect(page.getByRole("heading", { name: adult.name })).toBeVisible()
  const plannedStay = page.locator(".myHousingPlanned .myHousingStayCard")
  await expect(plannedStay.getByText(adult.room, { exact: true })).toBeVisible()
  await expect(plannedStay.getByText("实际入住后可查看")).toBeVisible()
  await expect(page.getByText("E2E教学教练", { exact: true })).toHaveCount(0)
  await expectPrivateHousingProjection(page)

  const { admin, csrfToken } = await createAdminRequest()
  try {
    const ended = await admin.post(
      `/api/v1/people/${adult.id}/guardians/${adult.relationshipId}/end`,
      {
        headers: { "X-CSRF-Token": csrfToken },
        data: { reason: `住宿门户四视口撤权 ${testInfo.project.name}` },
      },
    )
    expect(ended.status()).toBe(200)
  } finally {
    await admin.dispose()
  }

  await refocus(page)
  await expect(page.getByText(adult.name, { exact: true })).toHaveCount(0)
  await expect(page.getByText(adult.room, { exact: true })).toHaveCount(0)
  await expect(page.getByText("4-201", { exact: true })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".myHousingStayCard")
  await expectAboveMobileNavigation(page, ".myHousingStayCard")
  await expectPrivateHousingProjection(page)

  await logout(page)
  await loginAsStudent(page)
  await page.goto("/me/housing")
  await expect(
    page.getByRole("heading", { name: "E2E自助学员", exact: true }),
  ).toBeVisible()
  const selfStay = page.locator(".myHousingCurrent .myHousingStayCard")
  await expect(selfStay.getByText("4-210", { exact: true })).toBeVisible()
  await expect(selfStay.getByText("暂无室友", { exact: true })).toBeVisible()
  await expect(page.getByLabel("当前学员")).toHaveCount(0)
  await expect(page.getByText("E2E已绑定学员", { exact: true })).toHaveCount(0)
  await expect(page.getByText("E2E监护人第二学员", { exact: true })).toHaveCount(0)
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".myHousingStayCard")
  await expectAboveMobileNavigation(page, ".myHousingStayCard")
  await expectPrivateHousingProjection(page)
})
