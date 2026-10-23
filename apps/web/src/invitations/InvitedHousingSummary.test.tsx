import { render, screen } from "@testing-library/react"
import { expect, it } from "vitest"
import { InvitedHousingSummary } from "./InvitedHousingSummary"

it("separates planned and actual dates in China time without subtracting a day", () => {
  render(<InvitedHousingSummary summary={{ status: "reserved", next_planned_stay: null, stay: {
    building_name: "一号楼", floor_name: "四层", room_name: "4-202", duration_mode: "fixed_term",
    planned_check_in_at: "2026-10-10T18:00:00Z", planned_check_out_at: "2026-10-13T00:00:00Z",
    actual_check_in_at: null, actual_check_out_at: null,
  } }} />)
  expect(screen.getByText("2026-10-11")).toBeVisible()
  expect(screen.getByText("2026-10-13")).toBeVisible()
  expect(screen.getByText("实际入住")).toBeVisible()
  expect(screen.getAllByText("—")).toHaveLength(2)
})
it("distinguishes an authorized person with no housing from an empty invitation list", () => {
  render(<InvitedHousingSummary summary={{ status: "unarranged", stay: null, next_planned_stay: null }} />)
  expect(screen.getByText("未安排住宿")).toBeVisible()
})
it("retains the current room while showing a separate next long-term reservation and missing dates", () => {
  const stay = { building_name: "一号楼", floor_name: "四层", room_name: "4-201", duration_mode: "fixed_term" as const,
    planned_check_in_at: "2026-10-10T00:00:00Z", planned_check_out_at: null, actual_check_in_at: "2026-10-11T00:00:00Z", actual_check_out_at: null }
  render(<InvitedHousingSummary summary={{ status: "checkout_pending", stay, next_planned_stay: { ...stay, room_name: "4-202", duration_mode: "long_term", actual_check_in_at: null } }} />)
  expect(screen.getByText("待确认退房")).toBeVisible()
  expect(screen.getByText("一号楼 / 四层 / 4-201")).toBeVisible()
  expect(screen.getByText("一号楼 / 四层 / 4-202")).toBeVisible()
  expect(screen.getByText("下一次计划")).toBeVisible()
  expect(screen.getByText("长期入住")).toBeVisible()
  expect(screen.getByText("未确定")).toBeVisible()
})
