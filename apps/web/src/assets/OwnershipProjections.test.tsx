import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"
import * as api from "./api"
import { MyAssetsPage } from "./MyAssetsPage"
import { PersonAssetsPanel } from "./PersonAssetsPanel"
import { AssetScanPage } from "./AssetScanPage"

afterEach(() => vi.restoreAllMocks())
const details: api.Asset = {
  ownership_type: "rented", rental_started_on: "2026-01-01", rental_expires_on: "2026-10-31", rental_returned_on: null,
  rental_revision: 0, acquisition_method: null, acquisition_channel: null, acquisition_note: null,
  id: "asset", asset_code: "AST-1", name: "租赁显示器", status: "assigned", photo: null,
  category: "台式电脑", cpu_model: "R7", memory_capacity: "32GB", gpu_model: "核显", disk_capacity: null, brand: null, model: null, serial_number: null, location: "训练室", purchase_date: null,
  warranty_expires_on: null, source_purchase_id: null, note: null, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", current_assignment: null,
}
it.each(["self", "person", "scan"])("extends %s projections with lease dates only and no management controls", async (view) => {
  vi.spyOn(api, "listMyAssets").mockResolvedValue([{ ...details, assigned_at: "2026-01-01T00:00:00Z", expected_return_at: null }])
  vi.spyOn(api, "listPersonAssets").mockResolvedValue([details])
  vi.spyOn(api, "getAssetScan").mockResolvedValue({ ...details, scanned_at: "2026-10-25T00:00:00Z" })
  render(<QueryClientProvider client={new QueryClient()}><MemoryRouter initialEntries={["/scan/AST-1"]}>
    {view === "self" ? <MyAssetsPage /> : view === "person" ? <PersonAssetsPanel personId="person" /> : <Routes><Route path="/scan/:assetCode" element={<AssetScanPage />} /></Routes>}
  </MemoryRouter></QueryClientProvider>)
  expect(await screen.findByText("归属：租用")).toBeVisible()
  expect(screen.getByText("CPU 型号：R7")).toBeVisible()
  expect(screen.getByText("内存容量：32GB")).toBeVisible()
  expect(screen.getByText("GPU 型号：核显")).toBeVisible()
  expect(screen.getByText("硬盘容量：待补充")).toBeVisible()
  expect(screen.getByText("租期：2026-01-01 至 2026-10-31（含当天）")).toBeVisible()
  expect(screen.queryByRole("button", { name: "续期" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "退租" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "续期历史" })).not.toBeInTheDocument()
})
