import { expect, test } from "@playwright/test"
import { installUiThemeFixtures } from "./helpers/uiThemeFixtures"

test.beforeEach(async ({ baseURL }) => {
  if (!baseURL || !["127.0.0.1", "localhost"].includes(new URL(baseURL).hostname)) {
    throw new Error("UI theme validation must run against an isolated loopback web server")
  }
})

for (const theme of ["clear", "command"] as const) {
  test(`${theme}: housing summary shortcuts retain the selected building and floor`, async ({ page }) => {
    const fixture = await installUiThemeFixtures(page, theme)
    const queries: Record<string, string>[] = []
    page.on("request", request => {
      const url = new URL(request.url())
      if (url.pathname === "/api/v1/housing/overview") queries.push(Object.fromEntries(url.searchParams))
    })
    await page.goto("/housing")
    await expect(page.locator(".housingRoomGrid")).toBeVisible()
    const mobile = page.viewportSize()!.width < 720
    if (mobile) await page.getByRole("button", { name: "打开筛选", exact: true }).click()
    await page.getByRole("combobox", { name: "楼栋", exact: true }).selectOption("a")
    await page.getByRole("combobox", { name: "楼层", exact: true }).selectOption("a3")
    await expect.poll(() => queries.at(-1)).toMatchObject({ building_id: "a", floor_id: "a3" })
    if (mobile) await page.getByRole("button", { name: "关闭筛选", exact: true }).click()
    for (const [name, criteria] of [
      ["按空房筛选房间", { quick_filter: "vacant" }],
      ["按可入住筛选房间", { quick_filter: "assignable" }],
      ["按满房筛选房间", { quick_filter: "full" }],
      ["按每日保洁筛选房间", { quick_filter: "cleaning_daily" }],
      ["按每周保洁筛选房间", { quick_filter: "cleaning_weekly" }],
      ["按需保洁筛选房间", { quick_filter: "cleaning_on_demand" }],
    ] as const) {
      await page.getByRole("button", { name, exact: true }).click()
      await expect.poll(() => queries.at(-1)).toMatchObject({ building_id: "a", floor_id: "a3", ...criteria, page: "1" })
      expect(queries.at(-1)).not.toHaveProperty("occupancy")
      expect(queries.at(-1)).not.toHaveProperty("assignability")
      await expect(page.locator(".housingRoomGrid")).toBeVisible()
    }
    if (mobile) await page.getByRole("button", { name: "打开筛选", exact: true }).click()
    await expect(page.getByRole("combobox", { name: "楼栋", exact: true })).toHaveValue("a")
    await expect(page.getByRole("combobox", { name: "楼层", exact: true })).toHaveValue("a3")
    await expect(page.getByRole("combobox", { name: "可入住状态", exact: true })).toHaveValue("")
    await expect(page.getByRole("combobox", { name: "入住状态", exact: true })).toHaveCount(0)
    if (mobile) await page.getByRole("button", { name: "关闭筛选", exact: true }).click()
    await page.getByRole("button", { name: "查看全部房间", exact: true }).click()
    // Returning to a cached query need not make another request. Check visible
    // state, then a new combination proves hidden occupancy was also cleared.
    if (mobile) await page.getByRole("button", { name: "打开筛选", exact: true }).click()
    await expect(page.getByRole("combobox", { name: "楼栋", exact: true })).toHaveValue("a")
    await expect(page.getByRole("combobox", { name: "楼层", exact: true })).toHaveValue("a3")
    await expect(page.getByRole("combobox", { name: "可入住状态", exact: true })).toHaveValue("")
    await page.getByRole("textbox", { name: "搜索房间", exact: true }).fill("301")
    await expect.poll(() => queries.at(-1)).toEqual({ building_id: "a", floor_id: "a3", query: "301", page: "1", page_size: "20" })
    if (mobile) await page.getByRole("button", { name: "关闭筛选", exact: true }).click()
    await page.getByRole("button", { name: "按可入住筛选房间", exact: true }).click()
    await expect.poll(() => queries.at(-1)).toEqual({ building_id: "a", floor_id: "a3", query: "301", quick_filter: "assignable", page: "1", page_size: "20" })
    if (mobile) await page.getByRole("button", { name: "打开筛选", exact: true }).click()
    await page.getByRole("button", { name: "重置筛选", exact: true }).click()
    await page.getByRole("button", { name: "按已用房间数筛选房间", exact: true }).click()
    await expect.poll(() => queries.at(-1)).toEqual({ quick_filter: "occupied", page: "1", page_size: "20" })
    expect(fixture.unexpected).toEqual([])
    expect(fixture.mutations).toEqual([])
  })

  test(`${theme}: real routed pages preserve layout, navigation and drafts`, async ({ page }, info) => {
    const fixture = await installUiThemeFixtures(page, theme)
    const errors: string[] = []
    page.on("pageerror", error => errors.push(error.message))
    for (const [path, selector] of [["/", ".portalPage"], ["/housing", ".housingPage"], ["/people", ".peoplePage"], ["/settings/operations", ".operationsPage"]]) {
      await page.goto(path)
      await expect(page.locator(selector)).toBeVisible()
      await expect(page.locator("html")).toHaveAttribute("data-ui-theme", theme)
      await expect(page.getByText("Unexpected Application Error!", { exact: true })).toHaveCount(0)
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
      await page.screenshot({ path: info.outputPath(`${theme}-${path.replaceAll("/", "_") || "home"}.png`), fullPage: true })
    }
    await page.getByRole("radio", { name: theme === "clear" ? /深蓝效率/ : /清爽专业/ }).check()
    await expect(page.locator("html")).toHaveAttribute("data-ui-theme", theme)
    await page.getByRole("button", { name: "保存全局外观" }).click()
    const next = theme === "clear" ? "command" : "clear"
    await expect(page.locator("html")).toHaveAttribute("data-ui-theme", next)
    await page.goto("/people")
    await expect(page.getByRole("link", { name: "星河测试", exact: true })).toBeVisible()
    const input = page.locator('.peopleFilters input[type="search"], .peopleFilters input[type="text"], .peopleFilters input:not([type])').first()
    await input.fill("保留的筛选输入")
    fixture.setAppearance(theme)
    await page.evaluate(() => window.dispatchEvent(new Event("focus")))
    await expect(page.locator("html")).toHaveAttribute("data-ui-theme", theme)
    await expect(input).toHaveValue("保留的筛选输入")
    await expect(page).toHaveURL(/\/people$/)
    expect(errors).toEqual([])
    expect(fixture.unexpected).toEqual([])
    expect(fixture.mutations).toEqual(["/operations/appearance"])
  })
}

for (const theme of ["clear", "command"] as const) {
  test(`${theme}: asset row actions stay visible and clickable within the shell`, async ({ page }, info) => {
    const fixture = await installUiThemeFixtures(page, theme)
    const errors: string[] = []
    page.on("pageerror", error => errors.push(error.message))
    await page.goto("/assets")
    await expect(page.getByText("测试训练电脑", { exact: true })).toBeVisible()
    await expect(page.locator("html")).toHaveAttribute("data-ui-theme", theme)
    await expect(page.getByRole("button", { name: "二维码", exact: true })).toBeVisible()

    const viewport = page.viewportSize()!
    const widths = viewport.width === 1024 ? [1023, 1024, 1140, 1141] : [viewport.width]
    for (const width of widths) {
      await page.setViewportSize({ ...viewport, width })
      const actions = page.locator(".assetTableRow .assetRowActions button")
      for (const action of await actions.all()) {
        const bounds = await action.evaluate(button => {
          // Scroll only the document: scrollIntoView could silently scroll an
          // overflow:hidden table and conceal the clipping regression.
          const before = button.getBoundingClientRect()
          window.scrollTo(0, window.scrollY + before.top - innerHeight / 2)
          const rect = button.getBoundingClientRect()
          const directory = button.closest(".assetDirectory")!.getBoundingClientRect()
          const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)
          return {
            label: button.textContent,
            contained: rect.left >= directory.left && rect.right <= directory.right
              && rect.left >= 0 && rect.right <= innerWidth,
            receivesPointer: hit === button || button.contains(hit),
            buttonRight: rect.right,
            directoryRight: directory.right,
          }
        })
        expect(bounds, `${theme}/${width}px: ${bounds.label} must not be clipped by the asset directory`).toMatchObject({ contained: true, receivesPointer: true })
      }
    }
    await page.setViewportSize(viewport)
    await page.getByRole("button", { name: "二维码", exact: true }).click()
    await expect(page.getByRole("dialog", { name: "测试训练电脑", exact: true })).toBeVisible()
    await page.getByRole("button", { name: "关闭", exact: true }).click()
    await page.screenshot({ path: info.outputPath(`${theme}-asset-actions.png`), fullPage: true })
    expect(errors).toEqual([])
    expect(fixture.unexpected).toEqual([])
    expect(fixture.mutations).toEqual([])
  })

  test(`${theme}: financial pages and asset forms remain usable inside the actual shell`, async ({ page }, info) => {
    const fixture = await installUiThemeFixtures(page, theme)
    const errors: string[] = []
    page.on("pageerror", error => errors.push(error.message))
    for (const [path, content] of [["/assets", "测试训练电脑"], ["/procurement", "测试饮用水"], ["/finance/revenue-share", "测试教练"]]) {
      await page.goto(path)
      await expect(page.getByText(content, { exact: true })).toBeVisible()
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
      await page.screenshot({ path: info.outputPath(`${theme}-${path.replaceAll("/", "_")}.png`), fullPage: true })
    }
    await page.getByRole("button", { name: "登记打款" }).click()
    const settlement = page.getByRole("dialog", { name: "登记结算" })
    await expect(settlement).toBeVisible()
    await settlement.getByLabel("打款凭证号").fill("synthetic-not-submitted")
    fixture.setAppearance(theme === "clear" ? "command" : "clear")
    await page.evaluate(() => window.dispatchEvent(new Event("focus")))
    await expect(page.locator("html")).toHaveAttribute("data-ui-theme", theme === "clear" ? "command" : "clear")
    await expect(settlement.getByLabel("打款凭证号")).toHaveValue("synthetic-not-submitted")
    await settlement.getByRole("button", { name: "关闭", exact: true }).click()
    await page.goto("/assets")
    await page.getByRole("button", { name: "新增资产", exact: true }).click()
    const asset = page.getByRole("dialog")
    await expect(asset).toBeVisible()
    await asset.getByLabel("资产名称").fill("未提交的设备草稿")
    await expect(asset.getByRole("button", { name: "保存资产" })).toBeEnabled()
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    await asset.getByRole("button", { name: "取消", exact: true }).click()
    expect(errors).toEqual([])
    expect(fixture.unexpected).toEqual([])
    expect(fixture.mutations).toEqual([])
  })
}
