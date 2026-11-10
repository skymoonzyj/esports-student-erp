// Synthetic, full-application Chromium check. Run with a local Vite server:
// E2E_BASE_URL=http://127.0.0.1:18102 node src/appearance/appearance.browser.mjs
/* global process, URL, window, Event, document, localStorage, innerWidth, getComputedStyle, console */
import assert from "node:assert/strict"
import { mkdir } from "node:fs/promises"
import { fileURLToPath } from "node:url"
import { chromium } from "@playwright/test"

const base = process.env.E2E_BASE_URL || "http://127.0.0.1:18102"
const output = fileURLToPath(new URL("../../../../.superpowers/sdd/2026-10-27-erp-dual-theme-redesign/task-2-visuals/", import.meta.url))
const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: true })
let configuration = { theme: "clear", revision: 1 }
const writes = []
const errors = []

async function api(context, authenticated) {
  await context.route("**/api/v1/**", async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const json = (value, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) })
    if (path.endsWith("/auth/me")) return authenticated ? json({ account_id: "synthetic-admin", person_id: "synthetic-person", full_name: "模拟超管", email: null, force_password_change: false, is_super_admin: true, permissions: ["operations.monitor.read", "operations.appearance.manage"], scoped_permissions: [] }) : json({ code: "invalid_session", detail: "未登录" }, 401)
    if (path.endsWith("/auth/csrf")) return json({ csrf_token: "synthetic-csrf" })
    if (path.endsWith("/operations/appearance") && request.method() === "PUT") {
      const body = request.postDataJSON()
      assert.equal(request.headers()["x-csrf-token"], "synthetic-csrf")
      assert.equal(body.expected_revision, configuration.revision)
      writes.push(body)
      configuration = { theme: body.theme, revision: configuration.revision + 1 }
      return json(configuration)
    }
    if (path.endsWith("/appearance")) return json(configuration)
    if (path.endsWith("/portal/context")) return json({ coach_available: false, learning_available: false, payments_available: false, housing_management_available: false, housing_available: false, invitations_available: false, adult_guardian_invitations_available: false, food_profile_available: false, food_preferences_onboarding_required: false, learning_mode: null })
    if (path.endsWith("/team-management/context")) return json({ can_open_management: false, can_read_self: false, can_manage_catalog: false, can_assign_managers: false, can_transfer: false })
    // Deliberately fail independent operations data: appearance must remain usable.
    if (path.endsWith("/operations/overview")) return json({ detail: "合成监控服务不可用", code: "synthetic_unavailable" }, 503)
    return json({ detail: `Unmocked synthetic API: ${path}`, code: "unmocked_fixture" }, 599)
  })
}

try {
  await mkdir(output, { recursive: true })
  const adminContext = await browser.newContext()
  const anonymousContext = await browser.newContext()
  await api(adminContext, true)
  await api(anonymousContext, false)
  const admin = await adminContext.newPage()
  const login = await anonymousContext.newPage()
  admin.on("pageerror", (error) => errors.push(error.message))
  login.on("pageerror", (error) => errors.push(error.message))
  await admin.goto(`${base}/settings/operations`)
  await login.goto(`${base}/login`)
  const panel = admin.getByRole("region", { name: "系统外观" })
  await panel.waitFor()
  const loginInput = login.getByRole("textbox").first()
  await loginInput.fill("保留的合成输入")
  const loginPath = new URL(login.url()).pathname
  // Keep the editor mounted while another administrator changes global config.
  const originalClear = await panel.getByRole("radio", { name: /清爽专业/ }).elementHandle()
  configuration = { theme: "command", revision: 2 }
  await admin.evaluate(() => window.dispatchEvent(new Event("focus")))
  await admin.waitForFunction(() => document.documentElement.dataset.uiTheme === "command")
  await panel.getByRole("radio", { name: /深蓝效率.*当前全局/ }).waitFor()
  await panel.getByText("当前已保存 · 版本 2").waitFor()
  assert.equal(await originalClear.evaluate((element) => element.isConnected), true, "external update does not remount editor")
  await panel.getByRole("radio", { name: /清爽专业/ }).check()
  await panel.getByRole("button", { name: "取消" }).click()
  assert.equal(await panel.getByRole("radio", { name: /深蓝效率/ }).isChecked(), true, "cancel follows authoritative command2")
  await panel.getByRole("radio", { name: /清爽专业/ }).check()
  await panel.getByRole("button", { name: "保存全局外观" }).click()
  await panel.getByText(/已保存，全体用户/).waitFor()
  assert.deepEqual(writes[0], { theme: "clear", expected_revision: 2 })
  await panel.getByRole("radio", { name: /深蓝效率/ }).check()
  configuration = { theme: "clear", revision: 4 }
  await admin.evaluate(() => window.dispatchEvent(new Event("focus")))
  await admin.waitForFunction(() => JSON.parse(localStorage.getItem("erp.appearance.v1")).revision === 4)
  assert.equal(await panel.getByRole("radio", { name: /深蓝效率/ }).isChecked(), true, "external update preserves dirty draft")
  assert.equal(await panel.getByRole("radio", { name: /清爽专业.*当前全局/ }).isChecked(), false)
  await panel.getByRole("button", { name: "保存全局外观" }).click()
  await panel.getByText(/已保存，全体用户/).waitFor()
  assert.deepEqual(writes[1], { theme: "command", expected_revision: 4 })
  configuration = { theme: "clear", revision: 6 }
  await admin.evaluate(() => window.dispatchEvent(new Event("focus")))
  await panel.getByRole("radio", { name: /清爽专业.*当前全局/ }).waitFor()
  assert.equal(await panel.getByRole("radio", { name: /清爽专业/ }).isChecked(), true)
  const snapshots = []
  for (const width of [320, 390, 768, 1440]) {
    await admin.setViewportSize({ width, height: 900 })
    for (const [theme, name] of [["command", /深蓝效率/], ["clear", /清爽专业/]]) {
      const before = await admin.locator("html").getAttribute("data-ui-theme")
      await panel.getByRole("radio", { name }).check()
      assert.equal(await admin.locator("html").getAttribute("data-ui-theme"), before, "draft does not theme the screen")
      await panel.getByRole("button", { name: "保存全局外观" }).click()
      await panel.getByText(/已保存，全体用户/).waitFor()
      assert.equal(await admin.locator("html").getAttribute("data-ui-theme"), theme)
      await login.evaluate(() => window.dispatchEvent(new Event("focus")))
      await login.waitForFunction((theme) => document.documentElement.dataset.uiTheme === theme, theme)
      assert.equal(await loginInput.inputValue(), "保留的合成输入", "cross-page update preserves input")
      assert.equal(new URL(login.url()).pathname, loginPath, "cross-page update preserves route")
      const layout = await panel.evaluate((element) => ({
        overflow: document.documentElement.scrollWidth > innerWidth,
        panelWidth: element.getBoundingClientRect().width,
        choices: [...element.querySelectorAll(".appearanceChoice")].map((choice) => ({ x: choice.getBoundingClientRect().x, y: choice.getBoundingClientRect().y })),
        buttons: [...element.querySelectorAll("button")].map((button) => button.getBoundingClientRect().height),
        previewBackgrounds: [...element.querySelectorAll(".appearancePreview aside")].map((aside) => getComputedStyle(aside).backgroundColor),
      }))
      assert.equal(layout.overflow, false, `no overflow ${width}/${theme}`)
      assert.ok(layout.buttons.every((height) => height >= 44), "44px button targets")
      assert.notEqual(layout.previewBackgrounds[0], layout.previewBackgrounds[1], "two independently styled miniature previews")
      if (width <= 390) assert.ok(layout.choices[1].y > layout.choices[0].y, "stacked phone previews")
      if (width >= 768) assert.equal(layout.choices[1].y, layout.choices[0].y, "desktop previews side by side")
      snapshots.push({ width, theme, ...layout })
      await panel.screenshot({ path: `${output}/${theme}-${width}.png` })
    }
  }
  assert.equal(writes.length, 10)
  assert.deepEqual(errors, [], "no browser runtime errors")
  console.log(JSON.stringify({ result: "PASS", checks: "external-update/cancel/latest-save/dirty-draft regression plus 8 real-app save/sync/layout cases with preserved anonymous input and route", snapshots }, null, 2))
} finally { await browser.close() }
