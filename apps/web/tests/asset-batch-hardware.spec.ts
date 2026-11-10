import { expect, test } from "@playwright/test"
import type { Asset, AssetInput } from "../src/assets/api"
import { installUiThemeFixtures } from "./helpers/uiThemeFixtures"

// Real routed UI, entirely synthetic API. Never forward a business request.
test.beforeEach(async ({ baseURL }) => {
  if (!baseURL || !["127.0.0.1", "localhost"].includes(new URL(baseURL).hostname)) {
    throw new Error("Asset batch acceptance requires an isolated loopback web server")
  }
})

type Batch = { operation_key: string; quantity: number; asset: AssetInput }
const hardware = {
  cpu_model: "Intel i7-14700",
  memory_capacity: "32GB",
  gpu_model: "核显",
  disk_capacity: "1TB SSD + 2TB HDD",
}

for (const theme of ["clear", "command"] as const) {
  test(`${theme}: rental batch preserves configuration and retry identity at desktop and phone sizes`, async ({ page }, info) => {
    const fixture = await installUiThemeFixtures(page, theme)
    const requests: Batch[] = []
    let created: Asset[] = []
    let listReads = 0
    const pageErrors: string[] = []
    page.on("pageerror", error => pageErrors.push(error.message))
    await page.route(/\/api\/v1\/assets(?:\?.*)?$/, async route => {
      expect(route.request().method()).toBe("GET")
      listReads++
      await route.fulfill({ json: { items: created, page: 1, page_size: 50, total: created.length } })
    })
    await page.route("**/api/v1/assets/batch", async route => {
      expect(route.request().method()).toBe("POST")
      const input = route.request().postDataJSON() as Batch
      requests.push(input)
      if (requests.length === 1) {
        await route.fulfill({ status: 503, json: { status: 503, code: "test_unavailable", detail: "模拟网络结果不确定，请重试同一批次" } })
        return
      }
      // Literal expected count, unique codes and fixed dates do not use production helpers.
      created = [1, 2, 3].map(index => ({
        ...input.asset,
        id: `a0000000-0000-4000-8000-00000000000${index}`,
        asset_code: `SYN-BATCH-${index}`,
        name: "合成训练电脑",
        category: "台式电脑", brand: null, model: null, serial_number: null,
        status: "available", location: "合成训练室", purchase_date: null,
        warranty_expires_on: null, source_purchase_id: null, note: null,
        created_at: "2026-10-28T00:00:00Z", updated_at: "2026-10-28T00:00:00Z",
        current_assignment: null, photo: null,
        ownership_type: "rented", rental_started_on: "2026-10-28",
        rental_expires_on: "2027-01-28", rental_returned_on: null, rental_revision: 0,
      }))
      await route.fulfill({ status: 201, json: { operation_key: input.operation_key, items: created, total: 3 } })
    })

    await page.goto("/assets")
    await expect(page.locator("html")).toHaveAttribute("data-ui-theme", theme)
    await page.getByRole("button", { name: "批量新增租用资产", exact: true }).click()
    const dialog = page.getByRole("dialog", { name: "批量新增租用资产", exact: true })
    await dialog.getByLabel("资产名称", { exact: true }).fill("合成训练电脑")
    await dialog.getByLabel("资产分类", { exact: true }).selectOption("台式电脑")
    await dialog.getByLabel("批量数量", { exact: true }).fill("3")
    await dialog.getByLabel("CPU 型号", { exact: true }).fill(hardware.cpu_model)
    await dialog.getByLabel("内存容量", { exact: true }).fill(hardware.memory_capacity)
    await dialog.getByLabel("GPU 型号", { exact: true }).fill(hardware.gpu_model)
    await dialog.getByLabel("硬盘容量", { exact: true }).fill(hardware.disk_capacity)
    await dialog.getByLabel("存放位置", { exact: true }).fill("合成训练室")
    await dialog.getByLabel("租用开始日期", { exact: true }).fill("2026-10-28")
    await dialog.getByLabel("租用到期日期", { exact: true }).fill("2027-01-28")
    await expect(dialog.getByLabel("序列号", { exact: true })).toHaveCount(0)
    await expect(dialog.locator('input[type="file"]')).toHaveCount(0)
    expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    await page.screenshot({ path: info.outputPath(`${theme}-batch-form.png`), fullPage: true })

    const readsBefore = listReads
    await dialog.getByRole("button", { name: "创建租用资产", exact: true }).click()
    await expect(dialog.getByRole("button", { name: "重试同一批次", exact: true })).toBeVisible()
    await expect(dialog.getByLabel("批量数量", { exact: true })).toBeDisabled()
    await expect(dialog.getByLabel("CPU 型号", { exact: true })).toBeDisabled()
    await dialog.getByRole("button", { name: "重试同一批次", exact: true }).click()
    await expect(dialog).toContainText("已创建 3 台")
    expect(requests).toHaveLength(2)
    expect(requests[1]).toEqual(requests[0])
    expect(requests[0].operation_key).toMatch(/^[0-9a-f-]{36}$/)
    expect(requests[0]).toMatchObject({ quantity: 3, asset: { ...hardware, ownership_type: "rented", name: "合成训练电脑", category: "台式电脑" } })
    expect(requests[0].asset.serial_number ?? null).toBeNull()
    await dialog.getByRole("button", { name: "关闭", exact: true }).click()
    await expect.poll(() => listReads).toBeGreaterThan(readsBefore)
    await expect(page.locator(".assetTableRow")).toHaveCount(3)
    await expect(page.locator(".assetTableRow").first()).toContainText(hardware.disk_capacity)
    await expect(page.locator(".assetTableRow").first()).toContainText(hardware.gpu_model)
    await page.screenshot({ path: info.outputPath(`${theme}-batch-created.png`), fullPage: true })
    expect(pageErrors).toEqual([])
    expect(fixture.unexpected).toEqual([])
    expect(fixture.mutations).toEqual([])
  })
}
