import { execFile } from "node:child_process"
import { createHash } from "node:crypto"
import { delimiter } from "node:path"
import { promisify } from "node:util"
import { fileURLToPath } from "node:url"

import { expect, request } from "@playwright/test"
import type { APIRequestContext, Locator, Page } from "@playwright/test"

import { e2eCredentialTuple } from "./e2eCredentials"

const API_BASE_URL =
  process.env.E2E_API_URL ??
  process.env.E2E_BASE_URL ??
  "http://127.0.0.1:18080"

let personSequence = 10
const PERSON_SEQUENCE_BASES = new Map([
  [1440, 200],
  [1024, 300],
  [768, 400],
  [360, 500],
])

export type CreatedPerson = { id: string; name: string }

export type AdminRequestSession = {
  admin: APIRequestContext
  csrfToken: string
}

export type RequestSession = {
  api: APIRequestContext
  csrfToken: string
}

export type RegistrationTestChallengePurpose =
  | "registration"
  | "password_reset"
  | "guardian_invitation"
  | "add_login_identifier"
  | "assisted_activation"
  | "profile_claim"

const execFileAsync = promisify(execFile)
const API_DIRECTORY = fileURLToPath(new URL("../../api/", import.meta.url))

function projectNamespace(page: Page): string {
  return `viewport-${page.viewportSize()?.width ?? 1440}`
}

function nextPersonSequence(page: Page): number {
  personSequence += 1
  const width = page.viewportSize()?.width ?? 1440
  const sequenceBase = PERSON_SEQUENCE_BASES.get(width) ?? 700
  return sequenceBase + personSequence
}

export function makeCnId(birth: string, sequence: number): string {
  const body = `110105${birth}${String(sequence).padStart(3, "0")}`
  const weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
  const checks = ["1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2"]
  const sum = body
    .split("")
    .reduce((value, digit, index) => value + Number(digit) * weights[index], 0)
  return `${body}${checks[sum % 11]}`
}

async function login(page: Page, phone: string, password: string): Promise<void> {
  await page.goto("/login")
  await page.getByRole("heading", { name: "登录工作台", exact: true }).waitFor()
  await expectNoHorizontalOverflow(page)
  await page.getByLabel("手机号").fill(phone)
  await page.getByLabel("密码", { exact: true }).fill(password)
  await page.getByRole("button", { name: "登录" }).click()
  await page.getByRole("heading", { name: "工作台", exact: true }).waitFor()
  await expectNoHorizontalOverflow(page)
}

export async function loginWithIdentifier(
  page: Page,
  identifier: string,
  password: string,
): Promise<void> {
  await login(page, identifier, password)
}

async function selectRadioCard(page: Page, name: RegExp): Promise<void> {
  const radio = page.getByRole("radio", { name })
  await radio.locator("..").click()
  await expect(radio).toBeChecked()
}

export async function loginAsAdmin(page: Page): Promise<void> {
  await login(page, ...e2eCredentialTuple("admin"))
}

export async function createAdminRequest(): Promise<AdminRequestSession> {
  const session = await createRequestSession(...e2eCredentialTuple("admin"))
  return { admin: session.api, csrfToken: session.csrfToken }
}

export async function createRequestSession(
  identifier: string,
  password: string,
): Promise<RequestSession> {
  const api = await request.newContext({ baseURL: API_BASE_URL })
  const response = await api.post("/api/v1/auth/login", {
    data: {
      identifier,
      password,
    },
  })
  if (response.status() !== 200) {
    await api.dispose()
  }
  expect(response.status()).toBe(200)
  const body = (await response.json()) as { csrf_token: string }
  return { api, csrfToken: body.csrf_token }
}

export function registrationTestDestinationAlias(destination: string): string {
  const digest = createHash("sha256")
    .update(`registration-test-destination\0${destination}`, "utf8")
    .digest("hex")
  return `dst_${digest.slice(0, 24)}`
}

export async function readRegistrationTestCode(input: {
  challengeId: string
  destination: string
  purpose: Exclude<RegistrationTestChallengePurpose, "assisted_activation" | "profile_claim">
} | {
  destination: string
  purpose: "assisted_activation" | "profile_claim"
}): Promise<string> {
  const python = process.env.E2E_PYTHON_BIN
  if (!python) throw new Error("E2E_PYTHON_BIN is required")
  const mailbox = process.env.REGISTRATION_TEST_MAILBOX_DIR
  if (!mailbox) throw new Error("REGISTRATION_TEST_MAILBOX_DIR is required")
  const pythonPath = [API_DIRECTORY, process.env.PYTHONPATH]
    .filter((value): value is string => Boolean(value))
    .join(delimiter)
  const readerEnvironment: NodeJS.ProcessEnv = {
    APP_ENV: "test",
    PYTHONPATH: pythonPath,
    REGISTRATION_TEST_MAILBOX_DIR: mailbox,
  }
  for (const key of ["HOME", "LANG", "LC_ALL", "PATH", "TMPDIR"] as const) {
    const value = process.env[key]
    if (value) readerEnvironment[key] = value
  }
  const { stdout } = await execFileAsync(
    python,
    [
      "-m",
      "app.cli.read_registration_test_code",
      ...("challengeId" in input ? ["--challenge-id", input.challengeId] : []),
      "--destination-alias",
      registrationTestDestinationAlias(input.destination),
      "--purpose",
      input.purpose,
    ],
    {
      cwd: API_DIRECTORY,
      env: readerEnvironment,
      encoding: "utf8",
      maxBuffer: 4096,
    },
  )
  const code = stdout.trim()
  const valid = input.purpose === "guardian_invitation"
    ? /^[A-Za-z0-9_-]{43}$/.test(code)
    : /^\d{6}$/.test(code)
  if (!valid) throw new Error("registration test code reader returned invalid output")
  return code
}

export async function refocus(page: Page): Promise<void> {
  const other = await page.context().newPage()
  await other.bringToFront()
  await page.bringToFront()
  await other.close()
  // Headless Chromium does not reliably emit this event for bringToFront.
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")))
}

export async function loginAsCustomerService(page: Page): Promise<void> {
  await login(page, ...e2eCredentialTuple("customer_service"))
}

export async function loginAsCoach(page: Page): Promise<void> {
  await login(page, ...e2eCredentialTuple("coach"))
}

export async function loginAsGuardian(page: Page): Promise<void> {
  await login(page, ...e2eCredentialTuple("guardian"))
}

export async function loginAsStudent(page: Page): Promise<void> {
  await login(page, ...e2eCredentialTuple("student"))
}

export async function logout(page: Page): Promise<void> {
  const candidates = page.getByRole("button", { name: "退出登录" })
  for (let index = 0; index < (await candidates.count()); index += 1) {
    const candidate = candidates.nth(index)
    if (await candidate.isVisible()) {
      const responsePromise = page.waitForResponse((response) =>
        response.request().method() === "POST"
        && new URL(response.url()).pathname === "/api/v1/auth/logout"
        && response.status() === 204,
      )
      const [response] = await Promise.all([responsePromise, candidate.click()])
      expect(response.status()).toBe(204)
      await expect(page).toHaveURL(/\/login(?:\?|$)/)
      await page.getByRole("heading", { name: "登录工作台" }).waitFor()
      await expectNoHorizontalOverflow(page)
      return
    }
  }
  throw new Error("Visible logout action was not found")
}

export async function createPerson(
  page: Page,
  type: "guardian" | "student" | "staff",
  staffType: "coach" | "life_teacher" | "customer_service" | "administration" | "other" =
    "life_teacher",
): Promise<CreatedPerson> {
  const sequence = nextPersonSequence(page)
  const minor = type === "student"
  const birth = minor ? "20120102" : "19900102"
  const namespace = projectNamespace(page)
  const name = `E2E-${namespace}-${type}-${sequence}`
  await page.goto("/people/new")
  const typeLabel = { guardian: "监护人", student: "学员", staff: "工作人员" }[type]
  await selectRadioCard(page, new RegExp(`^${typeLabel}`))
  await page.getByLabel("姓名").fill(name)
  await page.getByLabel("身份证").fill(makeCnId(birth, sequence))
  await page.getByLabel("性别").selectOption(sequence % 2 === 0 ? "female" : "male")
  await page.getByLabel("出生日期").fill(minor ? "2012-01-02" : "1990-01-02")
  await page.getByLabel("手机号").fill(`139${String(sequence).padStart(8, "0")}`)
  await page.getByLabel("邮箱").fill(`${namespace}-${type}-${sequence}@example.com`)
  if (type === "student") {
    await page.getByLabel("学员编号", { exact: true }).fill(`E2E-${namespace}-S-${sequence}`)
  }
  if (type === "staff") {
    await page.getByLabel("工作人员类型").selectOption(staffType)
  }
  await page.getByRole("button", { name: "保存人员" }).click()
  await page.getByRole("heading", { name, level: 1, exact: true }).waitFor()
  const match = page.url().match(/\/people\/([0-9a-f-]+)$/)
  if (!match) throw new Error(`Person detail URL missing id: ${page.url()}`)
  return { id: match[1], name }
}

export async function createMinorStudent(page: Page): Promise<CreatedPerson> {
  return createPerson(page, "student")
}

export async function bindGuardian(
  page: Page,
  student: CreatedPerson,
  guardian: CreatedPerson,
): Promise<void> {
  await page.goto(`/people/${student.id}`)
  await page.getByRole("button", { name: "绑定监护人" }).click()
  await page.getByLabel("查找已有监护人").fill(guardian.name)
  await page.getByRole("button", { name: "搜索" }).click()
  await selectRadioCard(page, new RegExp(`^${guardian.name}`))
  await page.getByRole("button", { name: "确认绑定" }).click()
  await expect(page.getByText("未成年 · 监护人已绑定")).toBeVisible()
}

export async function openPersonAccount(
  page: Page,
  person: CreatedPerson,
): Promise<void> {
  await page.goto(`/people/${person.id}`)
  await expect(
    page.getByRole("heading", { name: person.name, level: 1 }),
  ).toBeVisible()
  await page.getByRole("button", { name: "开通账号" }).click()
  await page.getByRole("button", { name: "确认开通" }).click()
  await page.getByRole("heading", { name: "请立即安全交付临时密码" }).waitFor()
  await page.getByRole("button", { name: "我已保存并关闭" }).click()
  await expect(
    page.getByRole("heading", { name: "请立即安全交付临时密码" }),
  ).toHaveCount(0)
}

export async function assignRoleToPerson(
  page: Page,
  person: CreatedPerson,
  roleName: string,
): Promise<void> {
  await page.goto("/settings/permissions")
  const accountSelect = page.getByLabel("账号", { exact: true })
  const accountOption = accountSelect.locator("option").filter({
    hasText: `${person.name} · 已启用`,
  })
  await expect(accountOption).toHaveCount(1)
  const accountId = await accountOption.getAttribute("value")
  if (!accountId) throw new Error(`Account option missing value for ${person.name}`)
  await accountSelect.selectOption(accountId)
  await page.getByLabel("添加角色").selectOption({ label: roleName })
  await page.getByRole("button", { name: "分配角色" }).click()
  await expect(page.getByRole("button", { name: `移除 ${roleName}` })).toBeVisible()
}

export async function createStaffAccount(
  page: Page,
  staffType: "coach" | "life_teacher" | "customer_service" | "administration" | "other",
): Promise<CreatedPerson> {
  const person = await createPerson(page, "staff", staffType)
  await openPersonAccount(page, person)
  return person
}

export async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      ),
    )
    .toBe(true)
}

export async function expectNoInternalHorizontalOverflow(
  locator: Locator,
): Promise<void> {
  await expect(locator).toBeVisible()
  await expect
    .poll(() => locator.evaluate((element) => element.scrollWidth <= element.clientWidth))
    .toBe(true)
}

export async function expectPrivateHousingProjection(page: Page): Promise<void> {
  const portal = page.locator(".myHousingPage")
  await expect(portal).toBeVisible()
  const value = await portal.innerText()
  expect(value).not.toMatch(/\b1[3-9]\d{9}\b/)
  expect(value).not.toMatch(/\b\d{17}[\dX]\b/i)
  expect(value).not.toMatch(/[\w.+-]+@example\.com/i)
  expect(value).not.toMatch(
    /\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b/i,
  )
  for (const forbidden of [
    "手机号",
    "邮箱",
    "身份证",
    "性别",
    "问题房",
    "问题描述",
    "E2E个人住宿门户私有夹具备注",
    "subject_person_id",
    "relationshipId",
  ]) {
    expect(value).not.toContain(forbidden)
  }
}

export async function expectHorizontallyInsideViewport(
  page: Page,
  selector: string,
): Promise<void> {
  const locator = page.locator(selector)
  expect(await locator.count()).toBeGreaterThan(0)
  await expect(locator.first()).toBeVisible()
  const boxes = await locator.evaluateAll((elements) =>
    elements
      .filter((element) => element.getClientRects().length > 0)
      .map((element) => element.getBoundingClientRect())
      .map((box) => ({ left: box.left, right: box.right })),
  )
  expect(boxes.length).toBeGreaterThan(0)
  const viewport = page.viewportSize()
  if (!viewport) throw new Error("Viewport is required for responsive E2E")
  for (const box of boxes) {
    expect(box.left).toBeGreaterThanOrEqual(0)
    expect(box.right).toBeLessThanOrEqual(viewport.width)
  }
}

export async function expectResponsiveRecordLabels(
  row: Locator,
  labels: readonly string[],
): Promise<void> {
  for (const label of labels) {
    const cell = row.locator(`td[data-label="${label}"]`)
    await expect(cell).toHaveCount(1)
    await expect(cell).toBeVisible()
  }
}

export async function expectAboveMobileNavigation(
  page: Page,
  selector: string,
): Promise<void> {
  if (page.viewportSize()?.width !== 360) return
  const content = page.locator(selector).last()
  const navigation = page.locator(".shellMobileNavigation")
  await expect(content).toBeVisible()
  await expect(navigation).toBeVisible()
  const overlayZIndex = await content.evaluate((element) => {
    let current: Element | null = element
    while (current) {
      const style = window.getComputedStyle(current)
      if (style.position === "fixed") {
        const zIndex = Number(style.zIndex)
        return Number.isFinite(zIndex) ? zIndex : 0
      }
      current = current.parentElement
    }
    return null
  })
  if (overlayZIndex !== null) {
    const navigationZIndex = await navigation.evaluate((element) => {
      const zIndex = Number(window.getComputedStyle(element).zIndex)
      return Number.isFinite(zIndex) ? zIndex : 0
    })
    expect(overlayZIndex).toBeGreaterThan(navigationZIndex)
    return
  }
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight))
  const [contentBox, navigationBox] = await Promise.all([
    content.boundingBox(),
    navigation.boundingBox(),
  ])
  if (!contentBox || !navigationBox) throw new Error("Visible boxes are required")
  expect(contentBox.y + contentBox.height).toBeLessThanOrEqual(navigationBox.y)
}

export async function openNavigationItem(page: Page, name: string): Promise<void> {
  const candidates = page.getByRole("link", { name })
  const more = page.getByRole("button", { name: "更多" })
  const visibleCandidate = async (): Promise<Locator | null> => {
    for (let index = 0; index < (await candidates.count()); index += 1) {
      const candidate = candidates.nth(index)
      if (await candidate.isVisible()) return candidate
    }
    return null
  }

  await expect
    .poll(async () => (await visibleCandidate()) !== null || await more.isVisible())
    .toBe(true)
  const candidate = await visibleCandidate()
  if (candidate) {
    await candidate.click()
    return
  }
  await more.click()
  await page
    .getByRole("dialog", { name: "更多导航" })
    .getByRole("link", { name })
    .click()
}
