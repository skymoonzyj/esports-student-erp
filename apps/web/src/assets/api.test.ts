import { afterEach, expect, it, vi } from "vitest"

import { listAssetCategories } from "./api"

afterEach(() => vi.unstubAllGlobals())

it("reads the server category catalog without replacing legacy values", async () => {
  const catalog = {
    groups: [{ key: "computers", label: "电脑与电竞设备", options: ["台式电脑", "显示器"] }],
    legacy_categories: ["旧训练设备"],
  }
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(catalog), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }))
  vi.stubGlobal("fetch", fetch)

  await expect(listAssetCategories()).resolves.toEqual(catalog)
  expect(fetch.mock.calls[0][0]).toBe("/api/v1/assets/categories")
})
