import { createRef } from "react"
import { fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { expect, it, vi } from "vitest"

import type { HousingStay } from "./api"
import { HousingHistoryVoidDialog } from "./HousingHistoryVoidDialog"

const stay: HousingStay = {
  id: "stay-history", person_id: "person-history", room_id: "room-409", person_name: "历史学员",
  person_type: "student", sex: "male", stay_purpose: "temporary", duration_mode: "fixed_term",
  cohort_id: null, visit_reason: "交流", planned_check_in_at: "2026-07-01T09:00:00+08:00",
  planned_check_out_at: "2026-07-20T18:00:00+08:00", actual_check_in_at: "2026-07-01T09:00:00+08:00",
  actual_check_out_at: "2026-07-20T18:00:00+08:00", status: "checked_out", room_name_snapshot: "409",
  floor_name_snapshot: "四层", building_name_snapshot: "一号楼", notes: null, cancellation_reason: null,
  checkout_requested_actual_at: null, checkout_requested_at: null, version: 3,
  allowed_actions: { edit_planned_departure: false, edit_reservation: false, confirm_check_in: false,
    cancel_reservation: false, transfer: false, request_checkout: false, confirm_checkout: false },
  created_at: "2026-07-01T01:00:00Z", updated_at: "2026-07-20T10:00:00Z",
}

it("requiresReasonThenExplicitConfirmation", async () => {
  const user = userEvent.setup()
  const onSubmit = vi.fn()
  render(<HousingHistoryVoidDialog stay={stay} linkedTransfers={[{
    id: "transfer-1", person_id: stay.person_id, from_stay_id: "prior", to_stay_id: stay.id,
    reason: "原调房", transferred_at: "2026-07-01T09:00:00+08:00", created_at: "2026-07-01T01:00:00Z", updated_at: "2026-07-01T01:00:00Z",
  }]} pending={false} error={null} focusFallbackRef={createRef()} onSubmit={onSubmit} onClose={vi.fn()} />)
  expect(screen.getByRole("button", { name: "下一步：核对历史住宿" })).toBeDisabled()
  await user.type(screen.getByLabelText("作废原因"), "1")
  expect(screen.getByRole("button", { name: "下一步：核对历史住宿" })).toBeDisabled()
  await user.clear(screen.getByLabelText("作废原因"))
  await user.type(screen.getByLabelText("作废原因"), "13800138000")
  expect(screen.getByRole("button", { name: "下一步：核对历史住宿" })).toBeDisabled()
  await user.clear(screen.getByLabelText("作废原因"))
  await user.type(screen.getByLabelText("作废原因"), " 历史重复登记 ")
  await user.click(screen.getByRole("button", { name: "下一步：核对历史住宿" }))
  expect(screen.getByText("一号楼 · 四层 · 409")).toBeVisible()
  expect(screen.getByText("2026-07-01", { selector: "dd" })).toBeVisible()
  expect(screen.getByText(/2026-07-20/)).toBeVisible()
  expect(screen.getByText(/去向住宿/)).toBeVisible()
  expect(screen.getByText("只作废此条历史，不删除人员、账号和其他住宿")).toBeVisible()
  expect(screen.getByText("已生成报送不会自动改变")).toBeVisible()
  expect(screen.getByRole("checkbox")).toHaveFocus()
  const final = screen.getByRole("button", { name: "确认作废历史住宿" })
  expect(final).toBeDisabled()
  await user.click(screen.getByRole("checkbox"))
  await user.click(final)
  expect(onSubmit).toHaveBeenCalledWith({ expected_version: 3, reason: "历史重复登记", confirmed: true, idempotency_key: expect.any(String) })
})

it("retainsOperationKeyAfterTimeout and locks reason until a target readback", async () => {
  const user = userEvent.setup()
  const onSubmit = vi.fn()
  const props = { stay, linkedTransfers: [], pending: false, error: null as string | null, focusFallbackRef: createRef<HTMLElement>(), onSubmit, onClose: vi.fn() }
  const view = render(<HousingHistoryVoidDialog {...props} />)
  await user.type(screen.getByLabelText("作废原因"), "重复历史")
  await user.click(screen.getByRole("button", { name: "下一步：核对历史住宿" }))
  await user.click(screen.getByRole("checkbox"))
  fireEvent.click(screen.getByRole("button", { name: "确认作废历史住宿" }))
  fireEvent.click(screen.getByRole("button", { name: "确认作废历史住宿" }))
  expect(onSubmit).toHaveBeenCalledTimes(1)
  const first = onSubmit.mock.calls[0][0]
  view.rerender(<HousingHistoryVoidDialog {...props} pending />)
  expect(screen.getByRole("button", { name: "确认作废历史住宿" })).toBeDisabled()
  view.rerender(<HousingHistoryVoidDialog {...props} error="请求超时，结果不确定" />)
  expect(screen.queryByRole("textbox", { name: "作废原因" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "返回修改原因" })).not.toBeInTheDocument()
  expect(screen.getByText(/重新查询目标后才能修改原因/)).toBeVisible()
  await user.click(screen.getByRole("button", { name: "确认作废历史住宿" }))
  const retry = onSubmit.mock.calls[1][0]
  expect(retry.idempotency_key).toBe(first.idempotency_key)
  expect(retry.reason).toBe(first.reason)
})

it("can create an unpredictable UUID operation key when randomUUID is unavailable on a LAN origin", async () => {
  const getRandomValues = crypto.getRandomValues.bind(crypto)
  vi.stubGlobal("crypto", { getRandomValues })
  try {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<HousingHistoryVoidDialog stay={stay} linkedTransfers={[]} pending={false} error={null} focusFallbackRef={createRef()} onSubmit={onSubmit} onClose={vi.fn()} />)
    await user.type(screen.getByLabelText("作废原因"), "重复登记")
    await user.click(screen.getByRole("button", { name: "下一步：核对历史住宿" }))
    await user.click(screen.getByRole("checkbox"))
    await user.click(screen.getByRole("button", { name: "确认作废历史住宿" }))
    expect(onSubmit.mock.calls[0][0].idempotency_key).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
  } finally {
    vi.unstubAllGlobals()
  }
})
