import { readFileSync } from "node:fs"
import { expect, test } from "@playwright/test"
import { installUiThemeFixtures } from "./helpers/uiThemeFixtures"

const tokens = readFileSync(new URL("../src/styles/tokens.css", import.meta.url), "utf8")
const globalStyles = readFileSync(new URL("../src/styles/global.css", import.meta.url), "utf8")

function luminance(color: string): number {
  const channels = color.match(/[\d.]+/g)?.slice(0, 3).map(Number) ?? []
  if (channels.length !== 3) throw new Error(`Unresolved color: ${color}`)
  const linear = channels.map((value) => {
    const c = value / 255
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })
  return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722
}

function contrast(a: string, b: string): number {
  const values = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (values[0] + 0.05) / (values[1] + 0.05)
}

test("both global themes resolve accessible surfaces and retain form state", async ({ page }) => {
  await page.setContent(`<style>${tokens}\n${globalStyles}
    .sample { padding: 16px; max-width: 100%; }
    .rail { padding: 16px; color: var(--ui-sidebar-text); background: var(--ui-sidebar-bg); }
    .muted { color: var(--color-text-muted); background: var(--color-surface); }
    .card { border-radius: var(--ui-radius-lg); padding: var(--ui-space); background: var(--color-surface); }
    .cell { padding-block: var(--ui-table-cell-y); }
  </style><main class="sample"><nav class="rail">工作台</nav><section class="card"><p class="muted">资料仅向有权限的人员开放</p><label>备注<input aria-label="备注"></label><div class="cell">已入住</div><button class="primaryButton">保存设置</button></section></main>`)
  await page.getByRole("textbox", { name: "备注" }).fill("切换后保留的内容")
  const snapshots = []
  for (const theme of ["clear", "command"]) {
    await page.evaluate((value) => { document.documentElement.dataset.uiTheme = value }, theme)
    await page.evaluate(async () => {
      // Sample settled colors, not the first frame of the button's theme transition.
      void getComputedStyle(document.querySelector(".primaryButton")!).backgroundColor
      await Promise.all(document.getAnimations().map((animation) => animation.finished))
    })
    const snapshot = await page.evaluate(() => {
      const style = (selector: string) => getComputedStyle(document.querySelector(selector)!)
      return {
        rail: style(".rail").backgroundColor,
        railText: style(".rail").color,
        primary: style(".primaryButton").backgroundColor,
        primaryText: style(".primaryButton").color,
        muted: style(".muted").color,
        surface: style(".muted").backgroundColor,
        radius: parseFloat(style(".card").borderRadius),
        cellPadding: parseFloat(style(".cell").paddingTop),
        buttonHeight: document.querySelector("button")!.getBoundingClientRect().height,
        overflow: document.documentElement.scrollWidth > window.innerWidth,
      }
    })
    expect(snapshot.rail).not.toBe("rgba(0, 0, 0, 0)")
    expect(contrast(snapshot.rail, snapshot.railText)).toBeGreaterThanOrEqual(4.5)
    expect(contrast(snapshot.primary, snapshot.primaryText)).toBeGreaterThanOrEqual(4.5)
    expect(contrast(snapshot.muted, snapshot.surface)).toBeGreaterThanOrEqual(4.5)
    expect(snapshot.radius).toBeGreaterThan(0)
    expect(snapshot.buttonHeight).toBeGreaterThanOrEqual(44)
    expect(snapshot.overflow).toBe(false)
    await expect(page.getByRole("textbox", { name: "备注" })).toHaveValue("切换后保留的内容")
    snapshots.push(snapshot)
  }
  expect(snapshots[0].primary).not.toBe(snapshots[1].primary)
  expect(luminance(snapshots[0].rail)).toBeGreaterThan(0.8)
  expect(luminance(snapshots[1].rail)).toBeLessThan(0.1)
  expect(snapshots[1].cellPadding).toBeLessThan(snapshots[0].cellPadding)
})

for (const theme of ["clear", "command"] as const) {
  for (const path of ["/activate-profile", "/claim-profile"]) {
    test(`${theme}: ${path} keeps the public form usable across entry layouts`, async ({ page, baseURL }) => {
      if (!baseURL || !["127.0.0.1", "localhost"].includes(new URL(baseURL).hostname)) {
        throw new Error("Public entry layout checks require an isolated loopback server")
      }
      const fixture = await installUiThemeFixtures(page, theme, false)
      await page.goto(path)
      const email = page.getByLabel("本人邮箱", { exact: true })
      await expect(email).toBeVisible()
      await expect(page.locator("html")).toHaveAttribute("data-ui-theme", theme)
      const dimensions = await page.locator(".registrationCard").evaluate(element => ({
        width: element.getBoundingClientRect().width,
        viewport: innerWidth,
        documentWidth: document.documentElement.scrollWidth,
      }))
      // Missing the shared aside put these forms into the narrow left grid track
      // (253px at a 1024px viewport), while most of the screen remained unused.
      expect(dimensions.width).toBeGreaterThanOrEqual(Math.min(520, dimensions.viewport - 64))
      expect(dimensions.documentWidth).toBeLessThanOrEqual(dimensions.viewport + 1)
      await expect(page.locator(".publicEntryBrand")).toBeVisible()
      await email.fill("draft@example.test")
      const next = theme === "clear" ? "command" : "clear"
      fixture.setAppearance(next)
      await page.evaluate(() => window.dispatchEvent(new Event("focus")))
      await expect(page.locator("html")).toHaveAttribute("data-ui-theme", next)
      await expect(email).toHaveValue("draft@example.test")
      await page.getByRole("link", { name: "返回登录", exact: true }).click()
      await expect(page.getByRole("heading", { name: "登录工作台", exact: true })).toBeVisible()
      expect(fixture.unexpected).toEqual([])
      expect(fixture.mutations).toEqual([])
    })
  }
}
