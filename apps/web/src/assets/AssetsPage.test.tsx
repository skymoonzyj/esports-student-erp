import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { readFileSync } from "node:fs"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import * as api from "./api"
import * as peopleApi from "../people/api"
import { AssetsPage } from "./AssetsPage"
import { AssetWarrantyDetails } from "./AssetWarrantyDetails"

afterEach(() => vi.restoreAllMocks())

it("saves the selected warranty channel and reloads its fields when editing", async () => {
  const asset: api.Asset = {
    id: "asset-1", asset_code: "AST-1", name: "训练电脑", category: null,
    brand: null, model: null, serial_number: null, status: "available", location: null,
    purchase_date: null, warranty_expires_on: "2099-12-31", source_purchase_id: null,
    note: null, created_at: "", updated_at: "", current_assignment: null, photo: null,
    warranty_channel: "offline", warranty_service_location: "维修中心",
    warranty_contact_name: "张先生", warranty_contact_phone: "400-123-4567",
  }
  vi.spyOn(api, "listAssets").mockResolvedValue({ items: [asset], page: 1, page_size: 50, total: 1 })
  const save = vi.spyOn(api, "updateAsset").mockResolvedValue(asset)
  render(<QueryClientProvider client={new QueryClient()}>
    <AssetsPage />
  </QueryClientProvider>)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "编辑" }))
  const dialog = within(screen.getByRole("dialog"))
  expect(dialog.getByLabelText("售后联系人")).toHaveValue("张先生")
  expect(dialog.getByLabelText("售后电话")).toHaveValue("400-123-4567")
  await user.selectOptions(dialog.getByLabelText("保修方式"), "online")
  expect(dialog.queryByLabelText("售后联系人")).not.toBeInTheDocument()
  await user.type(dialog.getByLabelText("产品链接"), "https://shop.example.com/item/1")
  await user.type(dialog.getByLabelText("订单号"), "00012345678901234567890")
  await user.click(dialog.getByRole("button", { name: "保存资产" }))
  await waitFor(() => expect(save).toHaveBeenCalledWith("asset-1", expect.objectContaining({
    warranty_channel: "online", warranty_contact_name: null, warranty_contact_phone: null,
    warranty_service_location: null, warranty_product_url: "https://shop.example.com/item/1",
    warranty_order_number: "00012345678901234567890",
  })))
})

it("retains expired warranty information and links to the online product", async () => {
  render(<AssetWarrantyDetails asset={{
    warranty_expires_on: "2000-01-01", warranty_channel: "online",
    warranty_product_url: "https://shop.example.com/item/1", warranty_order_number: "000123",
  }} />)
  await userEvent.setup().click(screen.getByText("已过保 · 查看售后信息"))
  expect(screen.getByText("000123")).toBeVisible()
  expect(screen.getByRole("link", { name: "打开商品详情" })).toHaveAttribute("href", "https://shop.example.com/item/1")
})

it("keeps the selected-person hint and assignment draft unchanged when People styles prepare", async () => {
  const asset: api.Asset = {
    id: "asset-style", asset_code: "AST-STYLE", name: "样式测试电脑", category: null,
    brand: null, model: null, serial_number: null, status: "available", location: null,
    purchase_date: null, warranty_expires_on: null, source_purchase_id: null,
    note: null, created_at: "", updated_at: "", current_assignment: null, photo: null,
    warranty_channel: null,
  }
  vi.spyOn(api, "listAssets").mockResolvedValue({ items: [asset], page: 1, page_size: 50, total: 1 })
  vi.spyOn(peopleApi, "listPeople").mockResolvedValue({ items: [{ id: "person-style", full_name: "合成人员", person_types: ["student"],
    nickname: null, phone: null, email: null, sex: "male", date_of_birth: "2000-01-01", identity_number: "synthetic",
    created_at: "", staff_type: null, age_status: "adult", guardian_status: "not_applicable", assisted_kind: null,
    account_status: "not_activated" }], total: 1, page: 1, page_size: 20 })
  render(<QueryClientProvider client={new QueryClient()}><AssetsPage /></QueryClientProvider>)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "分配" }))
  await user.type(screen.getByLabelText("搜索人员"), "合成")
  await user.click(screen.getByRole("button", { name: "查找" }))
  await user.click(await screen.findByRole("radio"))
  const hint = await screen.findByText("已选择：合成人员")
  const note = screen.getByLabelText("备注")
  await user.type(note, "保留领用草稿")
  const styles = ["styles/tokens.css", "styles/global.css", "styles/shared-controls.css", "assets/assets.css"].map(path => {
    const style = document.createElement("style")
    style.textContent = readFileSync(`src/${path}`, "utf8"); document.head.append(style); return style
  })
  const prepared = document.createElement("style")
  try {
    const appearance = () => { const s = getComputedStyle(hint); return [s.fontSize, s.color, s.margin] }
    const before = appearance()
    prepared.textContent = readFileSync("src/people/people.css", "utf8"); document.head.append(prepared)
    expect(appearance()).toEqual(before)
    expect(screen.getByText("已选择：合成人员")).toBe(hint)
    expect(note).toHaveValue("保留领用草稿")
    expect(note).toHaveFocus()
  } finally { prepared.remove(); styles.forEach(style => style.remove()) }
})
