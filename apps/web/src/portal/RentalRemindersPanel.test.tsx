import { fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { expect, it, vi } from "vitest"

import { RentalRemindersPanel } from "./RentalRemindersPanel"
import type { useRentalReminders } from "./useRentalReminders"

function state(overrides = {}) {
  return { query: { isPending: false, isFetching: false, isError: false, data: { available: true, can_manage: false, business_date: "2026-10-25", page: 1, page_size: 20, total: 21, items: [
    { asset_id: "one", asset_code: "RENT-1", name: "租用电脑", person_name: "测试使用人", location: "一号室", rental_expires_on: "2026-10-24", days_remaining: -1, rental_revision: 1 },
    { asset_id: "two", asset_code: "RENT-2", name: "租用显示器", person_name: null, location: null, rental_expires_on: "2026-11-01", days_remaining: 7, rental_revision: 0 },
  ] }, ...overrides }, page: 1, setPage: vi.fn(), retry: vi.fn() } as unknown as ReturnType<typeof useRentalReminders>
}
it("shows independent rental metadata, overdue/near expiry and authorized management navigation", () => {
  const value = state()
  render(<MemoryRouter><RentalRemindersPanel state={value} /></MemoryRouter>)
  expect(screen.getByRole("heading", { name: "租用资产到期提醒" })).toBeVisible()
  for (const text of ["RENT-1", "租用电脑", "测试使用人", "一号室", "2026-10-24", "已逾期 1 天", "7 天后到期", "未分配", "地点未填写"]) expect(screen.getByText(text)).toBeVisible()
  expect(screen.getByRole("link", { name: "查看资产台账" })).toHaveAttribute("href", "/assets")
  expect(screen.queryByRole("button", { name: "续期" })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole("button", { name: "下一页租期提醒" }))
  expect(value.setPage).toHaveBeenCalledWith(2)
})
it("hides unavailable capability and never renders stale data during fetching/errors", () => {
  const view = render(<MemoryRouter><RentalRemindersPanel state={state({ data: { available: false } })} /></MemoryRouter>)
  expect(screen.queryByRole("heading")).not.toBeInTheDocument()
  view.rerender(<MemoryRouter><RentalRemindersPanel state={state({ isFetching: true })} /></MemoryRouter>)
  expect(screen.getByRole("status")).toHaveTextContent("正在更新租期提醒")
  expect(screen.queryByText("租用电脑")).not.toBeInTheDocument()
  const failed = state({ isError: true })
  view.rerender(<MemoryRouter><RentalRemindersPanel state={failed} /></MemoryRouter>)
  expect(screen.getByRole("alert")).toHaveTextContent("租期提醒暂时无法更新")
  expect(screen.queryByText("租用电脑")).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole("button", { name: "重试租期提醒" }))
  expect(failed.retry).toHaveBeenCalledOnce()
})
it("shows the independently empty state", () => {
  render(<MemoryRouter><RentalRemindersPanel state={state({ data: { available: true, items: [], total: 0, page_size: 20, business_date: "2026-10-25" } })} /></MemoryRouter>)
  expect(screen.getByText("暂无近七日到期或已逾期的租用资产")).toBeVisible()
})
