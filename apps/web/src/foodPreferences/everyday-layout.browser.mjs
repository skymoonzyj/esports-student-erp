/* global window, document, getComputedStyle */
import assert from "node:assert/strict"
import console from "node:console"
import { readFile } from "node:fs/promises"
import process from "node:process"
import { URL } from "node:url"
import { chromium } from "@playwright/test"

// Real Chromium geometry checks; all data is synthetic and no business writes occur.
// Run with a local Vite server: node src/foodPreferences/everyday-layout.browser.mjs
const baseURL = process.env.ENTRY_LAYOUT_URL ?? "http://127.0.0.1:18188"
const browser = await chromium.launch({ headless: true })
const page = await browser.newPage()
await page.emulateMedia({ reducedMotion: "reduce" })
let apiWrites = 0
let publishedTheme = "clear"
await page.route("**/api/v1/**", async (route) => {
  if (!["GET", "HEAD"].includes(route.request().method())) apiWrites += 1
  const path = new URL(route.request().url()).pathname
  const body = path.endsWith("/appearance")
    ? { theme: publishedTheme, revision: publishedTheme === "command" ? 2 : 1 }
    : path.endsWith("/channel-settings")
      ? { email_enabled: true, phone_enabled: true, phone_signup_allowed_by_deployment: true, updated_at: null, request_id: "synthetic-layout" }
      : { status: 401, code: "invalid_session", detail: "Synthetic anonymous session" }
  await route.fulfill({ status: path.includes("/auth/") ? 401 : 200, json: body })
})

async function noOverflow(label) {
  const geometry = await page.evaluate(() => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
  }))
  if (geometry.document > geometry.viewport + 1) {
    console.error(label, await page.evaluate(() => [...document.querySelectorAll("body *")].map((element) => ({
      element: element.className || element.tagName,
      right: element.getBoundingClientRect().right,
      width: element.getBoundingClientRect().width,
    })).filter((element) => element.right > window.innerWidth + 1)))
  }
  assert.ok(geometry.document <= geometry.viewport + 1, `${label}: horizontal overflow ${JSON.stringify(geometry)}`)
}

try {
  const brandBackgrounds = []
  for (const theme of ["clear", "command"]) {
    publishedTheme = theme
    for (const width of [1440, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 900 })
      for (const path of ["/login", "/register", "/forgot-password"]) {
        await page.goto(`${baseURL}${path}`)
        await page.locator(path === "/login" ? ".loginForm" : ".registrationCard").waitFor()
        // Vite injects the main CSS after eager route-module styles. Wait for the
        // existing global reset, not a timeout or a geometry condition that could hide overflow.
        await page.waitForFunction(() => getComputedStyle(document.body).margin === "0px")
        await page.evaluate((value) => { document.documentElement.dataset.uiTheme = value }, theme)
        await noOverflow(`${theme} ${width} ${path}`)
        if (process.env.ENTRY_LAYOUT_SCREENSHOT_DIR && [1440, 390].includes(width)) {
          await page.screenshot({ path: `${process.env.ENTRY_LAYOUT_SCREENSHOT_DIR}/task-8-${theme}-${width}-${path.slice(1)}.png`, fullPage: true })
        }
        if (path === "/login") {
          for (const locator of [page.getByRole("button", { name: "显示密码" }), ...await page.locator(".loginPublicActions a").all()]) {
            const box = await locator.boundingBox()
            assert.ok(box && box.height >= 44 && box.width >= 44, `44px login target: ${theme}/${width}`)
          }
          await page.getByLabel("手机号或邮箱").fill("synthetic@example.test")
          await page.getByLabel("密码", { exact: true }).fill("Draft123!")
          await page.evaluate((value) => { document.documentElement.dataset.uiTheme = value === "clear" ? "command" : "clear" }, theme)
          assert.equal(await page.getByLabel("手机号或邮箱").inputValue(), "synthetic@example.test")
          assert.equal(await page.getByLabel("密码", { exact: true }).inputValue(), "Draft123!")
          await page.evaluate((value) => { document.documentElement.dataset.uiTheme = value }, theme)
          if (width === 1440) brandBackgrounds.push(await page.locator(".loginBrand").evaluate((element) => getComputedStyle(element).backgroundColor))
        } else {
          if (width === 1440) {
            assert.ok(await page.locator(".publicEntryAside").isVisible(), "Desktop registration has editorial panel")
            const aside = await page.locator(".publicEntryAside").boundingBox()
            const card = await page.locator(".registrationCard").boundingBox()
            assert.ok(aside && card && aside.x + aside.width <= card.x + 1, "Editorial and functional panels are side by side")
          }
          if (path === "/register") {
            await page.getByRole("radio", { name: /^学员/ }).check()
            await page.locator("#registration-identifier").fill("draft@example.test")
            await page.evaluate((value) => { document.documentElement.dataset.uiTheme = value === "clear" ? "command" : "clear" }, theme)
            assert.equal(await page.locator("#registration-identifier").inputValue(), "draft@example.test")
            assert.ok(await page.getByRole("radio", { name: /^学员/ }).isChecked(), "Theme switch retains identity selection")
            await noOverflow(`${theme} ${width} selected registration form`)
          }
        }
      }
    }
  }
  assert.notEqual(brandBackgrounds[0], brandBackgrounds[1], "A/B entry panels have distinct material hierarchy")

  const files = ["styles/tokens.css", "styles/global.css", "people/self-profile.css", "foodPreferences/food-preferences.css", "travelReports/travel-reports.css"]
  const css = (await Promise.all(files.map((file) => readFile(new URL(`../${file}`, import.meta.url), "utf8")))).join("\n")
  for (const theme of ["clear", "command"]) {
    for (const width of [1440, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 900 })
      await page.setContent(`<html data-ui-theme="${theme}"><head><style>${css}</style></head><body><main style="padding:16px;min-width:0">
        <section class="selfProfilePanel"><header class="selfProfileHeader"><h2>个人资料</h2><span class="selfProfilePrivacy">仅本人可见</span></header><div class="selfProfileContent"><dl class="selfProfileFacts">${Array.from({ length: 6 }, (_, i) => `<div><dt>测试字段 ${i}</dt><dd>合成资料</dd></div>`).join("")}</dl><form class="selfProfileNicknameForm"><label>昵称<input value="未提交草稿"></label><button class="selfProfileSaveButton">保存昵称</button></form></div></section>
        <section class="foodPreferenceStatisticsPage"><div class="foodPreferenceStatistics__summary">${Array.from({ length: 4 }, (_, i) => `<article><span>汇总 ${i}</span><strong>12</strong></article>`).join("")}</div></section>
        <section class="travelReportsPage"><header class="travelReportsHeader"><h1>行程报表</h1><button class="travelReportsPrimaryButton">导入行程报表</button></header><div class="travelReportsSummary">${Array.from({ length: 4 }, () => "<div><span>合成汇总</span><strong>12</strong></div>").join("")}</div><div class="travelReportsFilters"><label>筛选<select><option>全部报表</option></select></label><button>刷新</button></div><div class="travelReportsTableWrap"><table class="travelReportsTable"><thead><tr><th>报表</th><th>时间</th></tr></thead><tbody><tr><td>synthetic.xlsx</td><td><div class="travelReportsActions"><button>下载</button></div></td></tr></tbody></table></div><nav class="travelReportsPagination"><button>上一页</button><button>下一页</button></nav></section>
        </main></body></html>`)
      await noOverflow(`${theme} ${width} everyday pages`)
      for (const control of await page.locator("button,input,select").all()) {
        const box = await control.boundingBox()
        assert.ok(box && box.height >= 44, `44px everyday target ${theme}/${width}`)
      }
      const summary = page.locator(".foodPreferenceStatistics__summary")
      const gap = await summary.evaluate((element) => getComputedStyle(element).columnGap)
      assert.equal(gap, theme === "command" ? "0px" : "16px", "A cards / B connected statistics")
      const table = page.locator(".travelReportsTableWrap")
      assert.ok(await table.evaluate((element) => element.scrollWidth <= element.clientWidth || getComputedStyle(element).overflowX === "auto"), "Wide report stays in a local scroll container")
      if (process.env.ENTRY_LAYOUT_SCREENSHOT_DIR && [1440, 390].includes(width)) {
        await page.screenshot({ path: `${process.env.ENTRY_LAYOUT_SCREENSHOT_DIR}/task-8-${theme}-${width}-daily-synthetic.png`, fullPage: true })
      }
    }
  }
  assert.equal(apiWrites, 0, "Presentation/theme checks cause no business writes")
  console.log("PASS: 24 public-entry layouts and 8 everyday layouts, A/B panels, preserved login/registration inputs, 44px targets, local table overflow and zero business writes")
} finally {
  await browser.close()
}
