import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import * as api from "./api"
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
