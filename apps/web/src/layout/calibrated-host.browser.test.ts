// @vitest-environment node
import { chromium, type Browser } from "@playwright/test"
import { createServer, type ViteDevServer } from "vite"
import { afterAll, beforeAll, expect, it } from "vitest"

// The fixture mounts production shell/chrome/game/store; only the public server
// response is synthetic. These baseline x/width/height values predate the
// redesign and do not depend on OS font metrics. The archived-baseline QA also
// checks initial y in the same browser; CI checks exact full rect during play.
const baseline = [
  [390, 16, 358, 420],
  [768, 30.71875, 706.5625, 500],
  [1023, 40.90625, 941.1875, 500],
  [1024, 288.953125, 694.09375, 500],
  [1280, 299.1875, 929.625, 500],
  [1440, 300, 1088, 500],
  [1920, 508, 1152, 500],
] as const
const entry = `
import React from 'react'; import {createRoot} from 'react-dom/client';
import {MemoryRouter} from 'react-router-dom';
import {AppShell} from '/src/layout/AppShell.tsx';
import {GameChrome} from '/src/talentTests/GameChrome.tsx';
import {AimGame} from '/src/talentTests/games/aim/AimGame.tsx';
import {appearanceStore} from '/src/appearance/store.ts';
import '/src/styles/tokens.css'; import '/src/styles/global.css';
window.interruptions=[];window.completed=[];
window.refreshAppearance=()=>appearanceStore.refresh();appearanceStore.start();
createRoot(document.getElementById('root')).render(React.createElement(MemoryRouter,{initialEntries:['/talent-tests/sessions/synthetic/aim']},
React.createElement(AppShell,null,React.createElement(GameChrome,{title:'校准场地',instructions:'保持原始场地尺寸与目标规则。',statusText:null},
React.createElement(AimGame,{onComplete:r=>window.completed.push(r),onInterrupted:r=>window.interruptions.push(r)})))));
`
let server: ViteDevServer
let browser: Browser
beforeAll(async () => {
  server = await createServer({ configFile: false, server: { host: "127.0.0.1", port: 0 }, plugins: [{
    name: "calibrated-host-regression",
    resolveId(id) { if (id === "virtual:calibrated-host") return "\0calibrated-host" },
    load(id) { if (id === "\0calibrated-host") return entry },
    configureServer(instance) {
      instance.middlewares.use("/__calibrated-host", async (_request, response) => {
        response.setHeader("Content-Type", "text/html")
        response.end(await instance.transformIndexHtml("/__calibrated-host", '<meta name="viewport" content="width=device-width, initial-scale=1"><div id="root"></div><script type="module" src="/@id/virtual:calibrated-host"></script>'))
      })
    },
  }] })
  await server.listen()
  browser = await chromium.launch({ headless: true })
}, 30_000)
afterAll(async () => { await browser?.close(); await server?.close() })

it.each(baseline)("preserves initial calibration and active input through a newer global appearance revision at %ipx", async (width, x, arenaWidth, height) => {
  for (const initial of ["clear", "command"] as const) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, serviceWorkers: "block" })
    try {
      const page = await context.newPage()
      const errors: string[] = [], blocked: string[] = []
      page.on("pageerror", error => errors.push(error.message))
      let appearance = { theme: initial as "clear" | "command", revision: 1 }
      const base = server.resolvedUrls!.local[0]
      await page.route("**/*", route => {
        const request = route.request(), url = new URL(request.url())
        if (url.origin !== new URL(base).origin || (url.pathname.startsWith("/api/v1/") && (url.pathname !== "/api/v1/appearance" || request.method() !== "GET"))) {
          blocked.push(request.method() + " " + url.pathname)
          return route.abort()
        }
        return url.pathname === "/api/v1/appearance" ? route.fulfill({ json: appearance }) : route.continue()
      })
      await page.goto(base + "__calibrated-host")
      await page.locator(`html[data-ui-theme="${initial}"]`).waitFor()
      const arena = page.locator(".aim-game")
      await arena.waitFor()
      expect(await arena.boundingBox()).toMatchObject({ x, width: arenaWidth, height })
      const start = page.getByRole("button", { name: "点击目标开始" })
      if (initial === "clear") { await start.focus(); await page.keyboard.press("Enter") }
      else await start.click()
      await page.getByRole("application", { name: "瞄准训练游戏区域" }).waitFor()
      const target = page.getByRole("button", { name: /击中目标/ })
      // Spawn animation scales the painted bounds; calibration uses CSS diameter.
      expect(await target.evaluate(element => parseFloat(getComputedStyle(element).width))).toBe(arenaWidth < 700 ? 64 : 100)
      await target.click()
      await page.evaluate(() => scrollTo(0, 0))
      const progress = await page.locator(".aim-game__counter").textContent()
      const beforeRevision = await arena.boundingBox()
      appearance = { theme: initial === "clear" ? "command" : "clear", revision: 2 }
      await page.evaluate(async () => {
        await (window as unknown as { refreshAppearance: () => Promise<void> }).refreshAppearance()
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
      })
      expect(await page.locator("html").getAttribute("data-ui-theme")).toBe(appearance.theme)
      expect(await arena.boundingBox()).toEqual(beforeRevision)
      expect(await page.evaluate(() => (window as unknown as { interruptions: string[] }).interruptions)).toEqual([])
      expect(await page.getByRole("application", { name: "瞄准训练游戏区域" }).isVisible()).toBe(true)
      expect(await page.locator(".aim-game__counter").textContent()).toBe(progress)
      await page.getByRole("button", { name: /击中目标/ }).click()
      expect(await page.locator(".aim-game__counter").textContent()).not.toBe(progress)
      expect(await page.evaluate(() => (window as unknown as { completed: unknown[] }).completed)).toEqual([])
      expect(errors).toEqual([])
      expect(blocked).toEqual([])
    } finally { await context.close() }
  }
}, 30_000)
