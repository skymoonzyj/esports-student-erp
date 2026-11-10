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
  completeRoomCleaning,
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
  updateHousingRoom,
  updateHousingPlannedDeparture,
  voidHousingStayHistory,
} from "./api"
import { HousingRoomDetailPage } from "./HousingRoomDetailPage"
import { RoomOccupancySlots } from "./RoomOccupancySlots"
import { housingKeys } from "./housingKeys"
import { personHousingKeys } from "../people/housingApi"
import { getRoomCards } from "./roomCardsApi"
import { getHousingPropertyCapabilities, submitHousingPropertyChange } from "./propertyApi"

vi.mock(import("./roomCardsApi"), async (importOriginal) => ({ ...await importOriginal(), getRoomCards: vi.fn().mockResolvedValue({ items: [] }) }))
vi.mock(import("./propertyApi"), async (importOriginal) => ({ ...await importOriginal(), getHousingPropertyCapabilities: vi.fn().mockResolvedValue({ can_submit: false, can_review: false, can_export: false, show_basic_export: false, can_create_request: false }), listHousingPropertyChanges: vi.fn().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 }), submitHousingPropertyChange: vi.fn() }))

const accountId = "account-housing-manager"
const authState = vi.hoisted(() => ({ accountId: "account-housing-manager", permissions: ["housing.read"], authenticated: true }))

vi.mock(import("../auth/AuthProvider"), () => ({
  useAuth: () => ({
    status: "authenticated" as const,
    user: authState.authenticated ? {
      account_id: authState.accountId,
      person_id: "person-housing-manager",
      full_name: "房态管理员",
      email: "housing-manager@example.test",
      force_password_change: false,
      permissions: authState.permissions,
    } : null,
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
  completeRoomCleaning: vi.fn(),
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
  updateHousingRoom: vi.fn(),
  updateHousingPlannedDeparture: vi.fn(),
  voidHousingStayHistory: vi.fn(),
}))

const cancelReservationMock = vi.mocked(cancelHousingReservation)
const confirmCheckInMock = vi.mocked(confirmHousingCheckIn)
const confirmCheckoutMock = vi.mocked(confirmHousingCheckout)
const cleaningMock = vi.mocked(completeRoomCleaning)
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
const updateDepartureMock = vi.mocked(updateHousingPlannedDeparture)
const voidHistoryMock = vi.mocked(voidHousingStayHistory)
const noStayActions = {
  edit_planned_departure: false,
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
    daily_cleaning_enabled: false, cleaning_status: "ready", cleaning_pending_at: null, cleaning_completed_at: null, photo_count: 0,
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
      manage_care: false, complete_cleaning: false, manage_photos: false,
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
    photos: [],
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

it("mounts current resident card dates independently of the occupancy controls", async () => {
  vi.mocked(getRoomCards).mockResolvedValueOnce({ items: [{ stay_id: "current", person_id: "p", person_name: "房卡住客", nickname: null, issued_on: "2026-11-01", expires_on: "2026-11-30", version: 1, can_edit: false }] })
  renderPage()
  expect(await screen.findByRole("region", { name: "住客房卡日期" })).toBeVisible()
  expect(await screen.findByText("当前开卡日：2026-11-01 · 到期日：2026-11-30")).toBeVisible()
  expect(screen.queryByRole("button", { name: "保存房卡住客的房卡日期" })).not.toBeInTheDocument()
})

it("mounts property proposals with the current room version and no direct occupancy authority", async () => {
  vi.mocked(getHousingPropertyCapabilities).mockResolvedValue({ can_submit: true, can_review: false, can_export: false, show_basic_export: false, can_create_request: true })
  detailMock.mockResolvedValue(detail({ room: room({ allowed_actions: { ...room().allowed_actions, create_stay: false, report_issue: false } }) }))
  renderPage()
  expect(await screen.findByRole("region", { name: "物业变更申请" })).toBeVisible()
  expect(screen.getByText(/为 409 提交申请（当前版本/)).toBeVisible()
  expect(screen.queryByRole("button", { name: "安排入住" })).not.toBeInTheDocument()
  expect(screen.queryByRole("region", { name: "物业变更审批" })).toBeNull()
  expect(screen.queryByRole("button", { name: "编辑房间" })).toBeNull()
})

it("directly saves an authorized administrative room edit with the existing dialog instead of a proposal", async () => {
  vi.mocked(getHousingPropertyCapabilities).mockResolvedValue({ can_submit: true, can_review: true, can_export: true, show_basic_export: false, can_create_request: false })
  const initial = room({ allowed_actions: { ...room().allowed_actions, edit: true } })
  detailMock.mockResolvedValue(detail({ room: initial }))
  vi.mocked(updateHousingRoom).mockResolvedValue({ ...initial, notes: "行政直接维护", service_status: "maintenance", version: 9 })
  renderPage()
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "编辑房间" }))
  const dialog = screen.getByRole("dialog", { name: "编辑房间 409" })
  await user.clear(within(dialog).getByLabelText("备注"))
  await user.type(within(dialog).getByLabelText("备注"), "行政直接维护")
  await user.selectOptions(within(dialog).getByLabelText("服务状态"), "maintenance")
  detailMock.mockResolvedValue(detail({ room: { ...initial, notes: "行政直接维护", service_status: "maintenance", status_text: "维修中", version: 9 } }))
  await user.click(within(dialog).getByRole("button", { name: "保存房间" }))
  await waitFor(() => expect(updateHousingRoom).toHaveBeenCalledWith("room-409", expect.objectContaining({ version: 8, notes: "行政直接维护", service_status: "maintenance" })))
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
  expect(await screen.findByText("维修中")).toBeVisible()
  expect(screen.queryByRole("button", { name: "提交待审申请" })).toBeNull()
  expect(submitHousingPropertyChange).not.toHaveBeenCalled()
})

it("does not offer either a direct edit or a new request after administrative edit denial", async () => {
  vi.mocked(getHousingPropertyCapabilities).mockResolvedValue({ can_submit: true, can_review: true, can_export: true, show_basic_export: true, can_create_request: false })
  const client = renderPage()
  await screen.findByRole("heading", { name: "房间 409" })
  await waitFor(() => expect(client.isFetching()).toBe(0))
  expect(screen.queryByRole("button", { name: "编辑房间" })).toBeNull()
  expect(screen.queryByRole("button", { name: "提交待审申请" })).toBeNull()
})

it.each([
  ["team_manager", "战队经理"],
  ["assistant_coach", "助教"],
] as const)("labels %s in room detail", async (personType, label) => {
  detailMock.mockResolvedValue(detail({ current_stays: [stay({ person_type: personType })] }))
  renderPage()
  expect((await screen.findAllByText(`${label} · 男`)).length).toBeGreaterThan(0)
})

it.each([
  ["team_manager", "战队经理"],
  ["assistant_coach", "助教"],
] as const)("labels %s in occupancy slots", (personType, label) => {
  render(<RoomOccupancySlots roomType="single" capacity={1} occupants={[stay({ person_type: personType })]} />)
  expect(screen.getByText(`${label} · 男`)).toBeVisible()
})

it("labels unknown sex in stay details without inferring female", async () => {
  detailMock.mockResolvedValue(detail({ current_stays: [stay({ sex: null })] }))
  renderPage()
  expect((await screen.findAllByText("学员 · 性别待补")).length).toBeGreaterThan(0)
  expect(screen.queryByText("学员 · 女")).not.toBeInTheDocument()
})

it("labels an initial ready room neutrally without implying a recorded cleaning event", async () => {
  detailMock.mockResolvedValue(detail({ room: room({ cleaning_status: "ready", cleaning_pending_at: null, cleaning_completed_at: null }) }))
  renderPage()
  const label = await screen.findByText("保洁状态")
  expect(label.parentElement).toHaveTextContent("无退房待保洁")
  expect(screen.queryByText("已清洁")).not.toBeInTheDocument()
})

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

  // Child room-card queries now have their own cancellation lifecycle too.
  // Assert the privacy roots, not a count of unrelated child cancellations.
  expect(cancelQueries).toHaveBeenCalledWith({ queryKey: housingKeys.details(accountId, catalog.access_revision) })
  expect(cancelQueries).toHaveBeenCalledWith({ queryKey: [...housingKeys.scope(accountId, catalog.access_revision), "cohort-options"] })
  expect(cancelQueries).toHaveBeenCalledWith({ queryKey: [...housingKeys.scope(accountId, catalog.access_revision), "transfer-options"] })
  expect(queryClient.getQueryData(housingKeys.detail(accountId, catalog.access_revision, "room-409"))).toBeUndefined()
  expect(queryClient.getQueryData(housingKeys.cohortOptions(accountId, catalog.access_revision, "room-409"))).toBeUndefined()
  expect(queryClient.getQueryData(housingKeys.transferOptions(accountId, catalog.access_revision, "stay-current"))).toBeUndefined()
})

beforeEach(() => {
  vi.mocked(updateHousingRoom).mockReset()
  vi.mocked(submitHousingPropertyChange).mockReset()
  vi.mocked(getHousingPropertyCapabilities).mockResolvedValue({ can_submit: false, can_review: false, can_export: false, show_basic_export: false })
  cleaningMock.mockReset()
  authState.accountId = accountId
  authState.permissions = ["housing.read"]
  authState.authenticated = true
  voidHistoryMock.mockReset()
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

function cleaningDetail(): HousingRoomDetail {
  const original = detail()
  return { ...original, room: room({ cleaning_status: "pending", is_assignable: false,
    allowed_actions: { ...original.room.allowed_actions, create_stay: false, complete_cleaning: true, manage_care: true, manage_photos: true } }) }
}

it("keeps cleaning failure visible and releases the shared mutation lock for retry", async () => {
  detailMock.mockResolvedValue(cleaningDetail())
  cleaningMock.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(room({ cleaning_status: "ready" }))
  renderPage()
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "完成保洁" }))
  const dialog = screen.getByRole("dialog", { name: "完成保洁 409" })
  await user.click(within(dialog).getByRole("button", { name: "确认完成保洁" }))
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("操作暂时失败")
  await user.click(within(dialog).getByRole("button", { name: "确认完成保洁" }))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(cleaningMock).toHaveBeenNthCalledWith(1, "room-409", { version: 8 })
  expect(cleaningMock).toHaveBeenCalledTimes(2)
})

it("refreshes cleaning conflicts without retrying and keeps the opening version", async () => {
  detailMock.mockResolvedValue(cleaningDetail())
  cleaningMock.mockRejectedValue(new ProblemDetailsError({ status: 409, code: "housing_stale_version", detail: "stale", title: "Conflict", type: "about:blank", request_id: "cleaning-test" }))
  renderPage()
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "完成保洁" }))
  detailMock.mockResolvedValue({ ...cleaningDetail(), room: { ...cleaningDetail().room, version: 9 } })
  await user.click(screen.getByRole("button", { name: "确认完成保洁" }))
  expect(await screen.findByText("数据已更新，请查看当前数据后再重试。")).toBeVisible()
  expect(cleaningMock).toHaveBeenCalledTimes(1)
  expect(detailMock.mock.calls.length).toBeGreaterThan(1)
})

it("blocks repeated cleaning clicks across the coordinator and invalidates affected caches", async () => {
  detailMock.mockResolvedValue(cleaningDetail())
  let finish!: (value: HousingRoom) => void
  cleaningMock.mockImplementation(() => new Promise((resolve) => { finish = resolve }))
  const client = renderPage()
  const invalidation = vi.spyOn(client, "invalidateQueries")
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "完成保洁" }))
  await user.dblClick(screen.getByRole("button", { name: "确认完成保洁" }))
  expect(cleaningMock).toHaveBeenCalledTimes(1)
  expect(screen.getByRole("button", { name: "上报问题" })).toBeDisabled()
  await act(async () => finish(room({ cleaning_status: "ready" })))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(invalidation).toHaveBeenCalledWith({ queryKey: personHousingKeys.root(accountId) })
  expect(invalidation).toHaveBeenCalledWith({ queryKey: [...housingKeys.scope(accountId, catalog.access_revision), "transfer-options"] })
})

it("does not expose cleaning completion from catalog edit permission alone", async () => {
  const original = detail()
  detailMock.mockResolvedValue({ ...original, room: room({ cleaning_status: "pending", allowed_actions: { ...original.room.allowed_actions, edit: true, complete_cleaning: false } }) })
  renderPage()
  await screen.findByRole("heading", { name: "房间 409" })
  expect(screen.queryByRole("button", { name: "完成保洁" })).not.toBeInTheDocument()
})

it("discards a late cleaning result after a permission revision changes", async () => {
  detailMock.mockResolvedValue(cleaningDetail())
  let finish!: (value: HousingRoom) => void
  cleaningMock.mockImplementation(() => new Promise((resolve) => { finish = resolve }))
  const client = renderPage()
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "完成保洁" }))
  await user.click(screen.getByRole("button", { name: "确认完成保洁" }))
  const revision = "b".repeat(64)
  catalogMock.mockResolvedValue({ ...catalog, access_revision: revision })
  detailMock.mockResolvedValue(detail({ access_revision: revision }))
  await act(async () => { await client.invalidateQueries({ queryKey: housingKeys.catalog(accountId) }) })
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  const invalidate = vi.spyOn(client, "invalidateQueries")
  await act(async () => finish(room({ cleaning_status: "ready" })))
  expect(invalidate).not.toHaveBeenCalled()
  expect(client.getQueryCache().findAll({ queryKey: housingKeys.scope(accountId, catalog.access_revision) })).toHaveLength(0)
})

function voidableDetail() {
  return detail({ can_view_voided_history: true, voided_history: [], history: [stay({
    id: "stay-history", person_name: "历史老师", status: "checked_out",
    actual_check_out_at: "2026-08-05T18:00:00+08:00", allowed_actions: { ...noStayActions, void_history: true },
  })] })
}

async function confirmHistoryVoid(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "作废历史住宿 历史老师" }))
  await user.type(screen.getByLabelText("作废原因"), "历史重复登记")
  await user.click(screen.getByRole("button", { name: "下一步：核对历史住宿" }))
  await user.click(screen.getByRole("checkbox", { name: /我已核对/ }))
  await user.click(screen.getByRole("button", { name: "确认作废历史住宿" }))
}

it("uses server capability and explicit includeVoided variants without cross-reading", async () => {
  const user = userEvent.setup()
  const regular = voidableDetail()
  const tombstone = stay({ id: "stay-voided", person_name: "已作废老师", allowed_actions: noStayActions,
    void_info: { voided_at: "2026-08-06T01:00:00Z", voided_by_user_id: "admin", reason: "重复登记" } })
  detailMock.mockImplementation(async (_id, _signal, includeVoided) => includeVoided ? { ...regular, voided_history: [tombstone] } : regular)
  const queryClient = renderPage()
  await screen.findByRole("heading", { name: "房间 409" })
  expect(screen.queryByText("已作废老师")).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "作废历史住宿 历史老师" })).toBeEnabled()
  await user.click(screen.getByRole("checkbox", { name: "查看已作废历史" }))
  expect(await screen.findByText("已作废老师")).toBeVisible()
  expect(screen.getByText(/重复登记/)).toBeVisible()
  expect(queryClient.getQueryData(housingKeys.detail(accountId, catalog.access_revision, "room-409", false))).toEqual(regular)
  expect(queryClient.getQueryData(housingKeys.detail(accountId, catalog.access_revision, "room-409", true))).toEqual({ ...regular, voided_history: [tombstone] })
  await user.click(screen.getByRole("checkbox", { name: "查看已作废历史" }))
  expect(screen.queryByText("已作废老师")).not.toBeInTheDocument()
  detailMock.mockResolvedValue({ ...regular, can_view_voided_history: false, history: [stay({ allowed_actions: noStayActions })] })
  await act(async () => { await queryClient.invalidateQueries({ queryKey: housingKeys.details(accountId, catalog.access_revision) }) })
  await waitFor(() => expect(screen.queryByRole("checkbox", { name: "查看已作废历史" })).not.toBeInTheDocument())
  expect(screen.queryByRole("button", { name: /作废历史住宿/ })).not.toBeInTheDocument()
  await waitFor(() => expect(queryClient.getQueryData(housingKeys.detail(accountId, catalog.access_revision, "room-409", true))).toBeUndefined())
})

it("retains operation key after timeout, deduplicates double click, and reads before a new reason", async () => {
  const user = userEvent.setup()
  const original = voidableDetail()
  detailMock.mockResolvedValue(original)
  let rejectMutation!: (error: Error) => void
  voidHistoryMock.mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectMutation = reject }))
  const queryClient = renderPage()
  await confirmHistoryVoid(user)
  expect(voidHistoryMock).toHaveBeenCalledTimes(1)
  await user.dblClick(screen.getByRole("button", { name: "确认作废历史住宿" }))
  expect(voidHistoryMock).toHaveBeenCalledTimes(1)
  await act(async () => rejectMutation(new Error("timeout")))
  const first = voidHistoryMock.mock.calls[0][1]
  voidHistoryMock.mockRejectedValueOnce(new Error("timeout"))
  await user.click(screen.getByRole("button", { name: "确认作废历史住宿" }))
  await waitFor(() => expect(voidHistoryMock).toHaveBeenCalledTimes(2))
  expect(voidHistoryMock.mock.calls[1][1].idempotency_key).toBe(first.idempotency_key)
  detailMock.mockRejectedValueOnce(new Error("still offline"))
  await user.click(screen.getByRole("button", { name: "关闭并重新查询目标" }))
  expect(await screen.findByRole("dialog")).toBeVisible()
  await waitFor(() => expect(screen.getByRole("button", { name: "关闭并重新查询目标" })).toBeEnabled())
  expect(screen.queryByRole("textbox", { name: "作废原因" })).not.toBeInTheDocument()
  detailMock.mockResolvedValue(original)
  await user.click(screen.getByRole("button", { name: "关闭并重新查询目标" }))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  await confirmHistoryVoid(user)
  expect(voidHistoryMock.mock.calls[2][1].idempotency_key).not.toBe(first.idempotency_key)
  expect(queryClient.getQueryData(housingKeys.detail(accountId, catalog.access_revision, "room-409"))).toEqual(original)
})

it("reads back success and refreshes both variants, cached invitation housing and personnel without changing generated reports", async () => {
  const user = userEvent.setup()
  const original = voidableDetail()
  detailMock.mockResolvedValue(original)
  voidHistoryMock.mockResolvedValue({ operation_id: "op", stay_id: "stay-history", room_id: "room-409", person_id: "person-current", version: 4, voided_at: "2026-08-06T01:00:00Z" })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invitationKey = ["portal", accountId, "invitations", "housing", "cached"]
  const reportKey = ["housing-report", "generated"]
  queryClient.setQueryData(invitationKey, { items: ["cached-invitation"] })
  queryClient.setQueryData(reportKey, { status: "generated" })
  queryClient.setQueryData(housingKeys.detail(accountId, catalog.access_revision, "room-409", true), original)
  const linkedRoomKey = housingKeys.detail(accountId, catalog.access_revision, "room-410", false)
  queryClient.setQueryData(linkedRoomKey, detail({ room: room({ id: "room-410" }), current_stays: [], future_reservations: [], history: [] }))
  const invalidate = vi.spyOn(queryClient, "invalidateQueries")
  renderPage(queryClient)
  await screen.findByRole("heading", { name: "房间 409" })
  detailMock.mockResolvedValue({ ...original, history: [], transfers: original.transfers.map(t => ({ ...t, from_stay_voided: true, to_stay_voided: true })) })
  await confirmHistoryVoid(user)
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(screen.queryByText("历史老师")).not.toBeInTheDocument()
  expect(screen.getByText("来源历史已作废")).toBeVisible()
  expect(screen.getByText("去向历史已作废")).toBeVisible()
  expect(queryClient.getQueryState(invitationKey)?.isInvalidated).toBe(true)
  expect(queryClient.getQueryState(housingKeys.detail(accountId, catalog.access_revision, "room-409", true))?.isInvalidated).toBe(true)
  expect(queryClient.getQueryState(linkedRoomKey)?.isInvalidated).toBe(true)
  expect(detailMock.mock.calls.every(([roomId]) => roomId === "room-409")).toBe(true)
  expect(invalidate).toHaveBeenCalledWith({ queryKey: personHousingKeys.root(accountId) })
  expect(queryClient.getQueryData(reportKey)).toEqual({ status: "generated" })
})

it("revocationDropsSensitiveLateResponse", async () => {
  const user = userEvent.setup()
  const original = voidableDetail()
  detailMock.mockResolvedValue(original)
  let resolveMutation!: (value: Awaited<ReturnType<typeof voidHousingStayHistory>>) => void
  voidHistoryMock.mockImplementationOnce(() => new Promise(resolve => { resolveMutation = resolve }))
  const queryClient = renderPage()
  await confirmHistoryVoid(user)
  queryClient.setQueryData(housingKeys.detail(accountId, catalog.access_revision, "room-409", true), { ...original, voided_history: [stay({ person_name: "撤权前敏感人员" })] })
  catalogMock.mockResolvedValue({ ...catalog, access_revision: "b".repeat(64) })
  detailMock.mockResolvedValue(detail({ access_revision: "b".repeat(64), can_view_voided_history: false, history: [] }))
  await act(async () => { await queryClient.invalidateQueries({ queryKey: housingKeys.catalog(accountId) }) })
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(document.body.textContent).not.toContain("历史重复登记")
  await act(async () => resolveMutation({ operation_id: "late", stay_id: "stay-history", room_id: "room-409", person_id: "person-current", version: 4, voided_at: "2026-08-06T01:00:00Z" }))
  expect(queryClient.getQueriesData({ queryKey: housingKeys.scope(accountId, catalog.access_revision) })).toEqual([])
  expect(screen.queryByRole("button", { name: /作废历史住宿/ })).not.toBeInTheDocument()
  expect(screen.queryByRole("checkbox", { name: "查看已作废历史" })).not.toBeInTheDocument()
})

it.each(["logout", "switch-account", "permissions"])("drops a pending void dialog and late mutation on %s", async (change) => {
  const user = userEvent.setup()
  detailMock.mockResolvedValue(voidableDetail())
  let resolveMutation!: (value: Awaited<ReturnType<typeof voidHousingStayHistory>>) => void
  voidHistoryMock.mockImplementationOnce(() => new Promise(resolve => { resolveMutation = resolve }))
  const view = mountPage()
  await confirmHistoryVoid(user)
  const privateKey = housingKeys.detail(accountId, catalog.access_revision, "room-409", true)
  view.queryClient.setQueryData(privateKey, { ...voidableDetail(), voided_history: [stay({ person_name: "旧账号作废人员" })] })
  if (change === "logout") authState.authenticated = false
  else if (change === "switch-account") authState.accountId = "other-account"
  else authState.permissions = []
  // The real auth boundary rerenders its consumers; preserve the mounted page to exercise its own cleanup.
  view.rerender(<QueryClientProvider client={view.queryClient}><MemoryRouter initialEntries={["/housing/rooms/room-409"]}><Routes><Route path="/housing/rooms/:roomId" element={<HousingRoomDetailPage />} /></Routes></MemoryRouter></QueryClientProvider>)
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(document.body.textContent).not.toContain("历史重复登记")
  await act(async () => resolveMutation({ operation_id: "late-auth", stay_id: "stay-history", room_id: "room-409", person_id: "person-current", version: 4, voided_at: "2026-08-06T01:00:00Z" }))
  expect(view.queryClient.getQueryData(privateKey)).toBeUndefined()
  expect(screen.queryByRole("button", { name: /作废历史住宿/ })).not.toBeInTheDocument()
})

it("does not repopulate tombstone cache from an aborted late detail response after revocation", async () => {
  const user = userEvent.setup()
  let resolveSensitive!: (value: HousingRoomDetail) => void
  detailMock.mockImplementation(async (_id, _signal, includeVoided) => includeVoided
    ? new Promise(resolve => { resolveSensitive = resolve }) : voidableDetail())
  const queryClient = renderPage()
  await user.click(await screen.findByRole("checkbox", { name: "查看已作废历史" }))
  await waitFor(() => expect(resolveSensitive).toBeTypeOf("function"))
  catalogMock.mockResolvedValue({ ...catalog, access_revision: "c".repeat(64) })
  detailMock.mockResolvedValue(detail({ access_revision: "c".repeat(64), can_view_voided_history: false, history: [] }))
  await act(async () => { await queryClient.invalidateQueries({ queryKey: housingKeys.catalog(accountId) }) })
  await screen.findByRole("heading", { name: "房间 409" })
  await act(async () => resolveSensitive({ ...voidableDetail(), voided_history: [stay({ person_name: "禁止迟到泄漏", void_info: { voided_at: "2026-08-06T01:00:00Z", voided_by_user_id: "admin", reason: "绝不可恢复的作废原因" } })] }))
  expect(queryClient.getQueriesData({ queryKey: housingKeys.scope(accountId, catalog.access_revision) })).toEqual([])
  expect(document.body.textContent).not.toContain("禁止迟到泄漏")
  expect(document.body.textContent).not.toContain("绝不可恢复的作废原因")
})

it("never restores a voided-history entry point from the other cached variant after a capability denial", async () => {
  const user = userEvent.setup()
  detailMock.mockResolvedValue(voidableDetail())
  const queryClient = renderPage()
  await user.click(await screen.findByRole("checkbox", { name: "查看已作废历史" }))
  await screen.findByRole("heading", { name: "已作废住宿历史" })
  detailMock.mockImplementation((_id, _signal, includeVoided) => includeVoided
    ? Promise.resolve({ ...voidableDetail(), can_view_voided_history: false, history: [] })
    : new Promise(() => {}))
  await act(async () => { await queryClient.invalidateQueries({ queryKey: housingKeys.detail(accountId, catalog.access_revision, "room-409", true) }) })
  await waitFor(() => expect(screen.queryByRole("checkbox", { name: "查看已作废历史" })).not.toBeInTheDocument())
  expect(screen.queryByRole("button", { name: /作废历史住宿/ })).not.toBeInTheDocument()
  expect(queryClient.getQueryData(housingKeys.detail(accountId, catalog.access_revision, "room-409", false))).toBeUndefined()
})

it("reads back an already-voided conflict rather than replaying or fabricating history", async () => {
  const user = userEvent.setup()
  const original = voidableDetail()
  detailMock.mockResolvedValue(original)
  voidHistoryMock.mockRejectedValue(new ProblemDetailsError({ type: "about:blank", title: "Conflict", status: 409,
    code: "housing_stay_already_voided", detail: "该历史已作废", request_id: "already-voided" }))
  renderPage()
  await screen.findByRole("heading", { name: "房间 409" })
  detailMock.mockResolvedValue({ ...original, history: [] })
  await confirmHistoryVoid(user)
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(screen.queryByText("历史老师")).not.toBeInTheDocument()
  expect(voidHistoryMock).toHaveBeenCalledTimes(1)
  expect(detailMock.mock.calls.length).toBeGreaterThan(1)
})

it("keeps the same confirmation after a conflict readback fails without an unhandled rejection", async () => {
  const user = userEvent.setup()
  detailMock.mockResolvedValue(voidableDetail())
  voidHistoryMock.mockRejectedValue(new ProblemDetailsError({ type: "about:blank", title: "Conflict", status: 409,
    code: "housing_stay_already_voided", detail: "该历史已作废", request_id: "offline-conflict" }))
  renderPage()
  await screen.findByRole("heading", { name: "房间 409" })
  detailMock.mockRejectedValue(new Error("offline-readback"))
  await confirmHistoryVoid(user)
  await waitFor(() => expect(screen.getByRole("button", { name: "确认作废历史住宿" })).toBeEnabled())
  expect(screen.getByRole("dialog")).toBeVisible()
  expect(screen.queryByRole("textbox", { name: "作废原因" })).not.toBeInTheDocument()
  expect(voidHistoryMock).toHaveBeenCalledTimes(1)
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

it("refreshes a stale planned-departure edit without replay and retries only after reopening", async () => {
  const user = userEvent.setup()
  const current = stay({ stay_purpose: "permanent", cohort_id: null, allowed_actions: { ...noStayActions, edit_planned_departure: true } })
  detailMock.mockResolvedValue(detail({ current_stays: [current] }))
  updateDepartureMock.mockReset().mockRejectedValueOnce(new ProblemDetailsError({
    type: "about:blank", title: "Conflict", status: 409, detail: "版本冲突", code: "housing_stale_version", request_id: "departure-stale",
  })).mockResolvedValue({ ...current, version: 5 })
  renderPage()
  await screen.findByRole("heading", { name: "房间 409" })
  await user.click(screen.getByRole("button", { name: /修改计划离开/ }))
  let dialog = screen.getByRole("dialog", { name: /修改计划离开/ })
  await user.selectOptions(within(dialog).getByLabelText("住宿期限"), "long_term")
  detailMock.mockResolvedValue(detail({ current_stays: [{ ...current, version: 5 }] }))
  await user.click(within(dialog).getByRole("button", { name: "保存计划离开" }))
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("数据已更新")
  expect(within(dialog).getByLabelText("住宿期限")).toHaveValue("long_term")
  expect(updateDepartureMock).toHaveBeenCalledTimes(1)
  await user.click(within(dialog).getByRole("button", { name: "取消" }))
  await user.click(screen.getByRole("button", { name: /修改计划离开/ }))
  dialog = screen.getByRole("dialog", { name: /修改计划离开/ })
  await user.selectOptions(within(dialog).getByLabelText("住宿期限"), "long_term")
  await user.click(within(dialog).getByRole("button", { name: "保存计划离开" }))
  await waitFor(() => expect(updateDepartureMock).toHaveBeenLastCalledWith("stay-current", { version: 5, duration_mode: "long_term", planned_check_out_at: null }))
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
  queryClient.setQueryData(personHousingKeys.summary(accountId, "person-current"), { room: "409" })
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
    queryKey: [...housingKeys.details(accountId, catalog.access_revision), "room-409"],
  }, { throwOnError: undefined })
  expect(invalidate).toHaveBeenCalledWith({
    queryKey: [...housingKeys.details(accountId, catalog.access_revision), "room-410"],
  }, { throwOnError: undefined })
  expect(invalidate).toHaveBeenCalledWith({
    queryKey: housingKeys.overviews(accountId, catalog.access_revision),
  })
  expect(invalidate).toHaveBeenCalledWith({
    queryKey: [...housingKeys.scope(accountId, catalog.access_revision), "transfer-options"],
  })
  expect(queryClient.getQueryState(personHousingKeys.summary(accountId, "person-current"))?.isInvalidated).toBe(true)
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
        manage_care: false, complete_cleaning: false, manage_photos: false,
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
