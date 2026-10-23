import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { StrictMode } from "react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import type { HousingCatalog, HousingRoom, HousingRoomDetail, HousingStay } from "./api"
import {
  cancelHousingReservation,
  confirmHousingCheckIn,
  confirmHousingCheckout,
  createHousingStay,
  getHousingCatalog,
  getHousingCohortOptions,
  getHousingRoomDetail,
  getHousingTransferOptions,
  listHousingResidentOptions,
  reportHousingIssue,
  requestHousingCheckout,
  resolveHousingIssue,
  transferHousingStay,
  updateHousingStay,
} from "./api"
import { HousingRoomDetailPage } from "./HousingRoomDetailPage"
import { housingKeys } from "./housingKeys"

const accountId = "account-housing-manager"

vi.mock(import("../auth/AuthProvider"), () => ({
  useAuth: () => ({
    status: "authenticated" as const,
    user: {
      account_id: accountId,
      person_id: "person-housing-manager",
      full_name: "房态管理员",
      email: "housing-manager@example.test",
      force_password_change: false,
      permissions: ["housing.read"],
    },
    login: vi.fn(),
    logout: vi.fn(),
    changePassword: vi.fn(),
    refreshUser: vi.fn(),
  }),
}))

vi.mock(import("./api"), async (importOriginal) => ({
  ...await importOriginal(),
  cancelHousingReservation: vi.fn(),
  confirmHousingCheckIn: vi.fn(),
  confirmHousingCheckout: vi.fn(),
  createHousingStay: vi.fn(),
  getHousingCatalog: vi.fn(),
  getHousingCohortOptions: vi.fn(),
  getHousingRoomDetail: vi.fn(),
  getHousingTransferOptions: vi.fn(),
  listHousingResidentOptions: vi.fn(),
  reportHousingIssue: vi.fn(),
  requestHousingCheckout: vi.fn(),
  resolveHousingIssue: vi.fn(),
  transferHousingStay: vi.fn(),
  updateHousingStay: vi.fn(),
}))

const cancelReservationMock = vi.mocked(cancelHousingReservation)
const confirmCheckInMock = vi.mocked(confirmHousingCheckIn)
const confirmCheckoutMock = vi.mocked(confirmHousingCheckout)
const createStayMock = vi.mocked(createHousingStay)
const catalogMock = vi.mocked(getHousingCatalog)
const cohortOptionsMock = vi.mocked(getHousingCohortOptions)
const detailMock = vi.mocked(getHousingRoomDetail)
const transferOptionsMock = vi.mocked(getHousingTransferOptions)
const residentOptionsMock = vi.mocked(listHousingResidentOptions)
const reportIssueMock = vi.mocked(reportHousingIssue)
const requestCheckoutMock = vi.mocked(requestHousingCheckout)
const resolveIssueMock = vi.mocked(resolveHousingIssue)
const transferStayMock = vi.mocked(transferHousingStay)
const updateStayMock = vi.mocked(updateHousingStay)
const noStayActions = {
  edit_reservation: false,
  confirm_check_in: false,
  cancel_reservation: false,
  transfer: false,
  request_checkout: false,
  confirm_checkout: false,
}

function stay(overrides: Partial<HousingStay> = {}): HousingStay {
  return {
    id: "stay-current",
    person_id: "person-current",
    room_id: "room-409",
    stay_purpose: "camp",
    duration_mode: "fixed_term",
    cohort_id: "cohort-1",
    visit_reason: null,
    planned_check_in_at: "2026-08-01T09:00:00+08:00",
    planned_check_out_at: "2026-08-22T18:00:00+08:00",
    actual_check_in_at: "2026-08-01T09:05:00+08:00",
    actual_check_out_at: null,
    status: "checked_in",
    room_name_snapshot: "409",
    floor_name_snapshot: "四层",
    building_name_snapshot: "一号楼",
    notes: "靠窗床位",
    cancellation_reason: null,
    checkout_requested_actual_at: null,
    checkout_requested_at: null,
    person_name: "当前学员甲",
    person_type: "student",
    sex: "male",
    version: 3,
    allowed_actions: { ...noStayActions, transfer: true, request_checkout: true },
    created_at: "2026-08-01T01:00:00Z",
    updated_at: "2026-08-01T01:05:00Z",
    ...overrides,
  }
}

function room(overrides: Partial<HousingRoom> = {}): HousingRoom {
  return {
    id: "room-409",
    building_id: "building-a",
    floor_id: "floor-a4",
    building_code: "A",
    building_name: "一号楼",
    building_is_active: true,
    floor_number: 4,
    floor_name: "四层",
    floor_is_active: true,
    room_number: "409",
    room_type: "double",
    capacity: 2,
    area_square_meters: "22.00",
    verification_status: "verified",
    service_status: "available",
    notes: "训练区附近",
    version: 8,
    current_occupants: 1,
    remaining_capacity: 1,
    current_gender: "male",
    has_future_reservation: true,
    occupancy_status: "occupied",
    is_assignable: true,
    status_text: "已入住 1 / 2",
    allowed_actions: {
      edit: false,
      delete: false,
      verify: false,
      create_stay: true,
      report_issue: true,
      view_detail: true,
    },
    occupants: [stay()],
    future_reservation_count: 1,
    open_issue_summary: null,
    pending_transfer_count: 0,
    created_at: "2026-07-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    ...overrides,
  }
}

const catalog: HousingCatalog = {
  access_revision: "a".repeat(64),
  can_create_building: false,
  buildings: [],
  floors: [],
}

function detail(overrides: Partial<HousingRoomDetail> = {}): HousingRoomDetail {
  return {
    access_revision: catalog.access_revision,
    as_of: "2026-08-06T10:00:00+08:00",
    room: room(),
    current_stays: [stay()],
    future_reservations: [stay({
      id: "stay-future",
      person_id: "person-future",
      person_name: "预约教练乙",
      person_type: "coach",
      status: "planned",
      actual_check_in_at: null,
      allowed_actions: { ...noStayActions, edit_reservation: true, confirm_check_in: true, cancel_reservation: true },
    })],
    history: [stay({
      id: "stay-history",
      person_id: "person-history",
      person_name: "历史生活老师丙",
      person_type: "life_teacher",
      status: "checked_out",
      actual_check_out_at: "2026-07-20T18:00:00+08:00",
      allowed_actions: noStayActions,
    })],
    transfers: [{
      id: "transfer-1",
      person_id: "person-history",
      from_stay_id: "stay-old",
      to_stay_id: "stay-history",
      transferred_at: "2026-07-10T10:00:00+08:00",
      reason: "设备调整",
      created_at: "2026-07-10T10:00:00+08:00",
      updated_at: "2026-07-10T10:00:00+08:00",
    }],
    issues: [{
      id: "issue-1",
      room_id: "room-409",
      description: "空调故障",
      previous_service_status: "available",
      reported_at: "2026-08-02T08:00:00+08:00",
      status: "resolved",
      resolved_at: "2026-08-02T12:00:00+08:00",
      resolution_note: "已经维修完成",
      allowed_actions: { resolve: false },
      created_at: "2026-08-02T08:00:00+08:00",
      updated_at: "2026-08-02T12:00:00+08:00",
    }],
    ...overrides,
  }
}

function mountPage(queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/housing/rooms/room-409"]}>
        <Routes>
          <Route path="/housing/rooms/:roomId" element={<HousingRoomDetailPage />} />
          <Route path="/housing" element={<p>住宿房态</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { queryClient, ...view }
}

function renderPage(queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return mountPage(queryClient).queryClient
}

it("keeps the room detail query alive through StrictMode effect replay", async () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  queryClient.setQueryData(housingKeys.catalog(accountId), catalog)
  detailMock.mockImplementation((_roomId, signal) => {
    return new Promise((resolve, reject) => {
      const onAbort = () => {
        reject(new DOMException("The operation was aborted", "AbortError"))
      }
      if (signal?.aborted) {
        onAbort()
        return
      }
      signal?.addEventListener("abort", onAbort, { once: true })
      queueMicrotask(() => {
        signal?.removeEventListener("abort", onAbort)
        if (!signal?.aborted) resolve(detail())
      })
    })
  })

  render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/housing/rooms/room-409"]}>
          <Routes>
            <Route path="/housing/rooms/:roomId" element={<HousingRoomDetailPage />} />
            <Route path="/housing" element={<p>住宿房态</p>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </StrictMode>,
  )

  expect(await screen.findByRole("heading", { name: "房间 409" })).toBeVisible()
})

it("clears current private detail and option queries after a real unmount", async () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  queryClient.setQueryData(housingKeys.catalog(accountId), catalog)
  queryClient.setQueryData(housingKeys.detail(accountId, catalog.access_revision, "room-409"), detail())
  queryClient.setQueryData(housingKeys.cohortOptions(accountId, catalog.access_revision, "room-409"), { items: [{ id: "private-cohort" }] })
  queryClient.setQueryData(housingKeys.transferOptions(accountId, catalog.access_revision, "stay-current"), { items: [{ room_id: "private-room" }] })
  const cancelQueries = vi.spyOn(queryClient, "cancelQueries")
  const view = mountPage(queryClient)

  expect(await screen.findByRole("heading", { name: "房间 409" })).toBeVisible()
  view.unmount()
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })

  expect(cancelQueries).toHaveBeenCalledTimes(3)
  expect(cancelQueries).toHaveBeenCalledWith({ queryKey: housingKeys.details(accountId, catalog.access_revision) })
  expect(cancelQueries).toHaveBeenCalledWith({ queryKey: [...housingKeys.scope(accountId, catalog.access_revision), "cohort-options"] })
  expect(cancelQueries).toHaveBeenCalledWith({ queryKey: [...housingKeys.scope(accountId, catalog.access_revision), "transfer-options"] })
  expect(queryClient.getQueryData(housingKeys.detail(accountId, catalog.access_revision, "room-409"))).toBeUndefined()
  expect(queryClient.getQueryData(housingKeys.cohortOptions(accountId, catalog.access_revision, "room-409"))).toBeUndefined()
  expect(queryClient.getQueryData(housingKeys.transferOptions(accountId, catalog.access_revision, "stay-current"))).toBeUndefined()
})

beforeEach(() => {
  cancelReservationMock.mockReset()
  confirmCheckInMock.mockReset()
  confirmCheckoutMock.mockReset()
  createStayMock.mockReset()
  catalogMock.mockReset().mockResolvedValue(catalog)
  cohortOptionsMock.mockReset().mockResolvedValue({ access_revision: catalog.access_revision, items: [] })
  detailMock.mockReset().mockResolvedValue(detail())
  transferOptionsMock.mockReset().mockResolvedValue({
    access_revision: catalog.access_revision,
    items: [{
      room_id: "room-410",
      building_label: "一号楼",
      floor_label: "四层",
      room_label: "410",
      capacity: 2,
      current_occupants: 0,
      current_gender: null,
      advisory_compatible: true,
      status_text: "可入住",
    }],
  })
  residentOptionsMock.mockReset().mockResolvedValue({
    access_revision: catalog.access_revision,
    items: [{
      person_id: "person-new",
      full_name: "新增学员丁",
      person_type: "student",
      sex: "male",
      masked_phone: "138****9000",
      account_ready: true,
      housing_ready: true,
      profile_ready: true,
      guardian_ready: true,
    }],
  })
  reportIssueMock.mockReset().mockResolvedValue(detail().issues[0])
  requestCheckoutMock.mockReset()
  resolveIssueMock.mockReset()
  transferStayMock.mockReset()
  updateStayMock.mockReset()
})

it("renders the complete room timeline and only server-authorized room actions", async () => {
  renderPage()

  expect(await screen.findByRole("heading", { name: "房间 409" })).toBeVisible()
  for (const heading of ["当前入住", "未来预约", "住宿历史", "调房记录", "问题记录"]) {
    expect(screen.getByRole("heading", { name: heading })).toBeVisible()
  }
  for (const value of ["当前学员甲", "预约教练乙", "历史生活老师丙", "设备调整", "空调故障", "已经维修完成"]) {
    expect(screen.getByText(value)).toBeVisible()
  }
  expect(screen.getByRole("button", { name: "安排入住" })).toBeEnabled()
  expect(screen.getByRole("button", { name: "上报问题" })).toBeEnabled()
  for (const action of [
    "调房 当前学员甲",
    "申请退房 当前学员甲",
    "编辑预约 预约教练乙",
    "确认入住 预约教练乙",
    "取消预约 预约教练乙",
  ]) {
    expect(screen.getByRole("button", { name: action })).toBeEnabled()
  }
  expect(screen.queryByText(/身份证|13800138000/)).not.toBeInTheDocument()
})

it("reports a room issue with the opening room version", async () => {
  const user = userEvent.setup()
  renderPage()
  await screen.findByRole("heading", { name: "房间 409" })

  await user.click(screen.getByRole("button", { name: "上报问题" }))
  const dialog = screen.getByRole("dialog", { name: "上报问题 · 房间 409" })
  await user.type(within(dialog).getByLabelText("问题描述"), " 空调漏水 ")
  await user.click(within(dialog).getByRole("button", { name: "确认上报" }))

  expect(reportIssueMock).toHaveBeenCalledTimes(1)
  expect(reportIssueMock).toHaveBeenCalledWith("room-409", {
    room_version: 8,
    description: "空调漏水",
  })
})

it("requires checkout request before the second confirmation releases capacity", async () => {
  const user = userEvent.setup()
  const checkoutPending = stay({
    status: "checkout_pending",
    version: 4,
    checkout_requested_actual_at: "2026-08-06T10:00:00+08:00",
    checkout_requested_at: "2026-08-06T10:01:00+08:00",
    allowed_actions: { ...noStayActions, confirm_checkout: true },
  })
  requestCheckoutMock.mockResolvedValue(checkoutPending)
  confirmCheckoutMock.mockResolvedValue(stay({
    status: "checked_out",
    version: 5,
    actual_check_out_at: "2026-08-06T10:00:00+08:00",
    allowed_actions: noStayActions,
  }))
  detailMock
    .mockResolvedValueOnce(detail())
    .mockResolvedValue(detail({ current_stays: [checkoutPending] }))
  renderPage()
  await screen.findByRole("heading", { name: "房间 409" })

  await user.click(screen.getByRole("button", { name: "申请退房 当前学员甲" }))
  const requestDialog = screen.getByRole("dialog", { name: "申请退房 · 当前学员甲" })
  await user.click(within(requestDialog).getByRole("button", { name: "申请退房" }))

  await waitFor(() => expect(requestCheckoutMock).toHaveBeenCalledWith("stay-current", {
    version: 3,
    actual_check_out_at: "2026-08-06T00:00:00+08:00",
  }))
  expect(confirmCheckoutMock).not.toHaveBeenCalled()
  const confirmDialog = await screen.findByRole("dialog", { name: "二次确认退房 · 当前学员甲" })
  const confirmButton = within(confirmDialog).getByRole("button", { name: "二次确认退房" })
  await waitFor(() => expect(confirmButton).toHaveFocus())
  await user.click(confirmButton)

  await waitFor(() => expect(confirmCheckoutMock).toHaveBeenCalledWith("stay-current", { version: 4 }))
})

it("keeps a stale-version dialog open, refreshes, and never replays the mutation", async () => {
  const user = userEvent.setup()
  reportIssueMock.mockRejectedValue(new ProblemDetailsError({
    type: "about:blank",
    title: "Conflict",
    status: 409,
    detail: "版本冲突",
    code: "housing_stale_version",
    request_id: "housing-stale",
  }))
  renderPage()
  await screen.findByRole("heading", { name: "房间 409" })

  await user.click(screen.getByRole("button", { name: "上报问题" }))
  const dialog = screen.getByRole("dialog", { name: "上报问题 · 房间 409" })
  await user.type(within(dialog).getByLabelText("问题描述"), "门锁损坏")
  await user.click(within(dialog).getByRole("button", { name: "确认上报" }))

  expect(await within(dialog).findByRole("alert")).toHaveTextContent("数据已更新")
  expect(within(dialog).getByLabelText("问题描述")).toHaveValue("门锁损坏")
  expect(reportIssueMock).toHaveBeenCalledTimes(1)
  await waitFor(() => expect(detailMock.mock.calls.length).toBeGreaterThan(1))
  expect(reportIssueMock).toHaveBeenCalledTimes(1)
})

it("resolves an open issue with the room version captured on dialog open", async () => {
  const user = userEvent.setup()
  const openIssue = {
    ...detail().issues[0],
    status: "open" as const,
    resolved_at: null,
    resolution_note: null,
    allowed_actions: { resolve: true },
  }
  resolveIssueMock.mockResolvedValue({
    ...openIssue,
    status: "resolved",
    resolved_at: "2026-08-06T11:00:00+08:00",
    resolution_note: "更换空调外机",
    allowed_actions: { resolve: false },
  })
  detailMock.mockResolvedValue(detail({
    room: room({ version: 12, service_status: "problem", allowed_actions: { ...room().allowed_actions, report_issue: false } }),
    issues: [openIssue],
  }))
  renderPage()
  await screen.findByRole("heading", { name: "房间 409" })

  await user.click(screen.getByRole("button", { name: "解决问题 空调故障" }))
  const dialog = screen.getByRole("dialog", { name: "解决问题 · 房间 409" })
  await user.type(within(dialog).getByLabelText("处理说明"), " 更换空调外机 ")
  await user.click(within(dialog).getByRole("button", { name: "确认解决" }))

  await waitFor(() => expect(resolveIssueMock).toHaveBeenCalledWith("issue-1", {
    room_version: 12,
    resolution_note: "更换空调外机",
  }))
})

it("purges all private housing state after a mutation loses access", async () => {
  const user = userEvent.setup()
  reportIssueMock.mockRejectedValue(new ProblemDetailsError({
    type: "about:blank",
    title: "Forbidden",
    status: 403,
    detail: "权限已变化",
    code: "permission_denied",
    request_id: "issue-private",
  }))
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  queryClient.setQueryData(housingKeys.cohortOptions(accountId, catalog.access_revision, "room-409"), {
    items: [{ full_name: "必须清除的候选姓名" }],
  })
  const otherAccountKey = housingKeys.detail("account-other", "revision-other", "room-other")
  queryClient.setQueryData(otherAccountKey, { occupants: ["其他账号住户"] })
  renderPage(queryClient)
  await screen.findByRole("heading", { name: "房间 409" })

  await user.click(screen.getByRole("button", { name: "上报问题" }))
  const dialog = screen.getByRole("dialog", { name: "上报问题 · 房间 409" })
  await user.type(within(dialog).getByLabelText("问题描述"), "门窗问题")
  await user.click(within(dialog).getByRole("button", { name: "确认上报" }))

  expect(await screen.findByRole("alert")).toHaveTextContent("房间详情不可访问")
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  expect(document.body.textContent).not.toContain("当前学员甲")
  await waitFor(() => {
    expect(queryClient.getQueriesData({ queryKey: housingKeys.root(accountId) }).every(([, value]) => value === undefined)).toBe(true)
  })
  expect(queryClient.getQueryData(otherAccountKey)).toEqual({ occupants: ["其他账号住户"] })
})

it("creates a stay through the room-scoped action", async () => {
  const user = userEvent.setup()
  cohortOptionsMock.mockResolvedValue({
    access_revision: catalog.access_revision,
    items: [{
      id: "cohort-new",
      name: "新营期",
      status: "upcoming",
      default_check_in_at: "2026-08-10T08:00:00+08:00",
      default_check_out_at: "2026-08-31T18:00:00+08:00",
    }],
  })
  createStayMock.mockResolvedValue(stay({ id: "stay-new", person_id: "person-new", person_name: "新增学员丁", status: "planned", actual_check_in_at: null }))
  renderPage()
  await screen.findByRole("heading", { name: "房间 409" })

  await user.click(screen.getByRole("button", { name: "安排入住" }))
  const dialog = screen.getByRole("dialog", { name: "安排入住 · 409" })
  await user.type(within(dialog).getByRole("textbox", { name: "搜索入住人员" }), "学员")
  await user.click(await within(dialog).findByRole("radio", { name: /新增学员丁/ }))
  await user.selectOptions(within(dialog).getByLabelText("关联营期"), "cohort-new")
  await user.click(within(dialog).getByRole("button", { name: "创建预约" }))

  await waitFor(() => expect(createStayMock).toHaveBeenCalledWith("room-409", {
    person_id: "person-new",
    stay_purpose: "camp",
    duration_mode: "fixed_term",
    cohort_id: "cohort-new",
    visit_reason: null,
    planned_check_in_at: "2026-08-10T00:00:00+08:00",
    planned_check_out_at: "2026-08-31T00:00:00+08:00",
    notes: null,
  }))
})

it("routes reservation edit, check-in and cancellation to their exact APIs", async () => {
  const user = userEvent.setup()
  const future = detail().future_reservations[0]
  updateStayMock.mockResolvedValue(future)
  confirmCheckInMock.mockResolvedValue({ ...future, status: "checked_in", version: 4, actual_check_in_at: detail().as_of })
  cancelReservationMock.mockResolvedValue({ ...future, status: "cancelled", version: 4, cancellation_reason: "行程变化" })
  renderPage()
  await screen.findByRole("heading", { name: "房间 409" })

  await user.click(screen.getByRole("button", { name: "编辑预约 预约教练乙" }))
  let dialog = screen.getByRole("dialog", { name: "编辑预约 · 预约教练乙" })
  await user.click(within(dialog).getByRole("button", { name: "保存预约" }))
  await waitFor(() => expect(updateStayMock).toHaveBeenCalledWith("stay-future", expect.objectContaining({ version: 3 })))

  await user.click(screen.getByRole("button", { name: "确认入住 预约教练乙" }))
  dialog = screen.getByRole("dialog", { name: "确认入住 · 预约教练乙" })
  await user.click(within(dialog).getByRole("button", { name: "确认入住" }))
  await waitFor(() => expect(confirmCheckInMock).toHaveBeenCalledWith("stay-future", { version: 3, actual_check_in_at: "2026-08-06T00:00:00+08:00" }))

  await user.click(screen.getByRole("button", { name: "取消预约 预约教练乙" }))
  dialog = screen.getByRole("dialog", { name: "取消预约 · 预约教练乙" })
  await user.type(within(dialog).getByLabelText("取消原因"), "行程变化")
  await user.click(within(dialog).getByRole("button", { name: "确认取消预约" }))
  await waitFor(() => expect(cancelReservationMock).toHaveBeenCalledWith("stay-future", { version: 3, reason: "行程变化" }))
})

it("transfers a current stay to the selected target room", async () => {
  const user = userEvent.setup()
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(queryClient, "invalidateQueries")
  transferStayMock.mockResolvedValue(stay({ room_id: "room-410", room_name_snapshot: "410", version: 4 }))
  renderPage(queryClient)
  await screen.findByRole("heading", { name: "房间 409" })

  await user.click(screen.getByRole("button", { name: "调房 当前学员甲" }))
  const dialog = screen.getByRole("dialog", { name: "调房 · 当前学员甲" })
  await user.click(await within(dialog).findByRole("radio", { name: /一号楼.*四层.*410/ }))
  await user.type(within(dialog).getByLabelText("调房原因"), "设备调整")
  await user.click(within(dialog).getByRole("button", { name: "确认调房" }))

  await waitFor(() => expect(transferStayMock).toHaveBeenCalledWith("stay-current", {
    version: 3,
    target_room_id: "room-410",
    transferred_at: "2026-08-06T00:00:00+08:00",
    reason: "设备调整",
  }))
  expect(invalidate).toHaveBeenCalledWith({
    queryKey: housingKeys.detail(accountId, catalog.access_revision, "room-409"),
    exact: true,
  })
  expect(invalidate).toHaveBeenCalledWith({
    queryKey: housingKeys.detail(accountId, catalog.access_revision, "room-410"),
    exact: true,
  })
  expect(invalidate).toHaveBeenCalledWith({
    queryKey: housingKeys.overviews(accountId, catalog.access_revision),
  })
  expect(invalidate).toHaveBeenCalledWith({
    queryKey: [...housingKeys.scope(accountId, catalog.access_revision), "transfer-options"],
  })
})

it("ignores a delayed mutation settlement after the detail page unmounts", async () => {
  let resolveReport: ((value: ReturnType<typeof detail>["issues"][number]) => void) | undefined
  reportIssueMock.mockImplementation(() => new Promise((resolve) => { resolveReport = resolve }))
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(queryClient, "invalidateQueries")
  const user = userEvent.setup()
  const view = mountPage(queryClient)
  await screen.findByRole("heading", { name: "房间 409" })

  await user.click(screen.getByRole("button", { name: "上报问题" }))
  const dialog = screen.getByRole("dialog", { name: "上报问题 · 房间 409" })
  await user.type(within(dialog).getByLabelText("问题描述"), "延迟返回")
  await user.click(within(dialog).getByRole("button", { name: "确认上报" }))
  expect(reportIssueMock).toHaveBeenCalledTimes(1)

  view.unmount()
  await act(async () => {
    resolveReport?.(detail().issues[0])
    await Promise.resolve()
  })

  expect(invalidate).not.toHaveBeenCalled()
})

it("hides management actions solely from the latest detail allowed_actions", async () => {
  detailMock.mockResolvedValue(detail({
    room: room({
      allowed_actions: {
        edit: true,
        delete: true,
        verify: true,
        create_stay: false,
        report_issue: false,
        view_detail: true,
      },
    }),
  }))
  renderPage()

  expect(await screen.findByRole("heading", { name: "房间 409" })).toBeVisible()
  expect(screen.queryByRole("button", { name: "安排入住" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "上报问题" })).not.toBeInTheDocument()
})

it("never renders detail data whose access revision differs from the catalog", async () => {
  detailMock.mockResolvedValue(detail({ access_revision: "b".repeat(64) }))
  renderPage()

  expect(await screen.findByRole("alert")).toHaveTextContent("房态权限范围已更新")
  expect(screen.queryByText("当前学员甲")).not.toBeInTheDocument()
})

it("purges every account-scoped housing cache after a private detail failure", async () => {
  detailMock.mockRejectedValue(new ProblemDetailsError({
    type: "about:blank",
    title: "Not found",
    status: 404,
    detail: "资源不可见",
    code: "housing_resource_not_found",
    request_id: "housing-detail-private",
  }))
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  queryClient.setQueryData(housingKeys.cohortOptions(accountId, catalog.access_revision, "room-409"), {
    items: [{ full_name: "不得残留的候选姓名" }],
  })
  const otherAccountKey = housingKeys.detail("account-other", "revision-other", "room-other")
  queryClient.setQueryData(otherAccountKey, { occupants: ["其他账号住户"] })
  renderPage(queryClient)

  expect(await screen.findByRole("alert")).toHaveTextContent("房间详情不可访问")
  await waitFor(() => {
    const cachedHousing = queryClient.getQueriesData({ queryKey: housingKeys.root(accountId) })
    expect(cachedHousing.every(([, value]) => value === undefined)).toBe(true)
    expect(JSON.stringify(cachedHousing)).not.toContain("不得残留的候选姓名")
  })
  expect(queryClient.getQueryData(otherAccountKey)).toEqual({ occupants: ["其他账号住户"] })
})

it.each(["resident", "cohort", "transfer"] as const)(
  "purges the current account housing root after a private %s-options failure",
  async (source) => {
    const privateError = new ProblemDetailsError({
      type: "about:blank",
      title: "Not found",
      status: 404,
      detail: "资源不可见",
      code: "housing_resource_not_found",
      request_id: `housing-${source}-private`,
    })
    if (source === "resident") residentOptionsMock.mockRejectedValueOnce(privateError)
    if (source === "cohort") cohortOptionsMock.mockRejectedValueOnce(privateError)
    if (source === "transfer") transferOptionsMock.mockRejectedValueOnce(privateError)
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const otherAccountKey = housingKeys.detail("account-other", "revision-other", "room-other")
    queryClient.setQueryData(otherAccountKey, { occupants: ["其他账号住户"] })
    const user = userEvent.setup()
    renderPage(queryClient)
    await screen.findByRole("heading", { name: "房间 409" })

    if (source === "transfer") {
      await user.click(screen.getByRole("button", { name: "调房 当前学员甲" }))
    } else {
      await user.click(screen.getByRole("button", { name: "安排入住" }))
      if (source === "resident") {
        await user.type(screen.getByRole("textbox", { name: "搜索入住人员" }), "学员")
      }
    }

    expect(await screen.findByRole("alert")).toHaveTextContent("房间详情不可访问")
    await waitFor(() => {
      expect(queryClient.getQueriesData({ queryKey: housingKeys.root(accountId) }).every(([, value]) => value === undefined)).toBe(true)
    })
    expect(queryClient.getQueryData(otherAccountKey)).toEqual({ occupants: ["其他账号住户"] })
  },
)

it("returns to the housing overview without exposing a stale detail", async () => {
  const user = userEvent.setup()
  renderPage()
  await screen.findByRole("heading", { name: "房间 409" })

  await user.click(screen.getByRole("link", { name: "返回住宿房态" }))

  expect(screen.getByText("住宿房态")).toBeVisible()
})
