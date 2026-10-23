import { expect, request, test } from "@playwright/test"
import type { APIRequestContext, Page } from "@playwright/test"

import {
  expectAboveMobileNavigation,
  expectHorizontallyInsideViewport,
  expectNoHorizontalOverflow,
  loginAsCoach,
  loginAsGuardian,
  loginAsStudent,
} from "./helpers"

const API_BASE_URL =
  process.env.E2E_API_URL ??
  process.env.E2E_BASE_URL ??
  "http://127.0.0.1:18080"

const REVOCATION_STUDENTS = {
  1440: {
    id: "e2000000-0000-4000-8000-000000000010",
    name: "E2E招生学员1440",
    relationshipId: "e2400000-0000-4000-8000-000000000001",
  },
  1024: {
    id: "e2000000-0000-4000-8000-000000000020",
    name: "E2E招生学员1024",
    relationshipId: "e2400000-0000-4000-8000-000000000002",
  },
  768: {
    id: "e2000000-0000-4000-8000-000000000030",
    name: "E2E招生学员768",
    relationshipId: "e2400000-0000-4000-8000-000000000003",
  },
  360: {
    id: "e2000000-0000-4000-8000-000000000040",
    name: "E2E招生学员360",
    relationshipId: "e2400000-0000-4000-8000-000000000004",
  },
} as const

async function createAdminRequest(): Promise<{
  admin: APIRequestContext
  csrfToken: string
}> {
  const admin = await request.newContext({ baseURL: API_BASE_URL })
  const response = await admin.post("/api/v1/auth/login", {
    data: { phone: "19990000001", password: "E2EAdm1n26" },
  })
  expect(response.status()).toBe(200)
  const body = (await response.json()) as { csrf_token: string }
  return { admin, csrfToken: body.csrf_token }
}

async function refocus(page: Page): Promise<void> {
  const other = await page.context().newPage()
  await other.bringToFront()
  await page.bringToFront()
  await other.close()
  // Headless Chrome does not always emit visibilitychange for bringToFront.
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")))
}

test("coach opens the nearest roster and keeps family access in one session", async ({
  page,
}) => {
  await loginAsCoach(page)
  await expect(page.getByRole("link", { name: "查看下一期名单" })).toBeVisible()
  await expect(page.getByRole("link", { name: "查看家庭信息" })).toBeVisible()
  await page.getByRole("link", { name: "查看下一期名单" }).click()
  await expect(page.getByRole("heading", { name: "下一期学员" })).toBeVisible()
  await expect(
    page.getByRole("heading", { name: "E2E英雄联盟普通班", exact: true }),
  ).toBeVisible()
  await expect(
    page.getByRole("heading", { name: "E2E英雄联盟菁英班", exact: true }),
  ).toBeVisible()
  await expectHorizontallyInsideViewport(page, ".portalRosterRow")
  await page.getByRole("button", { name: "2030-08-15 09:00" }).click()
  await expect(page.getByText("E2E自助学员", { exact: true })).toBeVisible()
  await page.getByRole("link", { name: "学习信息" }).click()
  await expect(page.getByRole("heading", { name: "E2E自助学员" })).toBeVisible()
  await expect(page.getByText("身份证")).toHaveCount(0)
  await expect(page.getByText("付款金额")).toHaveCount(0)
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".portalEnrollmentCard")
  await expectAboveMobileNavigation(page, ".portalEnrollmentCard")
})

test("guardian switches minor and adult students and sees only own payments", async ({
  page,
}) => {
  const viewport = page.viewportSize()?.width ?? 1440
  const adultName = `E2E招生学员${viewport}`
  await loginAsGuardian(page)
  await page.getByRole("link", { name: "查看家庭信息" }).click()
  const selector = page.getByLabel("当前学员")
  await selector.selectOption({ label: adultName })
  await expect(page.getByRole("heading", { name: adultName })).toBeVisible()
  await selector.selectOption({ label: "E2E已绑定学员" })
  await expect(page.getByRole("heading", { name: "E2E已绑定学员" })).toBeVisible()
  await expect(page.getByText("学习评价", { exact: true })).toBeVisible()
  await expect(page.getByText("下一阶段开放", { exact: true })).toBeVisible()
  await page.getByRole("link", { name: "工作台" }).click()
  await page.getByRole("link", { name: "查看本人付款" }).click()
  await expect(page.getByText("¥1,000.00", { exact: true })).toBeVisible()
  await expect(page.getByText("已退款 ¥200.00", { exact: true })).toBeVisible()
  await expect(page.getByText("¥999.99", { exact: true })).toHaveCount(0)
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".portalPaymentCard")
  await expectAboveMobileNavigation(page, ".portalPaymentCard")
})

test("student sees only self learning and self payments", async ({ page }) => {
  await loginAsStudent(page)
  await page.getByRole("link", { name: "查看我的学习" }).click()
  await expect(page.getByRole("heading", { name: "E2E自助学员" })).toBeVisible()
  await expect(page.getByLabel("当前学员")).toHaveCount(0)
  await page.goto("/learning/e1000000-0000-4000-8000-000000000004")
  await expect(page).not.toHaveURL(/e1000000-0000-4000-8000-000000000004$/)
  await expect(page.getByText("E2E已绑定学员", { exact: true })).toHaveCount(0)
  await page.getByRole("link", { name: "工作台" }).click()
  await page.getByRole("link", { name: "查看本人付款" }).click()
  await expect(page.getByText("¥800.00", { exact: true })).toBeVisible()
  await expect(page.getByText("¥1,000.00", { exact: true })).toHaveCount(0)
  await expect(page.getByText("¥999.99", { exact: true })).toHaveCount(0)
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".portalPaymentCard")
})

test("guardian relationship revocation clears the selected student", async ({
  page,
}, testInfo) => {
  const width = page.viewportSize()?.width as keyof typeof REVOCATION_STUDENTS
  const student = REVOCATION_STUDENTS[width]
  if (!student) throw new Error(`No revocation fixture for viewport ${width}`)
  await loginAsGuardian(page)
  await page.goto(`/learning/${student.id}`)
  await expect(page.getByRole("heading", { name: student.name })).toBeVisible()

  const { admin, csrfToken } = await createAdminRequest()
  try {
    const ended = await admin.post(
      `/api/v1/people/${student.id}/guardians/${student.relationshipId}/end`,
      {
        headers: { "X-CSRF-Token": csrfToken },
        data: { reason: `四视口撤权 ${testInfo.project.name}` },
      },
    )
    expect(ended.status()).toBe(200)
  } finally {
    await admin.dispose()
  }

  await page.goto(`/learning/${student.id}`)
  await expect(page).not.toHaveURL(new RegExp(`/learning/${student.id}$`))
  await expect(page.getByText(student.name, { exact: true })).toHaveCount(0)
})

test("window focus removes an ended coach assignment", async ({ page }) => {
  await loginAsCoach(page)
  await page.getByRole("link", { name: "查看下一期名单" }).click()
  await expect(
    page.getByRole("heading", { name: "E2E英雄联盟普通班", exact: true }),
  ).toBeVisible()
  const { admin, csrfToken } = await createAdminRequest()
  let endedAt = new Date()
  let assignmentEnded = false
  try {
    const listed = await admin.get(
      "/api/v1/classes/e2120000-0000-4000-8000-000000000001/coach-assignments",
    )
    expect(listed.status()).toBe(200)
    const body = (await listed.json()) as {
      items: Array<{ id: string; coach_person_id: string; ended_at: string | null }>
    }
    const active = body.items.find(
      (item) =>
        item.coach_person_id === "e1000000-0000-4000-8000-000000000006" &&
        item.ended_at === null,
    )
    expect(active).toBeTruthy()
    const ended = await admin.post(
      `/api/v1/classes/e2120000-0000-4000-8000-000000000001/coach-assignments/${active!.id}/end`,
      {
        headers: { "X-CSRF-Token": csrfToken },
        data: { reason: "门户聚焦撤权验收" },
      },
    )
    expect(ended.status()).toBe(200)
    const endedBody = (await ended.json()) as { ended_at: string }
    endedAt = new Date(endedBody.ended_at)
    assignmentEnded = true
    await refocus(page)
    await expect(
      page.getByRole("heading", {
        name: "E2E英雄联盟普通班",
        exact: true,
      }),
    ).toHaveCount(0)
    await expect(
      page.getByRole("heading", { name: "E2E英雄联盟菁英班", exact: true }),
    ).toBeVisible()
  } finally {
    if (assignmentEnded) {
      const restored = await admin.post(
        "/api/v1/classes/e2120000-0000-4000-8000-000000000001/coach-assignments",
        {
          headers: { "X-CSRF-Token": csrfToken },
          data: {
            coach_person_id: "e1000000-0000-4000-8000-000000000006",
            assignment_type: "primary",
            valid_from: new Date(endedAt.getTime() + 1_000).toISOString(),
            valid_until: "2031-01-01T00:00:00+08:00",
          },
        },
      )
      expect(restored.status()).toBe(201)
    }
    await admin.dispose()
  }
})

test("window focus clears amounts after payments.read self is denied", async ({
  page,
}) => {
  await loginAsGuardian(page)
  await page.getByRole("link", { name: "查看本人付款" }).click()
  await expect(page.getByText("¥1,000.00", { exact: true })).toBeVisible()
  const { admin, csrfToken } = await createAdminRequest()
  let overrideId: string | undefined
  try {
    const denied = await admin.post(
      "/api/v1/accounts/e1100000-0000-4000-8000-000000000003/permission-overrides",
      {
        headers: { "X-CSRF-Token": csrfToken },
        data: {
          permission_id: "20000000-0000-4000-8000-000000000018",
          effect: "deny",
          scope_kind: "self",
          scope_id: null,
        },
      },
    )
    expect(denied.status()).toBe(201)
    overrideId = ((await denied.json()) as { id: string }).id
    await refocus(page)
    await expect(page).toHaveURL(/\/$/)
    await expect(page.getByText("¥1,000.00", { exact: true })).toHaveCount(0)
    await expect(page.getByRole("link", { name: "查看本人付款" })).toHaveCount(0)
  } finally {
    if (overrideId) {
      const restored = await admin.delete(
        `/api/v1/accounts/e1100000-0000-4000-8000-000000000003/permission-overrides/${overrideId}`,
        { headers: { "X-CSRF-Token": csrfToken } },
      )
      expect(restored.status()).toBe(204)
    }
    await admin.dispose()
  }
})

test("keyboard opens roster, changes time group and enters learning", async ({
  page,
}) => {
  await loginAsCoach(page)
  const coachLink = page.getByRole("link", { name: "查看下一期名单" })
  await coachLink.focus()
  await expect(coachLink).toBeFocused()
  await page.keyboard.press("Enter")
  await expect(page).toHaveURL(/\/coach\/next-cohorts$/)
  const later = page.getByRole("button", { name: "2030-08-15 09:00" })
  await later.focus()
  await expect(later).toBeFocused()
  await page.keyboard.press("Enter")
  await expect(later).toHaveAttribute("aria-pressed", "true")
  await expect(page.getByText("E2E自助学员", { exact: true })).toBeVisible()
  const learningLink = page.getByRole("link", { name: "学习信息" })
  await learningLink.focus()
  await expect(learningLink).toBeFocused()
  await page.keyboard.press("Enter")
  await expect(page).toHaveURL(/\/learning\//)
})

test("shell changes cleanly at 767, 768, 1023 and 1024", async ({ page }) => {
  const original = page.viewportSize()
  if (!original) throw new Error("Project viewport is required")
  await loginAsCoach(page)
  try {
    await page.setViewportSize({ width: 767, height: 900 })
    await expect(page.locator(".shellMobileNavigation")).toBeVisible()
    await expect(page.locator(".shellRail")).toBeHidden()

    for (const width of [768, 1023]) {
      await page.setViewportSize({ width, height: 900 })
      await expect(page.locator(".shellMobileNavigation")).toBeHidden()
      await expect(page.locator(".shellRail")).toBeVisible()
      const topRail = await page.locator(".shellRail").boundingBox()
      if (!topRail) throw new Error("Top rail box is required")
      expect(Math.round(topRail.width)).toBe(width)
      expect(Math.round(topRail.height)).toBe(76)
    }

    await page.setViewportSize({ width: 1024, height: 768 })
    const sideRail = await page.locator(".shellRail").boundingBox()
    if (!sideRail) throw new Error("Side rail box is required")
    expect(sideRail.width).toBeLessThan(400)
    expect(sideRail.height).toBeGreaterThan(500)
    await expectNoHorizontalOverflow(page)
  } finally {
    await page.setViewportSize(original)
  }
})
